import { FatalError } from "workflow";
import { apiUrl, upstreamHeaders } from "@/lib/server";
export async function ingestion(jobId: string) {
  "use workflow";
  let step = 0;
  try {
    while (true) {
      const result = await advance(jobId, step);
      if (["complete", "cancelled"].includes(result.status)) return;
      step = result.step;
    }
  } catch (error) {
    await markFailed(jobId);
    throw error;
  }
}
async function advance(jobId: string, step: number): Promise<{status: string; step: number}> {
  "use step";
  const response = await fetch(apiUrl(`internal/jobs/${jobId}/step`), {
    method: "POST", headers: {...upstreamHeaders(), "Content-Type": "application/json",
      Authorization: `Bearer ${process.env.WORKFLOW_SECRET}`},
    body: JSON.stringify({step}), signal: AbortSignal.timeout(235_000),
  });
  if (response.status >= 400 && response.status < 500) throw new FatalError("Job was rejected");
  if (!response.ok) throw new Error("Processing step failed");
  return response.json();
}
advance.maxRetries = 5;
async function markFailed(jobId: string) {
  "use step";
  const response = await fetch(apiUrl(`internal/jobs/${jobId}/failed`), {
    method: "POST", headers: {...upstreamHeaders(), Authorization: `Bearer ${process.env.WORKFLOW_SECRET}`},
  });
  if (!response.ok) throw new Error("Could not record job failure");
}
