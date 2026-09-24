import { NewMeetingForm } from "@/components/new-meeting-form";
import { getMaxUploadBytes } from "@/lib/env";

export const metadata = { title: "New meeting" };

export default function NewMeetingPage() {
  return (
    <div className="mx-auto max-w-2xl">
      <h1 className="mb-6 text-xl font-semibold tracking-tight">New meeting</h1>
      <NewMeetingForm maxUploadBytes={getMaxUploadBytes()} />
    </div>
  );
}
