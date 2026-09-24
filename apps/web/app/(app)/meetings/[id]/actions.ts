"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { z } from "zod";
import { createClient } from "@/lib/supabase/server";
import { STORAGE_BUCKET } from "@/lib/constants";

async function requireUser() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) throw new Error("Not authenticated");
  return { supabase, user };
}

const uuid = z.string().uuid();

function refresh(meetingId: string) {
  revalidatePath(`/meetings/${meetingId}`);
  revalidatePath("/meetings");
}

// --- Meeting-level actions --------------------------------------------------

const meetingDetailsSchema = z.object({
  title: z.string().trim().min(1).max(300),
  description: z.string().trim().max(5000).nullable(),
  meeting_date: z.string().datetime({ offset: true }).or(z.string().date()),
});

export async function updateMeetingDetails(
  meetingId: string,
  input: { title: string; description: string | null; meeting_date: string },
) {
  uuid.parse(meetingId);
  const data = meetingDetailsSchema.parse(input);
  const { supabase } = await requireUser();

  const { error } = await supabase
    .from("meetings")
    .update({
      title: data.title,
      description: data.description,
      meeting_date: new Date(data.meeting_date).toISOString(),
    })
    .eq("id", meetingId);
  if (error) throw new Error(error.message);
  refresh(meetingId);
}

export async function requestMeetingRetry(meetingId: string) {
  uuid.parse(meetingId);
  const { supabase } = await requireUser();
  const { error } = await supabase.rpc("request_meeting_retry", {
    p_meeting_id: meetingId,
  });
  if (error) throw new Error(error.message);
  refresh(meetingId);
}

export async function deleteMeeting(meetingId: string) {
  uuid.parse(meetingId);
  const { supabase, user } = await requireUser();

  // Best-effort removal of stored recording objects before the DB cascade.
  const prefix = `${user.id}/${meetingId}/`;
  const { data: objects } = await supabase.storage
    .from(STORAGE_BUCKET)
    .list(prefix, { limit: 100 });
  if (objects && objects.length > 0) {
    const { error: rmErr } = await supabase.storage
      .from(STORAGE_BUCKET)
      .remove(objects.map((o) => `${prefix}${o.name}`));
    if (rmErr) throw new Error(`Could not remove recording: ${rmErr.message}`);
  }

  const { error } = await supabase.rpc("delete_meeting", {
    p_meeting_id: meetingId,
  });
  if (error) throw new Error(error.message);

  revalidatePath("/meetings");
  redirect("/meetings");
}

// --- Participants and speaker mapping ---------------------------------------

export async function addParticipant(meetingId: string, displayName: string) {
  uuid.parse(meetingId);
  const name = z.string().trim().min(1).max(200).parse(displayName);
  const { supabase, user } = await requireUser();
  const { error } = await supabase
    .from("participants")
    .insert({ meeting_id: meetingId, owner_id: user.id, display_name: name });
  if (error) throw new Error(error.message);
  refresh(meetingId);
}

export async function renameParticipant(
  participantId: string,
  meetingId: string,
  displayName: string,
) {
  uuid.parse(participantId);
  uuid.parse(meetingId);
  const name = z.string().trim().min(1).max(200).parse(displayName);
  const { supabase } = await requireUser();
  const { error } = await supabase
    .from("participants")
    .update({ display_name: name })
    .eq("id", participantId)
    .eq("meeting_id", meetingId);
  if (error) throw new Error(error.message);
  refresh(meetingId);
}

export async function removeParticipant(
  participantId: string,
  meetingId: string,
) {
  uuid.parse(participantId);
  uuid.parse(meetingId);
  const { supabase } = await requireUser();
  const { error } = await supabase
    .from("participants")
    .delete()
    .eq("id", participantId)
    .eq("meeting_id", meetingId);
  if (error) throw new Error(error.message);
  refresh(meetingId);
}

/** Assign a diarized speaker label to a named participant. */
export async function assignSpeaker(
  meetingId: string,
  speakerLabel: string,
  displayName: string,
) {
  uuid.parse(meetingId);
  const name = z.string().trim().min(1).max(200).parse(displayName);
  const { supabase, user } = await requireUser();

  // Find or create the participant mapped to this raw speaker label.
  const { data: existing } = await supabase
    .from("participants")
    .select("id")
    .eq("meeting_id", meetingId)
    .eq("speaker_label", speakerLabel)
    .maybeSingle();

  let participantId: string;
  if (existing) {
    const { error } = await supabase
      .from("participants")
      .update({ display_name: name })
      .eq("id", existing.id);
    if (error) throw new Error(error.message);
    participantId = existing.id;
  } else {
    const { data: created, error } = await supabase
      .from("participants")
      .insert({
        meeting_id: meetingId,
        owner_id: user.id,
        display_name: name,
        speaker_label: speakerLabel,
      })
      .select("id")
      .single();
    if (error) throw new Error(error.message);
    participantId = created.id;
  }

  const { error: segErr } = await supabase
    .from("transcript_segments")
    .update({ participant_id: participantId })
    .eq("meeting_id", meetingId)
    .eq("speaker_label", speakerLabel);
  if (segErr) throw new Error(segErr.message);

  refresh(meetingId);
}

