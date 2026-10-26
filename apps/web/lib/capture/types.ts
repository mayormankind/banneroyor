/**
 * Capture abstraction — every way Banner Recall obtains meeting media
 * converges on the same ingestion contract: produce a Blob/File, upload it to
 * private storage, finalize. The pipeline downstream never cares which
 * provider produced the bytes.
 */

export type CaptureSourceType =
  | "upload"
  | "browser"
  | "desktop"
  | "google_meet"
  | "zoom"
  | "teams";

export type CaptureSessionStatus =
  | "created"
  | "requesting_permission"
  | "capturing"
  | "paused"
  | "stopped"
  | "uploading"
  | "uploaded"
  | "processing"
  | "completed"
  | "failed"
  | "cancelled";

export interface CaptureSession {
  id: string;
  meeting_id: string;
  owner_id: string;
  source_type: CaptureSourceType;
  status: CaptureSessionStatus;
  audio_format: string | null;
  sample_rate: number | null;
  channel_count: number | null;
  storage_path: string | null;
  error_message: string | null;
  metadata: Record<string, unknown>;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface CaptureCapabilities {
  supportsLiveAudio: boolean;
  supportsSystemAudio: boolean;
  supportsNativeCapture: boolean;
  supportsAutomaticStart: boolean;
  supportsVideo: boolean;
}

/** What a capture provider yields when stopped. */
export interface CapturedMedia {
  blob: Blob;
  mimeType: string;
  durationSeconds: number;
}

/** Options a provider may accept in prepare(). Ignored keys are allowed. */
export interface CapturePrepareOptions {
  /** Request shared system/tab audio in addition to the microphone. */
  systemAudio?: boolean;
}

/**
 * Provider interface. Providers that can't support an operation simply don't
 * implement it — capabilities are declared explicitly, never assumed.
 * Signatures are deliberately loose: each provider prepares differently (a
 * browser needs permissions, a native integration needs OAuth tokens), while
 * `stop()` always yields the same CapturedMedia contract.
 */
export interface CaptureProvider {
  readonly sourceType: CaptureSourceType;
  readonly capabilities: CaptureCapabilities;
  /** Acquire capture resources (permissions, devices, streams). */
  prepare?(options?: CapturePrepareOptions): Promise<unknown>;
  start?(): void | Promise<void>;
  pause?(): void;
  resume?(): void;
  /** Stop capturing and return the produced media. */
  stop?(): Promise<CapturedMedia>;
  /** Abort and release all resources without producing media. */
  cancel?(): void | Promise<void>;
}
