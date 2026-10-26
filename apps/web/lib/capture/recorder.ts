"use client";

import type {
  CapturedMedia,
  CaptureCapabilities,
  CaptureProvider,
} from "./types";

/**
 * BrowserCaptureProvider — records microphone audio (optionally mixed with
 * shared system/tab audio) via MediaRecorder and yields a single Blob.
 *
 * Honest browser limits:
 *  - System/tab audio requires getDisplayMedia screen-share consent and is
 *    only reliable on Chromium. Safari/Firefox typically deliver no audio
 *    track; we surface that state rather than pretending.
 *  - MediaRecorder codecs vary: webm/opus (Chromium/Firefox), mp4 (Safari).
 */

export const RECORDING_MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/ogg;codecs=opus",
  "audio/ogg",
] as const;

/** Pure, testable: pick the first MIME type the platform reports support for. */
export function chooseRecordingMimeType(
  isTypeSupported: (mime: string) => boolean,
): string | null {
  for (const candidate of RECORDING_MIME_CANDIDATES) {
    if (isTypeSupported(candidate)) return candidate;
  }
  return null;
}

export interface PreparedSources {
  /** The stream fed to MediaRecorder (mic, or mic+system merged). */
  stream: MediaStream;
  microphoneActive: boolean;
  systemAudioActive: boolean;
  /** Raw streams owned by this capture; stop() releases all tracks. */
  ownedStreams: MediaStream[];
}

export class BrowserAudioCapture implements CaptureProvider {
  readonly sourceType = "browser" as const;
  readonly capabilities: CaptureCapabilities = {
    supportsLiveAudio: true, // MediaRecorder timeslice chunks
    supportsSystemAudio: true, // via getDisplayMedia where available
    supportsNativeCapture: false,
    supportsAutomaticStart: false,
    supportsVideo: false,
  };

  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private prepared: PreparedSources | null = null;
  private audioContext: AudioContext | null = null;
  private startedAt = 0;
  private pausedAt: number | null = null;
  private pausedTotalMs = 0;

  static isSupported(): boolean {
    return (
      typeof window !== "undefined" &&
      typeof MediaRecorder !== "undefined" &&
      !!navigator.mediaDevices?.getUserMedia
    );
  }

  static canShareSystemAudio(): boolean {
    return (
      typeof navigator !== "undefined" &&
      typeof navigator.mediaDevices?.getDisplayMedia === "function"
    );
  }

  /**
   * Acquire microphone (required) and, when requested, shared system/tab
   * audio (best effort — caller must inspect `systemAudioActive`).
   */
  async prepare(options?: { systemAudio?: boolean }): Promise<PreparedSources> {
    const withSystemAudio = options?.systemAudio ?? false;
    if (!BrowserAudioCapture.isSupported()) {
      throw new Error("Audio recording is not supported in this browser.");
    }

    const mic = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    });

    let display: MediaStream | null = null;
    if (withSystemAudio && BrowserAudioCapture.canShareSystemAudio()) {
      display = await navigator.mediaDevices.getDisplayMedia({
        video: true,
        audio: true,
      });
      // We only need its audio; discard video tracks immediately.
      display.getVideoTracks().forEach((t) => t.stop());
      if (display.getAudioTracks().length === 0) {
        // User shared without audio, or the platform didn't offer it.
        display.getTracks().forEach((t) => t.stop());
        display = null;
      }
    }

    let stream: MediaStream;
    if (display) {
      // Merge mic + shared audio into one stream for MediaRecorder.
      this.audioContext = new AudioContext();
      const dest = this.audioContext.createMediaStreamDestination();
      this.audioContext.createMediaStreamSource(mic).connect(dest);
      this.audioContext
        .createMediaStreamSource(display)
        .connect(dest);
      stream = dest.stream;
    } else {
      stream = mic;
    }

    this.prepared = {
      stream,
      microphoneActive: true,
      systemAudioActive: display !== null,
      ownedStreams: display ? [mic, display] : [mic],
    };
    return this.prepared;
  }

  get isCapturing(): boolean {
    return this.recorder?.state === "recording";
  }

  get systemAudioActive(): boolean {
    return this.prepared?.systemAudioActive ?? false;
  }

  /**
   * @param onPart — called for every MediaRecorder timeslice fragment. Parts
   * are container fragments (only part 0 carries the init segment); consumers
   * must upload them in order and never treat them as standalone files.
   */
  start(opts?: {
    partMs?: number;
    onPart?: (blob: Blob, partIndex: number) => void;
  }): void {
    if (!this.prepared) throw new Error("prepare() must run before start()");
    const mimeType = chooseRecordingMimeType((t) =>
      MediaRecorder.isTypeSupported(t),
    );
    if (!mimeType) {
      throw new Error("No supported audio recording format in this browser.");
    }
    const partMs = opts?.partMs ?? 1000;
    let partIndex = 0;
    this.recorder = new MediaRecorder(this.prepared.stream, { mimeType });
    this.chunks = [];
    this.recorder.ondataavailable = (e) => {
      if (e.data.size > 0) {
        this.chunks.push(e.data);
        opts?.onPart?.(e.data, partIndex++);
      }
    };
    this.recorder.start(partMs);
    this.startedAt = Date.now();
    this.pausedTotalMs = 0;
    this.pausedAt = null;
  }

  pause(): void {
    if (this.recorder?.state === "recording") {
      this.recorder.pause();
      this.pausedAt = Date.now();
    }
  }

  resume(): void {
    if (this.recorder?.state === "paused") {
      this.recorder.resume();
      if (this.pausedAt) this.pausedTotalMs += Date.now() - this.pausedAt;
      this.pausedAt = null;
    }
  }

  elapsedSeconds(): number {
    if (!this.startedAt) return 0;
    const end = this.pausedAt ?? Date.now();
    return Math.max(0, (end - this.startedAt - this.pausedTotalMs) / 1000);
  }

  stop(): Promise<CapturedMedia> {
    const recorder = this.recorder;
    if (!recorder) return Promise.reject(new Error("Not recording"));
    const durationSeconds = this.elapsedSeconds();
    return new Promise((resolve, reject) => {
      recorder.onstop = () => {
        this.release();
        const blob = new Blob(this.chunks, { type: recorder.mimeType });
        resolve({
          blob,
          mimeType: recorder.mimeType,
          durationSeconds,
        });
      };
      recorder.onerror = () => {
        this.release();
        reject(new Error("Recording failed"));
      };
      if (recorder.state === "paused") this.pausedTotalMs += Date.now() - (this.pausedAt ?? 0);
      recorder.stop();
    });
  }

  async cancel(): Promise<void> {
    try {
      this.recorder?.stop();
    } catch {
      /* already stopped */
    }
    this.release();
  }

  private release(): void {
    for (const s of this.prepared?.ownedStreams ?? []) {
      s.getTracks().forEach((t) => t.stop());
    }
    void this.audioContext?.close().catch(() => {});
    this.audioContext = null;
    this.prepared = null;
    this.recorder = null;
  }
}
