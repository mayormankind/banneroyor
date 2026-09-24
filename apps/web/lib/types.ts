// Row types mirroring supabase/migrations. Kept as plain types (no generated
// client typings) so the app compiles without a live Supabase typegen step.

export type MeetingStatus =
  | "draft"
  | "uploading"
  | "uploaded"
  | "queued"
  | "processing"
  | "completed"
  | "failed"
  | "deleting";

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "dead";

export type ActionItemStatus = "open" | "in_progress" | "completed" | "cancelled";

export interface Profile {
  id: string;
  display_name: string;
  email: string | null;
  avatar_url: string | null;
  created_at: string;
  updated_at: string;
}

export interface Meeting {
  id: string;
  owner_id: string;
  title: string;
  description: string | null;
  meeting_date: string;
  duration_seconds: number | null;
  status: MeetingStatus;
  source_type: string;
  source_file_path: string | null;
  source_file_name: string | null;
  source_mime_type: string | null;
  source_size_bytes: number | null;
  recording_consent_confirmed_at: string | null;
  current_run_id: string | null;
  failure_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface Participant {
  id: string;
  meeting_id: string;
  owner_id: string;
  profile_id: string | null;
  display_name: string;
  email: string | null;
  speaker_label: string | null;
  created_at: string;
  updated_at: string;
}

export interface SpeakerProfile {
  id: string;
  owner_id: string;
  display_name: string;
  reference_storage_path: string | null;
  reference_metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface TranscriptSegment {
  id: string;
  meeting_id: string;
  owner_id: string;
  run_id: string | null;
  segment_index: number;
  speaker_label: string | null;
  participant_id: string | null;
  speaker_profile_id: string | null;
  start_ms: number;
  end_ms: number;
  text: string;
  user_text: string | null;
  confidence: number | null;
  source: string;
  edited_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Summary {
  id: string;
  meeting_id: string;
  run_id: string;
  summary: string;
  generation_version: string;
  model: string | null;
  is_edited: boolean;
  created_at: string;
}

export interface KeyPoint {
  id: string;
  meeting_id: string;
  run_id: string;
  content: string;
  sort_order: number;
  evidence_segment_ids: string[];
  timestamp_ms: number | null;
  generation_version: string;
  is_edited: boolean;
}

export interface Decision {
  id: string;
  meeting_id: string;
  run_id: string;
  description: string;
  speaker_label: string | null;
  participant_id: string | null;
  evidence_segment_ids: string[];
  timestamp_ms: number | null;
  generation_version: string;
  is_edited: boolean;
}

export interface ActionItem {
  id: string;
  meeting_id: string;
  run_id: string;
  description: string;
  owner_participant_id: string | null;
  owner_speaker_label: string | null;
  due_date: string | null;
  status: ActionItemStatus;
  evidence_segment_ids: string[];
  timestamp_ms: number | null;
  generation_version: string;
  is_edited: boolean;
}

export interface Question {
  id: string;
  meeting_id: string;
  run_id: string;
  question: string;
  resolved: boolean;
  evidence_segment_ids: string[];
  timestamp_ms: number | null;
  generation_version: string;
  is_edited: boolean;
}

export interface ProcessingJob {
  id: string;
  meeting_id: string;
  owner_id: string;
  job_type: string;
  status: JobStatus;
  attempts: number;
  max_attempts: number;
  current_stage: string | null;
  progress: number | null;
  run_id: string | null;
  error_code: string | null;
  error_message: string | null;
  diagnostic: Record<string, unknown> | null;
  cost_metadata: Record<string, unknown>;
  started_at: string | null;
  completed_at: string | null;
  next_attempt_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProcessingJobEvent {
  id: string;
  job_id: string;
  meeting_id: string;
  stage: string;
  status: "started" | "completed" | "failed" | "retrying" | "skipped";
  attempt_number: number;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  error_code: string | null;
  error_message: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface SearchResult {
  meeting_id: string;
  meeting_title: string;
  meeting_date: string;
  result_type:
    | "meeting"
    | "summary"
    | "key_point"
    | "decision"
    | "action_item"
    | "question"
    | "transcript";
  result_id: string;
  excerpt: string;
  rank: number;
  segment_start_ms: number | null;
}

export interface RecallCitation {
  meeting_id: string;
  meeting_title: string;
  meeting_date: string;
  segment_id: string | null;
  timestamp_ms: number | null;
  excerpt: string;
}

export interface RecallAnswer {
  answer: string;
  sufficient_evidence: boolean;
  citations: RecallCitation[];
}
