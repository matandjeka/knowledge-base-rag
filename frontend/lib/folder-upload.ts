/** Pure helpers for uploading a folder of PDF, DOCX, and CSV files as separate sources. */
import {ApiError} from "@/lib/api";

export const CONTENT_TYPES = {
  pdf: "application/pdf",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  csv: "text/csv",
} as const;
export type FileKind = keyof typeof CONTENT_TYPES;
export const FILE_KINDS = Object.keys(CONTENT_TYPES) as FileKind[];

export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
export const MAX_FILENAME_LENGTH = 200;
export const MAX_CSV_COLUMNS = 200;
/**
 * The backend allows only one active indexing job per workspace at a time (a unique row in
 * `rag_workspace_jobs`, freed on completion or explicit cancel) — see app/jobs/repository.py.
 * A folder batch must therefore create and finish one job before starting the next; this cap
 * just bounds how long a single "add a folder" run keeps the form busy.
 */
export const MAX_FOLDER_FILES = 20;
export const JOB_POLL_INTERVAL_MS = 2000;
export const JOB_POLL_TIMEOUT_MS = 3 * 60_000;
/** Access tokens last 15 minutes; refresh well before that during long batches. */
export const TOKEN_REFRESH_MS = 5 * 60_000;

export type FolderFile = {name: string; size: number; lastModified: number; webkitRelativePath?: string};

export function kindOf(name: string): FileKind | undefined {
  const lower = name.toLowerCase();
  return FILE_KINDS.find(kind => lower.endsWith(`.${kind}`));
}
export const relativePath = (file: FolderFile) => file.webkitRelativePath || file.name;
export const fingerprint = (file: FolderFile) => `${relativePath(file)}:${file.size}:${file.lastModified}`;

/** Returns why a file cannot be uploaded, or undefined when it is acceptable. */
export function precheck(file: FolderFile): string | undefined {
  if (file.size > MAX_UPLOAD_BYTES) return "Files must be 25 MB or smaller.";
  if (file.name.length > MAX_FILENAME_LENGTH) return `File names must be ${MAX_FILENAME_LENGTH} characters or fewer.`;
}

export function planFolder<T extends FolderFile>(files: T[], alreadyAdded: ReadonlySet<string>) {
  const supported = files.flatMap(file => { const kind = kindOf(file.name); return kind ? [{file, kind}] : []; })
    .sort((a, b) => relativePath(a.file).localeCompare(relativePath(b.file)));
  const fresh = supported.filter(({file}) => !alreadyAdded.has(fingerprint(file)));
  return {
    selected: fresh.slice(0, MAX_FOLDER_FILES),
    unsupported: files.length - supported.length,
    alreadyAdded: supported.length - fresh.length,
    overflow: Math.max(fresh.length - MAX_FOLDER_FILES, 0),
  };
}

/** Text columns become searchable text; everything else is kept as filterable metadata. */
export function csvColumnChoice(columns: {name: string; inferred_type: string}[]) {
  const text = columns.filter(c => c.inferred_type === "text").map(c => c.name);
  if (!text.length) return {text: columns.map(c => c.name), metadata: []};
  return {text, metadata: columns.filter(c => c.inferred_type !== "text").map(c => c.name)};
}

const defaultSleep = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms));
export type JobStatus = {status: string; error?: string};
/**
 * Polls a job until it reaches a terminal state. A job that ends "failed" still holds the
 * workspace's one job slot (the backend only frees it on "complete" or an explicit cancel), so
 * this cancels it on the caller's behalf; if that cancel itself is refused (publishing had
 * already begun) `slotFreed` comes back false and the caller should stop the batch rather than
 * create another job that is guaranteed to 409.
 */
export async function awaitJobCompletion(
  jobId: string,
  {getJob, cancelJob, sleep = defaultSleep, timeoutMs = JOB_POLL_TIMEOUT_MS, intervalMs = JOB_POLL_INTERVAL_MS}:
    {getJob: (id: string) => Promise<JobStatus>; cancelJob: (id: string) => Promise<void>; sleep?: (ms: number) => Promise<void>; timeoutMs?: number; intervalMs?: number},
): Promise<JobStatus & {slotFreed: boolean}> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const job = await getJob(jobId);
    if (job.status === "complete") return {...job, slotFreed: true};
    if (job.status === "cancelled") return {...job, slotFreed: true};
    if (job.status === "failed") {
      const slotFreed = await cancelJob(jobId).then(() => true, () => false);
      return {...job, slotFreed};
    }
    if (Date.now() >= deadline) {
      const slotFreed = await cancelJob(jobId).then(() => true, () => false);
      return {status: "failed", error: "Timed out waiting for this file to finish indexing.", slotFreed};
    }
    await sleep(intervalMs);
  }
}

/** Retries a request rejected by the server's rate limiter, waiting as long as it asks (capped). */
export async function withRateLimitRetry<T>(
  run: () => Promise<T>,
  {attempts = 4, sleep = defaultSleep, onWait}: {attempts?: number; sleep?: (ms: number) => Promise<void>; onWait?: (seconds: number) => void} = {},
): Promise<T> {
  for (let attempt = 1; ; attempt++) {
    try { return await run(); }
    catch (error) {
      if (!(error instanceof ApiError) || error.status !== 429 || attempt >= attempts) throw error;
      const seconds = Math.min(Math.max(error.retryAfter ?? 20, 1), 65);
      onWait?.(seconds);
      await sleep(seconds * 1000);
    }
  }
}
