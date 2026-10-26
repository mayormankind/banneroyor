"use server";

import { revalidatePath } from "next/cache";
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

export async function createSpeakerProfile(displayName: string) {
  const name = z.string().trim().min(1).max(200).parse(displayName);
  const { supabase, user } = await requireUser();
  const { error } = await supabase
    .from("speaker_profiles")
    .insert({ owner_id: user.id, display_name: name });
  if (error) throw new Error(error.message);
  revalidatePath("/speakers");
}

export async function renameSpeakerProfile(
  profileId: string,
  displayName: string,
) {
  uuid.parse(profileId);
  const name = z.string().trim().min(1).max(200).parse(displayName);
  const { supabase } = await requireUser();
  const { error } = await supabase
    .from("speaker_profiles")
    .update({ display_name: name })
    .eq("id", profileId);
  if (error) throw new Error(error.message);
  revalidatePath("/speakers");
}

/** Record the storage path of an uploaded reference clip. The upload itself
 * happens client-side under the owner's storage prefix. */
export async function setSpeakerReferenceClip(
  profileId: string,
  storagePath: string,
) {
  uuid.parse(profileId);
  const path = z.string().trim().min(1).max(1000).parse(storagePath);
  const { supabase, user } = await requireUser();
  if (!path.startsWith(`${user.id}/speaker-refs/${profileId}/`)) {
    throw new Error("Reference clip path does not match this profile.");
  }
  const { error } = await supabase
    .from("speaker_profiles")
    .update({ reference_storage_path: path })
    .eq("id", profileId);
  if (error) throw new Error(error.message);
  revalidatePath("/speakers");
}

export async function deleteSpeakerProfile(profileId: string) {
  uuid.parse(profileId);
  const { supabase, user } = await requireUser();

  const { data: profile } = await supabase
    .from("speaker_profiles")
    .select("reference_storage_path")
    .eq("id", profileId)
    .single();

  if (profile?.reference_storage_path) {
    await supabase.storage
      .from(STORAGE_BUCKET)
      .remove([profile.reference_storage_path]);
  }
  const { error } = await supabase
    .from("speaker_profiles")
    .delete()
    .eq("id", profileId)
    .eq("owner_id", user.id);
  if (error) throw new Error(error.message);
  revalidatePath("/speakers");
}
