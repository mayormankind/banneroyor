"""Strict schemas for generated meeting intelligence. The model references
transcript evidence by segment *index* (not UUID — it cannot know our DB ids);
the pipeline maps indices to persisted segment ids after validation."""

from pydantic import BaseModel, Field


class KeyPointOut(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    evidence_segment_indices: list[int] = Field(default_factory=list)
    timestamp_ms: int | None = None


class DecisionOut(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    speaker_label: str | None = None
    evidence_segment_indices: list[int] = Field(default_factory=list)
    timestamp_ms: int | None = None


class ActionItemOut(BaseModel):
    task: str = Field(min_length=1, max_length=2000)
    owner_speaker_label: str | None = None
    deadline: str | None = None
    evidence_segment_indices: list[int] = Field(default_factory=list)
    timestamp_ms: int | None = None


class OpenQuestionOut(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    resolved: bool = False
    evidence_segment_indices: list[int] = Field(default_factory=list)
    timestamp_ms: int | None = None


class MeetingIntelligence(BaseModel):
    summary: str = Field(min_length=1, max_length=20000)
    key_points: list[KeyPointOut] = Field(default_factory=list)
    decisions: list[DecisionOut] = Field(default_factory=list)
    action_items: list[ActionItemOut] = Field(default_factory=list)
    open_questions: list[OpenQuestionOut] = Field(default_factory=list)


def validate_evidence(
    intel: MeetingIntelligence, segment_count: int
) -> MeetingIntelligence:
    """Drop evidence references that point outside the transcript. Keeps the
    item itself — an unverifiable reference is removed, not the claim — but the
    item must then stand on transcript content alone."""
    def clean(indices: list[int]) -> list[int]:
        return [i for i in indices if isinstance(i, int) and 0 <= i < segment_count]

    for item in [
        *intel.key_points,
        *intel.decisions,
        *intel.action_items,
        *intel.open_questions,
    ]:
        item.evidence_segment_indices = clean(item.evidence_segment_indices)
    return intel


def merge_chunk_results(results: list[MeetingIntelligence]) -> MeetingIntelligence:
    """Merge per-chunk analyses into one report, deduplicating repeated findings
    produced by overlapping or adjacent chunks."""
    if not results:
        raise ValueError("no intelligence results to merge")

    def norm(s: str) -> str:
        return " ".join(s.lower().split())

    seen_kp: set[str] = set()
    seen_dec: set[str] = set()
    seen_act: set[str] = set()
    seen_q: set[str] = set()

    merged = MeetingIntelligence(summary=results[0].summary)
    if len(results) > 1:
        merged.summary = "\n\n".join(r.summary for r in results)

    for r in results:
        for kp in r.key_points:
            if norm(kp.text) not in seen_kp:
                seen_kp.add(norm(kp.text))
                merged.key_points.append(kp)
        for d in r.decisions:
            if norm(d.text) not in seen_dec:
                seen_dec.add(norm(d.text))
                merged.decisions.append(d)
        for a in r.action_items:
            if norm(a.task) not in seen_act:
                seen_act.add(norm(a.task))
                merged.action_items.append(a)
        for q in r.open_questions:
            if norm(q.question) not in seen_q:
                seen_q.add(norm(q.question))
                merged.open_questions.append(q)
    return merged
