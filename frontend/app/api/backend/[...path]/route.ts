import { apiUrl, allowedOrigin, upstreamHeaders } from "@/lib/server";
export const maxDuration = 300;
const roots = new Set(["auth", "sources", "query", "settings", "evaluations", "jobs", "uploads", "graph", "health"]);
async function proxy(request: Request, context: {params: Promise<{path: string[]}>}) {
  const {path} = await context.params;
  if (!roots.has(path[0]) || path.some(p => p.includes("..") || p.includes("/") || p.includes("\\")))
    return Response.json({detail: "Not found"}, {status: 404});
  if (!allowedOrigin(request)) return Response.json({detail: "Origin rejected"}, {status: 403});
  const headers = new Headers(upstreamHeaders());
  for (const key of ["authorization", "cookie", "content-type", "origin", "sec-fetch-site"])
    if (request.headers.has(key)) headers.set(key, request.headers.get(key)!);
  try {
    const response = await fetch(apiUrl(path.join("/")) + new URL(request.url).search, {
      method: request.method, headers, cache: "no-store", redirect: "manual",
      body: ["GET", "HEAD"].includes(request.method) ? undefined : await request.arrayBuffer(),
      signal: AbortSignal.timeout(240_000),
    });
    const returned = new Headers({"Content-Type": response.headers.get("content-type") || "application/json", "Cache-Control": "no-store"});
    for (const value of response.headers.getSetCookie())
      returned.append("Set-Cookie", value.replace("Path=/api/auth", "Path=/api/backend/auth"));
    return new Response(response.body, {status: response.status, headers: returned});
  } catch {
    return Response.json({detail: "The service is unavailable. Please try again."}, {status: 503});
  }
}
export {proxy as GET, proxy as POST, proxy as DELETE};
