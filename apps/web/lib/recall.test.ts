import { describe, expect, it } from "vitest";
import { toCitations } from "./recall";

const evidence = [
  {
    evidence_id: "seg-1",
    meeting_id: "m-1",
    meeting_title: "Product sync",
    meeting_date: "2026-09-20T10:00:00Z",
    segment_id: "seg-1",
    timestamp_ms: 4200,
    kind: "transcript",
    text: "We decided to use cursor pagination.",
  },
  {
    evidence_id: "dec-9",
    meeting_id: "m-2",
    meeting_title: "Retro",
    meeting_date: "2026-09-21T10:00:00Z",
    segment_id: null,
    timestamp_ms: null,
    kind: "decision",
    text: "Deploy on Fridays.",
  },
];

describe("toCitations", () => {
  it("maps valid evidence ids to citations", () => {
    const out = toCitations([{ evidence_id: "seg-1" }], evidence);
    expect(out).toHaveLength(1);
    expect(out[0].meeting_title).toBe("Product sync");
    expect(out[0].timestamp_ms).toBe(4200);
  });

  it("drops fabricated references", () => {
    const out = toCitations(
      [{ evidence_id: "seg-1" }, { evidence_id: "invented-id" }],
      evidence,
    );
    expect(out).toHaveLength(1);
  });

  it("deduplicates repeated citations", () => {
    const out = toCitations(
      [{ evidence_id: "seg-1" }, { evidence_id: "seg-1" }, { evidence_id: "dec-9" }],
      evidence,
    );
    expect(out).toHaveLength(2);
  });

  it("returns empty for no evidence", () => {
    expect(toCitations([{ evidence_id: "x" }], [])).toEqual([]);
  });
});
