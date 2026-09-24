import { NextResponse } from "next/server";
import { z } from "zod";
import { createClient } from "@/lib/supabase/server";
import { answerQuestion } from "@/lib/recall";

const bodySchema = z.object({
  question: z.string().trim().min(3).max(2000),
});

export async function POST(request: Request) {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  let question: string;
  try {
    ({ question } = bodySchema.parse(await request.json()));
  } catch {
    return NextResponse.json({ error: "Invalid request" }, { status: 400 });
  }

  try {
    const answer = await answerQuestion(supabase, question);
    return NextResponse.json(answer);
  } catch (e) {
    const message = e instanceof Error ? e.message : "";
    if (message.includes("OPENAI_API_KEY")) {
      return NextResponse.json(
        { error: "Recall is not configured on this deployment." },
        { status: 503 },
      );
    }
    return NextResponse.json(
      { error: "Recall failed. Please try again." },
      { status: 502 },
    );
  }
}
