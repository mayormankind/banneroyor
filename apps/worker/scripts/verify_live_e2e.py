"""End-to-end verification of the near-live capture path.

Simulates what the browser does during a capture session:
  create meeting (source_type='browser') + capture_session
  -> upload part 0 -> notify_capture_part -> worker syncs
  -> live_transcript_segments appear, cursor advances
  -> upload part 1 -> notify -> cursor advances again
  -> upload full recording -> finalize_meeting_upload
  -> batch pipeline produces authoritative transcript; session -> completed

Usage (worker venv, .env populated, worker running):
    python scripts/verify_live_e2e.py --audio scripts/test_meeting.mp3
"""

import argparse
import subprocess
import sys
import tempfile
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
    web = parse(ROOT.parent / "web" / ".env.local")
    env.setdefault("SUPABASE_ANON_KEY", web.get("NEXT_PUBLIC_SUPABASE_ANON_KEY", ""))
    return env


def ok(name: str, cond: bool, detail: str = "") -> bool:
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    return cond


def split_audio(src: Path, n_parts: int, out_dir: Path) -> list[Path]:
    """Split into standalone mp3 parts (concatenating them back is valid,
    matching the byte-concat model of MediaRecorder fragments)."""
    import os

    ffprobe = os.environ.get("FFPROBE_BIN", "ffprobe")
    ffmpeg = os.environ.get("FFMPEG_BIN", "ffmpeg")
    probe = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(src)],
        capture_output=True, text=True, check=True,
    )
    dur = float(probe.stdout.strip())
    outs = []
    for i in range(n_parts):
        p = out_dir / f"part-{i:05d}.mp3"
        subprocess.run(
            [ffmpeg, "-y", "-v", "error",
             "-ss", f"{dur * i / n_parts:.3f}",
             "-to", f"{dur * (i + 1) / n_parts:.3f}",
             "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
             "-b:a", "48k", "-f", "mp3", str(p)],
            check=True,
        )
        outs.append(p)
    return outs


