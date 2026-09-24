from app.intelligence.schema import (
    ActionItemOut,
    DecisionOut,
    KeyPointOut,
    MeetingIntelligence,
    OpenQuestionOut,
    merge_chunk_results,
    validate_evidence,
)


def make_intel(**kwargs) -> MeetingIntelligence:
    base = dict(
        summary="A meeting happened.",
        key_points=[],
        decisions=[],
        action_items=[],
        open_questions=[],
    )
    base.update(kwargs)
    return MeetingIntelligence(**base)


class TestSchema:
    def test_requires_nonempty_summary(self):
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            MeetingIntelligence(summary="")

    def test_nullable_fields(self):
        a = ActionItemOut(task="do x")
        assert a.owner_speaker_label is None
        assert a.deadline is None


class TestValidateEvidence:
    def test_drops_out_of_range_indices(self):
        intel = make_intel(
            key_points=[
                KeyPointOut(text="real", evidence_segment_indices=[0, 2, 999, -1])
            ]
        )
        out = validate_evidence(intel, segment_count=3)
        assert out.key_points[0].evidence_segment_indices == [0, 2]

    def test_keeps_items_with_no_evidence(self):
        intel = make_intel(
            decisions=[DecisionOut(text="d", evidence_segment_indices=[42])]
        )
        out = validate_evidence(intel, segment_count=1)
        assert len(out.decisions) == 1
        assert out.decisions[0].evidence_segment_indices == []


class TestMerge:
    def test_single_result_passthrough(self):
        r = make_intel(summary="S", key_points=[KeyPointOut(text="k")])
        merged = merge_chunk_results([r])
        assert merged.summary == "S"
        assert len(merged.key_points) == 1

    def test_dedupes_repeated_findings(self):
        a = make_intel(
            summary="s1",
            decisions=[DecisionOut(text="Use cursor pagination")],
            action_items=[ActionItemOut(task="Ship it")],
            open_questions=[OpenQuestionOut(question="Page size?")],
        )
        b = make_intel(
            summary="s2",
            decisions=[
                DecisionOut(text="Use cursor pagination"),  # dup
                DecisionOut(text="Deploy on Fridays"),
            ],
        )
        merged = merge_chunk_results([a, b])
        assert len(merged.decisions) == 2
        assert {d.text for d in merged.decisions} == {
            "Use cursor pagination",
            "Deploy on Fridays",
        }
        assert len(merged.action_items) == 1

    def test_dedupes_case_insensitive_whitespace(self):
        a = make_intel(decisions=[DecisionOut(text="Use  cursor   pagination")])
        b = make_intel(decisions=[DecisionOut(text="use cursor pagination")])
        merged = merge_chunk_results([a, b])
        assert len(merged.decisions) == 1