/** Reassign a single segment to a different speaker. */
export async function reassignSegmentSpeaker(
  segmentId: string,
  meetingId: string,
  participantId: string | null,
) {
  uuid.parse(segmentId);
  uuid.parse(meetingId);
  if (participantId) uuid.parse(participantId);
  const { supabase } = await requireUser();
  const { error } = await supabase
    .from("transcript_segments")
    .update({ participant_id: participantId })
    .eq("id", segmentId)
    .eq("meeting_id", meetingId);
  if (error) throw new Error(error.message);
  refresh(meetingId);
}

// --- Transcript correction ----------------------------------------------------

export async function updateSegmentText(
  segmentId: string,
  meetingId: string,
  userText: string,
) {
  uuid.parse(segmentId);
  uuid.parse(meetingId);
  const text = z.string().max(20000).parse(userText);
  const { supabase } = await requireUser();
  const { error } = await supabase
    .from("transcript_segments")
    .update({
      user_text: text.trim() ? text.trim() : null,
      edited_at: new Date().toISOString(),
    })
    .eq("id", segmentId)
    .eq("meeting_id", meetingId);
  if (error) throw new Error(error.message);
  refresh(meetingId);
}

// --- Intelligence edits -------------------------------------------------------

export async function updateSummary(
  summaryId: string,
  meetingId: string,
  summary: string,
) {
  uuid.parse(summaryId);
  uuid.parse(meetingId);
  const text = z.string().trim().min(1).max(20000).parse(summary);
  const { supabase } = await requireUser();
  const { error } = await supabase
    .from("summaries")
    .update({ summary: text, is_edited: true })
    .eq("id", summaryId)
    .eq("meeting_id", meetingId);
  if (error) throw new Error(error.message);
  refresh(meetingId);
}

export async function updateActionItemStatus(
  actionItemId: string,
  meetingId: string,
  status: "open" | "in_progress" | "completed" | "cancelled",
) {
  uuid.parse(actionItemId);
  uuid.parse(meetingId);
  z.enum(["open", "in_progress", "completed", "cancelled"]).parse(status);
  const { supabase } = await requireUser();
  const { error } = await supabase
    .from("action_items")
    .update({ status })
    .eq("id", actionItemId)
    .eq("meeting_id", meetingId);
  if (error) throw new Error(error.message);
  refresh(meetingId);
}

const actionItemEditSchema = z.object({
  description: z.string().trim().min(1).max(2000),
  due_date: z.string().date().nullable(),
  owner_participant_id: z.string().uuid().nullable(),
});

export async function updateActionItem(
  actionItemId: string,
  meetingId: string,
  input: {
    description: string;
    due_date: string | null;
    owner_participant_id: string | null;
  },
) {
  uuid.parse(actionItemId);
  uuid.parse(meetingId);
  const data = actionItemEditSchema.parse(input);
  const { supabase } = await requireUser();
  const { error } = await supabase
    .from("action_items")
    .update({ ...data, is_edited: true })
    .eq("id", actionItemId)
    .eq("meeting_id", meetingId);
  if (error) throw new Error(error.message);
  refresh(meetingId);
}

export async function updateDecision(
  decisionId: string,
  meetingId: string,
  description: string,
) {
  uuid.parse(decisionId);
  uuid.parse(meetingId);
  const text = z.string().trim().min(1).max(2000).parse(description);
  const { supabase } = await requireUser();
  const { error } = await supabase
    .from("decisions")
    .update({ description: text, is_edited: true })
    .eq("id", decisionId)
    .eq("meeting_id", meetingId);
  if (error) throw new Error(error.message);
  refresh(meetingId);
}

export async function updateKeyPoint(
  keyPointId: string,
  meetingId: string,
  content: string,
) {
  uuid.parse(keyPointId);
  uuid.parse(meetingId);
  const text = z.string().trim().min(1).max(2000).parse(content);
  const { supabase } = await requireUser();
  const { error } = await supabase
    .from("key_points")
    .update({ content: text, is_edited: true })
    .eq("id", keyPointId)
    .eq("meeting_id", meetingId);
  if (error) throw new Error(error.message);
  refresh(meetingId);
}

export async function updateQuestion(
  questionId: string,
  meetingId: string,
  input: { question?: string; resolved?: boolean },
) {
  uuid.parse(questionId);
  uuid.parse(meetingId);
  const data = z
    .object({
      question: z.string().trim().min(1).max(2000).optional(),
      resolved: z.boolean().optional(),
    })
    .parse(input);
  const { supabase } = await requireUser();
  const { error } = await supabase
    .from("questions")
    .update({ ...data, is_edited: data.question !== undefined ? true : undefined })
    .eq("id", questionId)
    .eq("meeting_id", meetingId);
  if (error) throw new Error(error.message);
  refresh(meetingId);
}