def wait_for(cond, timeout: int, label: str) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if cond():
                return True
        except Exception:
            pass  # transient network flake — keep polling
        time.sleep(4)
    print(f"  (timeout waiting for {label})")
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--parts", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args()

    env = load_env()
    url, service_key = env["SUPABASE_URL"], env["SUPABASE_SERVICE_ROLE_KEY"]
    anon = env.get("SUPABASE_ANON_KEY", "")
    bucket = env.get("SUPABASE_STORAGE_BUCKET", "meeting-recordings")
    results: list[bool] = []
    admin: Client = create_client(url, service_key)
    user: Client = create_client(url, anon)

    signin = user.auth.sign_in_with_password(
        {"email": "e2e-probe@banner-recall.test",
         "password": "e2e-probe-password-123!"}
    )
    user_id = signin.user.id
    results.append(ok("user sign-in", True))

    src = Path(args.audio)
    file_bytes = src.read_bytes()

    with tempfile.TemporaryDirectory() as td:
        parts = split_audio(src, args.parts, Path(td))

        # --- meeting + session ---------------------------------------------
        meeting = user.table("meetings").insert({
            "owner_id": user_id,
            "title": "E2E live-capture verification",
            "source_type": "browser",
            "recording_consent_confirmed_at": "now()",
        }).execute().data[0]
        mid = meeting["id"]
        session = user.table("capture_sessions").insert({
            "meeting_id": mid, "owner_id": user_id,
            "source_type": "browser", "status": "capturing",
            "started_at": "now()",
        }).execute().data[0]
        sid = session["id"]
        results.append(ok("browser meeting + session", True, mid))

        # --- stream parts ---------------------------------------------------
        prev_count = 0
        for i, part in enumerate(parts):
            path = f"{user_id}/{mid}/live/{sid}/part-{i:05d}"
            for attempt in range(3):
                try:
                    user.storage.from_(bucket).upload(
                        path, part.read_bytes(),
                        {"content-type": "audio/mpeg", "upsert": "true"},
                    )
                    user.rpc("notify_capture_part", {
                        "p_session_id": sid, "p_part_index": i,
                        "p_storage_path": path,
                        "p_size_bytes": part.stat().st_size,
                        "p_mime_type": "audio/mpeg",
                    }).execute()
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(3)

            def synced(want=i, pc=prev_count):
                s = admin.table("capture_sessions").select(
                    "transcribed_part_index").eq("id", sid).single().execute().data
                segs = admin.table("live_transcript_segments").select("id") \
                    .eq("session_id", sid).execute().data
                return s["transcribed_part_index"] == want and len(segs) > pc

            got = wait_for(lambda: synced(), args.timeout, f"part {i} sync")
            segs_now = admin.table("live_transcript_segments").select("id") \
                .eq("session_id", sid).execute().data
            results.append(ok(
                f"part {i} synced -> live segments", got,
                f"{len(segs_now)} segments total",
            ))
            prev_count = len(segs_now)

        # --- segment timestamps are globally offset --------------------------
        rows = admin.table("live_transcript_segments").select(
            "run_part_index,start_ms,text").eq("session_id", sid) \
            .order("start_ms").execute().data
        later_run = [r for r in rows if r["run_part_index"] > 0]
        if later_run:
            results.append(ok(
                "later-run segments have nonzero global offset",
                later_run[0]["start_ms"] > 0,
                f"first later segment at {later_run[0]['start_ms']}ms",
            ))
        print("  live segments sample:", [r["text"][:40] for r in rows[:4]])

        # --- live intelligence (runs every 3rd part) --------------------------
        if args.parts >= 3:
            def intel_done():
                s = admin.table("capture_sessions").select(
                    "intel_part_index").eq("id", sid).single().execute().data
                return s["intel_part_index"] >= 0

            got_intel = wait_for(intel_done, args.timeout, "live intel")
            items = admin.table("live_intelligence_items").select(
                "kind,content").eq("session_id", sid).execute().data
            results.append(ok(
                "live intelligence committed", got_intel and len(items) > 0,
                f"{len(items)} candidate items",
            ))
            print("  intel kinds:", {i["kind"] for i in items})

        # --- stop + finalize --------------------------------------------------
        user.table("capture_sessions").update(
            {"status": "stopped", "ended_at": "now()"}
        ).eq("id", sid).execute()
        user.rpc("begin_meeting_upload", {"p_meeting_id": mid}).execute()
        final_path = f"{user_id}/{mid}/{uuid.uuid4()}-{src.name}"
        signed = user.storage.from_(bucket).create_signed_upload_url(final_path)
        su = getattr(signed, "signed_url", None) or getattr(signed, "signedUrl", None)
        if su is None and isinstance(signed, dict):
            su = signed.get("signed_url") or signed.get("signedUrl")
        put = httpx.put(su, content=file_bytes,
                        headers={"content-type": "audio/mpeg"}, timeout=300)
        results.append(ok("final upload", put.is_success, str(put.status_code)))
        user.table("capture_sessions").update(
            {"status": "uploaded", "storage_path": final_path}
        ).eq("id", sid).execute()
        user.rpc("finalize_meeting_upload", {
            "p_meeting_id": mid, "p_file_path": final_path,
            "p_file_name": src.name, "p_mime_type": "audio/mpeg",
            "p_size_bytes": len(file_bytes),
        }).execute()
        results.append(ok("finalize_meeting_upload", True))

        def finished():
            m = admin.table("meetings").select("status") \
                .eq("id", mid).single().execute().data
            return m["status"] in ("completed", "failed")

        wait_for(finished, args.timeout, "batch pipeline")
        m = admin.table("meetings").select("status,current_run_id") \
            .eq("id", mid).single().execute().data
        results.append(ok("batch pipeline completed",
                          m["status"] == "completed", m["status"]))

        sess = admin.table("capture_sessions").select("status") \
            .eq("id", sid).single().execute().data
        results.append(ok("session marked completed",
                          sess["status"] == "completed", sess["status"]))

        final_segs = admin.table("transcript_segments").select("id") \
            .eq("meeting_id", mid).eq("run_id", m["current_run_id"]).execute().data
        results.append(ok("authoritative transcript exists",
                          len(final_segs) > 0, f"{len(final_segs)} segments"))

    passed = sum(1 for r in results if r)
    print(f"\n=== {passed}/{len(results)} checks passed ===")
    print(f"(test meeting {mid} kept for inspection; delete via UI)")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
