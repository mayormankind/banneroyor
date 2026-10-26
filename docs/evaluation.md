# Testing and evaluation

## Automated coverage

**Worker (pytest — 36 tests, no network/ffmpeg/OpenAI):**

- transcript normalization: seconds→ms, chunk offsets, invalid-segment drops,
  dense re-indexing
- chunk merge + deduplication, timestamp ordering
- prompt transcript rendering with stable evidence indices
- character-bounded transcript chunking on segment boundaries
- intelligence schema validation, out-of-range evidence rejection,
  cross-chunk dedupe
- queue message validation; provider status→error classification; bounded
  exponential backoff
- silence-aware split-point selection with exact-boundary fallback
- end-to-end pipeline against an in-memory store + fake providers: full
  success path, idempotent duplicate delivery, participant creation, fatal vs
  retryable failure, retry-exhaustion terminal state

**Web (vitest):**

- formatting/speaker-label helpers
- recall citation validation: fabricated references dropped, dedupe, mapping
  to meeting/segment/timestamp

## Running

```bash
cd apps/worker && .venv/Scripts/python -m pytest
cd apps/web && npm run test && npm run typecheck && npm run lint && npm run build
```

## Real-provider evaluation

Requires `OPENAI_API_KEY` and a permission-cleared test recording.

1. Start local Supabase + web + worker with real providers
   (`WORKER_FAKE_PROVIDERS=false`).
2. Upload the test recording through the UI.
3. Check against `evaluation/fixtures/<name>.expected.md`:
   - speaker count and label stability
   - segment timestamp sanity (monotonic, within duration)
   - expected decisions/action items present with valid evidence links
   - search and recall return the meeting

Document observed transcription/diarization errors per run — this repo does
**not** claim production transcription accuracy from mocked tests.

## Manual acceptance checklist

- [ ] Sign in; create meeting; consent + upload; progress shown
- [ ] Queued → processing → ready without refresh hacks
- [ ] Transcript renders with speakers + timestamps; search/filter/copy work
- [ ] Assign speaker name; edits persist; segment text edit + revert
- [ ] Action item status changes persist across reload
- [ ] Retry a failed job; delete a meeting end-to-end
- [ ] FTS search returns grouped results; recall cites real segments only
- [ ] A second user cannot read the first user's meetings (RLS)
