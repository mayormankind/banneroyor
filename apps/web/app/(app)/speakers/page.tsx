import { createClient } from "@/lib/supabase/server";
import { SpeakerProfilesPanel } from "@/components/speaker-profiles-panel";
import type { SpeakerProfile } from "@/lib/types";

export const metadata = { title: "Speakers" };

export default async function SpeakersPage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  const { data } = await supabase
    .from("speaker_profiles")
    .select("*")
    .order("display_name");

  return (
    <div>
      <div className="mb-6">
        <h1 className="text-xl font-semibold tracking-tight">Known speakers</h1>
        <p className="mt-1 text-sm text-muted">
          Enroll the people you meet with regularly. When a voice reference is
          on file, the transcriber labels their speech with their name
          automatically.
        </p>
      </div>
      <SpeakerProfilesPanel
        profiles={(data ?? []) as SpeakerProfile[]}
        ownerId={user!.id}
      />
    </div>
  );
}
