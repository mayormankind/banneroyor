import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase/server";
import { getMediaUrlTtl } from "@/lib/env";
import { STORAGE_BUCKET } from "@/lib/constants";
import type { Meeting } from "@/lib/types";

export async function GET(
  _request: Request,
  { params }: RouteContext<"/api/meetings/[id]/media">,
) {
  const { id } = await params;
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  // RLS enforces ownership on this query.
  const { data: meeting } = await supabase
    .from("meetings")
    .select("id, source_file_path")
    .eq("id", id)
    .single();
  const row = meeting as Pick<Meeting, "id" | "source_file_path"> | null;

  if (!row?.source_file_path) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  const { data, error } = await supabase.storage
    .from(STORAGE_BUCKET)
    .createSignedUrl(row.source_file_path, getMediaUrlTtl());

  if (error || !data?.signedUrl) {
    return NextResponse.json(
      { error: "Could not create media URL" },
      { status: 502 },
    );
  }
  return NextResponse.json(
    { url: data.signedUrl },
    { headers: { "Cache-Control": "no-store" } },
  );
}
