import { describe, expect, it } from "vitest";
import {
  formatBytes,
  formatDurationSeconds,
  formatMs,
  speakerDisplayName,
} from "./format";

describe("formatMs", () => {
  it("formats sub-hour timestamps", () => {
    expect(formatMs(0)).toBe("0:00");
    expect(formatMs(4200)).toBe("0:04");
    expect(formatMs(65_000)).toBe("1:05");
    expect(formatMs(3_599_000)).toBe("59:59");
  });

  it("formats hour-plus timestamps", () => {
    expect(formatMs(3_600_000)).toBe("1:00:00");
    expect(formatMs(7_261_500)).toBe("2:01:01");
  });

  it("clamps negative values", () => {
    expect(formatMs(-500)).toBe("0:00");
  });
});

describe("formatDurationSeconds", () => {
  it("handles null", () => {
    expect(formatDurationSeconds(null)).toBe("—");
  });
  it("formats seconds", () => {
    expect(formatDurationSeconds(95)).toBe("1:35");
  });
});

describe("formatBytes", () => {
  it("handles null and zero", () => {
    expect(formatBytes(null)).toBe("—");
    expect(formatBytes(0)).toBe("0 B");
  });
  it("scales units", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});

describe("speakerDisplayName", () => {
  it("prefers participant name", () => {
    expect(speakerDisplayName("A", "Mayowa")).toBe("Mayowa");
  });
  it("maps letter labels to friendly names", () => {
    expect(speakerDisplayName("A", null)).toBe("Speaker 1");
    expect(speakerDisplayName("C", null)).toBe("Speaker 3");
  });
  it("keeps non-letter labels", () => {
    expect(speakerDisplayName("SPEAKER_07", null)).toBe("SPEAKER_07");
  });
  it("handles unknown speakers", () => {
    expect(speakerDisplayName(null, null)).toBe("Unknown speaker");
  });
});
