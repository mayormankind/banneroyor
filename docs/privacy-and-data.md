# Privacy and data handling

## Consent

Uploading requires an explicit confirmation that the user has the right to record and upload the meeting and has given any legally required notice or obtained consent from participants. The timestamp is stored on
`meetings.recording_consent_confirmed_at`. This is a product safeguard — the
user remains responsible for complying with applicable laws and meeting
policies.

## Isolation

- All user data tables enable Postgres RLS scoped to `owner_id = auth.uid()`.
- Recordings live in a **private** bucket under `{owner_id}/{meeting_id}/…`;
  storage policies restrict access to the owner folder.
- Media playback uses short-lived signed URLs minted after a server-side
  ownership check.
- Pipeline-owned meeting fields (status, source paths, run pointers) are
  read-only for end users (column grants + trigger).
- Service-role credentials exist only in the worker/deployment env — never in
  the browser.

## Speaker identity

Diarization labels (`A`, `B`, …) are preserved verbatim as provenance.
Participants created from labels get neutral names (`Speaker 1`) unless the
user explicitly enrolled a known-speaker reference clip
(`speaker_profiles.reference_storage_path`), in which case OpenAI's
`known_speaker_names/references` hints may attach the enrolled name. Users can
always rename a speaker manually. We never infer real-world identity from
conversational content.

## Retention and deletion

- Source recordings persist until the meeting is deleted.
- Intermediate normalized audio/chunks exist only in the worker's temp dir and
  are removed when each job finishes (success or failure).
- Deleting a meeting removes storage objects (best-effort API delete +
  `storage.objects` rows) and cascades through Postgres to transcript,
  participants, intelligence, and processing records — atomically and
  idempotently.
- Superseded processing runs are retained for provenance but never displayed
  (the UI reads only `current_run_id`).

## Logging

The worker emits structured JSON logs containing ids, stages, durations, error
codes, and usage metadata only — never raw audio, transcript contents, API
keys, or signed URLs. User-facing error messages are sanitized; provider
payload details are kept in `processing_jobs.diagnostic`, readable only by the
service role.

## Model training

Meeting content is sent to OpenAI for transcription and analysis. Banner
Recall does not itself train models on user content; whether OpenAI retains
API data is governed by the API terms of the account used — review OpenAI's
data-usage terms for your key before production use.
