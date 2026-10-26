import { describe, expect, it } from "vitest";
import { chooseRecordingMimeType } from "./recorder";
import { extensionForMime } from "./ingest";

describe("chooseRecordingMimeType", () => {
  it("prefers webm/opus when supported", () => {
    const mime = chooseRecordingMimeType(() => true);
    expect(mime).toBe("audio/webm;codecs=opus");
  });

  it("falls back through candidates in order", () => {
    // Safari: no webm, mp4 works.
    const mime = chooseRecordingMimeType((t) => t === "audio/mp4");
    expect(mime).toBe("audio/mp4");
  });

  it("returns null when nothing is supported", () => {
    expect(chooseRecordingMimeType(() => false)).toBeNull();
  });
});

describe("extensionForMime", () => {
  it("maps known audio types", () => {
    expect(extensionForMime("audio/webm;codecs=opus")).toBe(".webm");
    expect(extensionForMime("audio/mp4")).toBe(".mp4");
    expect(extensionForMime("audio/ogg")).toBe(".ogg");
  });

  it("is case-insensitive and strips codecs", () => {
    expect(extensionForMime("AUDIO/WEBM;CODECS=OPUS")).toBe(".webm");
  });

  it("returns .bin for unknown types", () => {
    expect(extensionForMime("application/x-strange")).toBe(".bin");
  });
});
