import { issueSignedToken, presignUrl } from "@vercel/blob";
import { identity, apiUrl, upstreamHeaders } from "@/lib/server";
export async function GET(request: Request) {
  try {
    const user = await identity(request);
    const sourceId = new URL(request.url).searchParams.get("source_id");
    if (!sourceId || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(sourceId))
      return Response.json({error: "Invalid source"}, {status: 400});
    const response = await fetch(apiUrl(`sources?workspace_id=${user.workspace_id}`), {
      headers: {...upstreamHeaders(), authorization: request.headers.get("authorization")!}, cache: "no-store",
    });
    if (!response.ok) return Response.json({error: "Access denied"}, {status: 403});
    const sources = await response.json() as {source_id: string; config: {source_type: string}}[];
    const source = sources.find(s => s.source_id === sourceId);
    if (!source || !["pdf", "csv"].includes(source.config.source_type)) return Response.json({error: "Source not found"}, {status: 404});
    const pathname = `workspaces/${user.workspace_id}/sources/${sourceId}/original.${source.config.source_type}`;
    const validUntil = Date.now() + 60_000;
    const token = await issueSignedToken({pathname, operations: ["get"], validUntil});
    const {presignedUrl} = await presignUrl(token, {operation: "get", pathname, access: "private", validUntil});
    return Response.json({url: presignedUrl}, {headers: {"Cache-Control": "no-store"}});
  } catch {
    return Response.json({error: "Download authorization failed"}, {status: 403});
  }
}
