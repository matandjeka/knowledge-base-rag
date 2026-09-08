/** Server-only upstream configuration and authenticated identity lookup. */
export function apiUrl(path: string): string {
  const base = process.env.RAG_API_URL;
  if (!base) throw new Error("RAG_API_URL is not configured");
  return `${base.replace(/\/$/, "")}/${path.replace(/^\//, "")}`;
}
export function upstreamHeaders(): Record<string, string> {
  return process.env.VERCEL_AUTOMATION_BYPASS_SECRET
    ? {"x-vercel-protection-bypass": process.env.VERCEL_AUTOMATION_BYPASS_SECRET} : {};
}
export async function identity(request: Request): Promise<{id: string; workspace_id: string}> {
  const authorization = request.headers.get("authorization");
  if (!authorization?.startsWith("Bearer ")) throw new Error("Sign in to continue");
  const response = await fetch(apiUrl("auth/me"), {
    headers: {...upstreamHeaders(), authorization}, cache: "no-store",
  });
  if (!response.ok) throw new Error("Session is invalid or expired");
  return response.json();
}
export function allowedOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  return (!origin || origin === process.env.FRONTEND_ORIGIN || origin === new URL(request.url).origin)
    && request.headers.get("sec-fetch-site") !== "cross-site";
}
