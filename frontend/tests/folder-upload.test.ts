import {test} from "node:test";
import assert from "node:assert/strict";
import {ApiError, api} from "../lib/api";
import {MAX_FOLDER_FILES, awaitJobCompletion, csvColumnChoice, fingerprint, kindOf, planFolder, precheck, withRateLimitRetry} from "../lib/folder-upload";

const file = (name: string, size = 10, path = `docs/${name}`) => ({name, size, lastModified: 1, webkitRelativePath: path});

test("kindOf matches PDF, DOCX and CSV case-insensitively", () => {
  assert.equal(kindOf("Report.PDF"), "pdf");
  assert.equal(kindOf("notes.docx"), "docx");
  assert.equal(kindOf("data.csv"), "csv");
  assert.equal(kindOf("photo.png"), undefined);
  assert.equal(kindOf(".DS_Store"), undefined);
  assert.equal(kindOf("archive.pdf.zip"), undefined);
});

test("planFolder keeps supported files in path order and reports the rest", () => {
  const plan = planFolder([file("b.pdf"), file("a.csv"), file("c.png"), file(".DS_Store")], new Set());
  assert.deepEqual(plan.selected.map(s => [s.file.name, s.kind]), [["a.csv", "csv"], ["b.pdf", "pdf"]]);
  assert.equal(plan.unsupported, 2);
  assert.equal(plan.alreadyAdded, 0);
  assert.equal(plan.overflow, 0);
});

test("planFolder skips files that were already added so re-selecting cannot duplicate them", () => {
  const files = [file("a.pdf"), file("b.pdf"), file("c.docx")];
  const plan = planFolder(files, new Set([fingerprint(files[0])]));
  assert.deepEqual(plan.selected.map(s => s.file.name), ["b.pdf", "c.docx"]);
  assert.equal(plan.alreadyAdded, 1);
});

test("planFolder caps a batch and counts the overflow, and a rerun picks up the remainder", () => {
  const files = Array.from({length: MAX_FOLDER_FILES + 5}, (_, i) => file(`f${String(i).padStart(2, "0")}.pdf`));
  const first = planFolder(files, new Set());
  assert.equal(first.selected.length, MAX_FOLDER_FILES);
  assert.equal(first.overflow, 5);
  const done = new Set(first.selected.map(s => fingerprint(s.file)));
  const second = planFolder(files, done);
  assert.equal(second.selected.length, 5);
  assert.equal(second.overflow, 0);
  assert.equal(second.alreadyAdded, MAX_FOLDER_FILES);
});

test("precheck rejects oversized files and over-long names", () => {
  assert.equal(precheck(file("ok.pdf")), undefined);
  assert.match(precheck(file("big.pdf", 25 * 1024 * 1024 + 1))!, /25 MB/);
  assert.match(precheck(file("x".repeat(201) + ".pdf"))!, /200 characters/);
});

test("csvColumnChoice indexes text columns and keeps other columns as metadata", () => {
  const choice = csvColumnChoice([{name: "id", inferred_type: "integer"}, {name: "notes", inferred_type: "text"}, {name: "when", inferred_type: "date"}]);
  assert.deepEqual(choice, {text: ["notes"], metadata: ["id", "when"]});
});

test("csvColumnChoice falls back to every column when none is text", () => {
  const choice = csvColumnChoice([{name: "a", inferred_type: "integer"}, {name: "b", inferred_type: "decimal"}]);
  assert.deepEqual(choice, {text: ["a", "b"], metadata: []});
});

test("awaitJobCompletion returns as soon as a job completes, without cancelling it", async () => {
  let cancelled = false;
  const result = await awaitJobCompletion("job-1", {
    getJob: async () => ({status: "complete"}),
    cancelJob: async () => { cancelled = true; },
    sleep: async () => {},
  });
  assert.deepEqual(result, {status: "complete", slotFreed: true});
  assert.equal(cancelled, false);
});

test("awaitJobCompletion cancels a failed job to free the workspace's job slot", async () => {
  const cancelled: string[] = [];
  const result = await awaitJobCompletion("job-2", {
    getJob: async () => ({status: "failed", error: "Could not parse this file."}),
    cancelJob: async id => { cancelled.push(id); },
    sleep: async () => {},
  });
  assert.deepEqual(result, {status: "failed", error: "Could not parse this file.", slotFreed: true});
  assert.deepEqual(cancelled, ["job-2"]);
});

test("awaitJobCompletion reports the slot as stuck when a failed job refuses to cancel", async () => {
  const result = await awaitJobCompletion("job-3", {
    getJob: async () => ({status: "failed", error: "Boom"}),
    cancelJob: async () => { throw new Error("Publication has begun; allow this job to finish."); },
    sleep: async () => {},
  });
  assert.equal(result.slotFreed, false);
});

test("awaitJobCompletion polls until terminal and gives up (freeing the slot) after its timeout", async () => {
  const statuses = ["queued", "running", "running"];
  let now = 0;
  const result = await awaitJobCompletion("job-4", {
    getJob: async () => ({status: statuses.shift() ?? "running"}),
    cancelJob: async () => {},
    sleep: async ms => { now += ms; },
    timeoutMs: 20,
    intervalMs: 10,
  });
  assert.equal(result.status, "failed");
  assert.match(result.error!, /Timed out/);
  assert.equal(result.slotFreed, true);
  assert.ok(now >= 20);
});

test("withRateLimitRetry waits for Retry-After and then succeeds", async () => {
  let calls = 0;
  const waits: number[] = [];
  const result = await withRateLimitRetry(async () => {
    if (++calls < 3) throw new ApiError("Too many requests.", 429, 7);
    return "ok";
  }, {sleep: async ms => { waits.push(ms); }, onWait: () => {}});
  assert.equal(result, "ok");
  assert.deepEqual(waits, [7000, 7000]);
});

test("withRateLimitRetry gives up after the attempt limit and never retries other errors", async () => {
  let limited = 0;
  await assert.rejects(withRateLimitRetry(async () => { limited++; throw new ApiError("Too many requests.", 429, 1); }, {attempts: 3, sleep: async () => {}}), /Too many requests/);
  assert.equal(limited, 3);
  let other = 0;
  await assert.rejects(withRateLimitRetry(async () => { other++; throw new ApiError("Bad", 422); }, {sleep: async () => {}}), /Bad/);
  assert.equal(other, 1);
});

test("api surfaces the Retry-After header and readable validation messages", async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = (async () => new Response(JSON.stringify({detail: "Too many requests. Please slow down."}), {status: 429, headers: {"Retry-After": "12"}})) as typeof fetch;
    await assert.rejects(api("jobs"), (e: unknown) => e instanceof ApiError && e.status === 429 && e.retryAfter === 12);
    globalThis.fetch = (async () => new Response(JSON.stringify({detail: [{loc: ["body", "text_columns"], msg: "List should have at most 200 items"}]}), {status: 422})) as typeof fetch;
    await assert.rejects(api("jobs"), /text_columns: List should have at most 200 items/);
    globalThis.fetch = (async () => new Response("{}", {status: 500})) as typeof fetch;
    await assert.rejects(api("jobs"), /Please check the form and try again/);
  } finally { globalThis.fetch = original; }
});
