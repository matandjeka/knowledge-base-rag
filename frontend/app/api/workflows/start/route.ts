import { timingSafeEqual } from "node:crypto";
import { start } from "workflow/api";
import { ingestion } from "@/workflows/ingest";
export async function POST(request: Request) {
  const expected = `Bearer ${process.env.WORKFLOW_SECRET || ""}`;
  const actual = request.headers.get("authorization") || "";
  if (!process.env.WORKFLOW_SECRET || expected.length !== actual.length ||
      !timingSafeEqual(Buffer.from(expected), Buffer.from(actual)))
    return Response.json({error: "Unauthorized"}, {status: 401});
  const {job_id} = await request.json();
  if (typeof job_id !== "string" ||
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(job_id))
    return Response.json({error: "Invalid job"}, {status: 400});
  const run = await start(ingestion, [job_id]);
  return Response.json({run_id: run.runId}, {status: 202});
}
