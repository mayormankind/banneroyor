"""End-to-end smoke test against hosted Supabase + a real recording.

Mirrors the exact sequence the web client performs:
  create meeting (as user) -> begin_meeting_upload -> signed upload URL ->
  PUT file -> finalize_meeting_upload -> worker picks job -> report rows.

Usage (from apps/worker, venv active, .env populated):
    python scripts/verify_e2e.py --audio path/to/file.mp3

Requires SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_ANON_KEY,
OPENAI_API_KEY (for TTS sample generation when --audio is omitted and
--generate is passed).
"""

import argparse
import sys
import time
import uuid
from pathlib import Path

import httpx
from supabase import Client, create_client

ROOT = Path(__file__).resolve().parents[1]


def load_env() -> dict[str, str]:
    def parse(path: Path) -> dict[str, str]:
        out: dict[str, str] = {}
        if not path.exists():
            return out
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
        return out

    env = parse(ROOT / ".env")
    # The anon key lives in the web app's env file.
    web = parse(ROOT.parent / "web" / ".env.local")
    env.setdefault("SUPABASE_ANON_KEY", web.get("NEXT_PUBLIC_SUPABASE_ANON_KEY", ""))
    return env


def ok(name: str, cond: bool, detail: str = "") -> bool:
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    return cond


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", help="Path to a real audio/video file")
    ap.add_argument("--email", default="e2e-probe@banner-recall.test")
    ap.add_argument("--password", default="e2e-probe-password-123!")
    ap.add_argument("--keep", action="store_true", help="Keep the test meeting")
    ap.add_argument("--timeout", type=int, default=600,
                    help="Seconds to wait for processing")
    ap.add_argument("--source", choices=["upload", "browser"], default="upload",
                    help="Exercise the browser-capture data path "
                         "(source_type='browser' + capture_sessions row)")
    args = ap.parse_args()

    env = load_env()
    url = env["SUPABASE_URL"]
    service_key = env["SUPABASE_SERVICE_ROLE_KEY"]
    anon_key = env.get("SUPABASE_ANON_KEY", "")
    bucket = env.get("SUPABASE_STORAGE_BUCKET", "meeting-recordings")

    results: list[bool] = []
    admin: Client = create_client(url, service_key)

    # --- Schema presence -----------------------------------------------------
    tables = [
        "profiles", "meetings", "participants", "speaker_profiles",
        "transcript_segments", "summaries", "key_points", "decisions",
        "action_items", "questions", "processing_jobs",
        "processing_job_events",
    ]
    for t in tables:
        try:
            admin.table(t).select("id").limit(1).execute()
            results.append(ok(f"table {t}", True))
        except Exception as e:
            results.append(ok(f"table {t}", False, str(e)[:120]))

    try:
        res = admin.rpc("read_meeting_jobs", {"p_vt_seconds": 0, "p_qty": 0}).execute()
        results.append(ok("pgmq read_meeting_jobs", res.data is not None))
    except Exception as e:
        results.append(ok("pgmq read_meeting_jobs", False, str(e)[:120]))

    try:
        buckets = admin.storage.list_buckets()
        names = [b.name if hasattr(b, "name") else b.get("name") for b in buckets]
        results.append(ok(f"storage bucket '{bucket}'", bucket in names))
    except Exception as e:
        results.append(ok("storage bucket list", False, str(e)[:120]))

    # --- Auth: ensure a test user exists --------------------------------------
    if not anon_key:
        print("SUPABASE_ANON_KEY missing in worker .env — required for the "
              "user-scoped flow. Add it and re-run.")
        return 2

    user_client: Client = create_client(url, anon_key)
    try:
        admin.auth.admin.create_user(
            {"email": args.email, "password": args.password,
             "email_confirm": True}
        )
    except Exception:
        pass  # already exists

    try:
        signin = user_client.auth.sign_in_with_password(
            {"email": args.email, "password": args.password}
        )
        user_id = signin.user.id
        results.append(ok("user sign-in", True, user_id))
    except Exception as e:
        results.append(ok("user sign-in", False, str(e)[:160]))
        return _summary(results)

    # --- Client-equivalent flow ------------------------------------------------
    audio_path = Path(args.audio) if args.audio else None
    if not audio_path or not audio_path.exists():
        print(f"Audio file missing: {audio_path}")
        return _summary(results)

    file_bytes = audio_path.read_bytes()
    mime = {
        ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".wav": "audio/wav",
        ".mp4": "video/mp4", ".webm": "audio/webm", ".ogg": "audio/ogg",
    }.get(audio_path.suffix.lower(), "application/octet-stream")

    meeting_id = None
    try:
        res = (
            user_client.table("meetings")
            .insert({
                "owner_id": user_id,
                "title": f"E2E verification meeting ({args.source})",
                "source_type": args.source,
                "recording_consent_confirmed_at": "now()",
            })
            .execute()
        )
        meeting_id = res.data[0]["id"]
        results.append(ok(f"insert meeting (source={args.source})", True, meeting_id))
    except Exception as e:
        results.append(ok("insert meeting", False, str(e)[:160]))
        return _summary(results)

    if args.source == "browser":
        try:
            sess = (
                user_client.table("capture_sessions")
                .insert({
                    "meeting_id": meeting_id,
                    "owner_id": user_id,
                    "source_type": "browser",
                    "status": "created",
                })
                .execute()
            )
            session_id = sess.data[0]["id"]
            user_client.table("capture_sessions").update({
                "status": "capturing",
                "started_at": "now()",
                "metadata": {"microphone": True},
            }).eq("id", session_id).execute()
            results.append(ok("capture_session lifecycle", True, session_id))
        except Exception as e:
            results.append(ok("capture_session lifecycle", False, str(e)[:160]))

    try:
        user_client.rpc("begin_meeting_upload", {"p_meeting_id": meeting_id}).execute()
        results.append(ok("begin_meeting_upload", True))
    except Exception as e:
        results.append(ok("begin_meeting_upload", False, str(e)[:160]))

    storage_path = f"{user_id}/{meeting_id}/{uuid.uuid4()}-{audio_path.name}"
    try:
        signed = user_client.storage.from_(bucket).create_signed_upload_url(storage_path)
        # supabase-py returns dict-ish; signed url field name: 'signed_url' or 'signedUrl'
        signed_url = getattr(signed, "signed_url", None) or getattr(signed, "signedUrl", None)
        if signed_url is None and isinstance(signed, dict):
            signed_url = signed.get("signed_url") or signed.get("signedUrl")
        if not signed_url:
            raise RuntimeError(f"no signed URL in response: {signed}")
        put = httpx.put(signed_url, content=file_bytes,
                        headers={"content-type": mime}, timeout=300)
        results.append(ok("signed upload PUT", put.is_success,
                          f"status={put.status_code}"))
    except Exception as e:
        results.append(ok("signed upload", False, str(e)[:160]))
        return _summary(results)

    try:
        fin = user_client.rpc("finalize_meeting_upload", {
            "p_meeting_id": meeting_id,
            "p_file_path": storage_path,
            "p_file_name": audio_path.name,
            "p_mime_type": mime,
            "p_size_bytes": len(file_bytes),
        }).execute()
        job_id = fin.data
        results.append(ok("finalize_meeting_upload", True, f"job={job_id}"))
        if args.source == "browser":
            user_client.table("capture_sessions").update({
                "status": "uploaded",
                "ended_at": "now()",
                "storage_path": storage_path,
            }).eq("meeting_id", meeting_id).execute()
    except Exception as e:
        results.append(ok("finalize_meeting_upload", False, str(e)[:160]))
        return _summary(results)

    # --- Wait for the worker ---------------------------------------------------
    print(f"Waiting up to {args.timeout}s for the worker to process "
          f"meeting {meeting_id} ...")
    deadline = time.time() + args.timeout
    final_status = None
    while time.time() < deadline:
        m = admin.table("meetings").select("status,failure_reason,current_run_id") \
            .eq("id", meeting_id).single().execute().data
        if m["status"] in ("completed", "failed"):
            final_status = m
            break
        time.sleep(5)
        print("  status:", m["status"], flush=True)

    if final_status is None:
        results.append(ok("worker processed meeting", False, "timeout"))
        return _summary(results)

    results.append(ok("worker processed meeting",
                      final_status["status"] == "completed",
                      final_status["status"]))

    if final_status["status"] == "completed":
        run_id = final_status["current_run_id"]
        segs = admin.table("transcript_segments").select("id") \
            .eq("meeting_id", meeting_id).eq("run_id", run_id).execute().data
        results.append(ok("transcript segments persisted", len(segs) > 0,
                          f"{len(segs)} segments"))
        summary = admin.table("summaries").select("summary") \
            .eq("meeting_id", meeting_id).eq("run_id", run_id).execute().data
        results.append(ok("summary persisted", bool(summary)))
        parts = admin.table("participants").select("display_name") \
            .eq("meeting_id", meeting_id).execute().data
        results.append(ok("participants created", len(parts) > 0,
                          str([p["display_name"] for p in parts])))

    if not args.keep and meeting_id:
        try:
            user_client.rpc("delete_meeting", {"p_meeting_id": meeting_id}).execute()
            results.append(ok("delete_meeting cleanup", True))
        except Exception as e:
            results.append(ok("delete_meeting cleanup", False, str(e)[:120]))

    return _summary(results)


def _summary(results: list[bool]) -> int:
    passed = sum(1 for r in results if r)
    print(f"\n=== {passed}/{len(results)} checks passed ===")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
