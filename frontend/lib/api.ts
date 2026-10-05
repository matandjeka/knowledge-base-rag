/** Session access tokens remain in memory; refresh credentials use HttpOnly cookies. */
export type User = {id: string; email: string; workspace_id: string};
export type Session = {access_token: string; user: User};
let token: string | undefined;
let refreshing: Promise<Session> | undefined;
export function setSession(session?: Session) { token = session?.access_token; }
export function accessToken() { return token; }
export async function restore(): Promise<Session> {
  if (!refreshing) refreshing = (async () => {
    const run = async () => {
      const response = await fetch("/api/backend/auth/refresh", {method: "POST"});
      if (!response.ok) throw new Error("Sign in to continue.");
      const session = await response.json() as Session;
      setSession(session);
      return session;
    };
    return typeof navigator !== "undefined" && navigator.locks ? navigator.locks.request("rag-refresh", run) : run();
  })().finally(() => {refreshing = undefined;});
  return refreshing;
}
export class ApiError extends Error {
  status: number;
  retryAfter?: number;
  constructor(message: string, status: number, retryAfter?: number) { super(message); this.status = status; this.retryAfter = retryAfter; }
}
function describe(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const issues = detail.filter((i): i is {msg: string; loc?: unknown[]} => typeof i?.msg === "string").slice(0, 2).map(i => {
      const field = Array.isArray(i.loc) ? i.loc.filter(p => p !== "body").join(".") : "";
      return field ? `${field}: ${i.msg}` : i.msg;
    });
    if (issues.length) return issues.join("; ");
  }
  return "Please check the form and try again.";
}
export async function api<T>(path: string, options: RequestInit = {}, retry = true): Promise<T> {
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(`/api/backend/${path}`, {...options, headers, cache: "no-store"});
  if (response.status === 401 && retry && !path.startsWith("auth/")) {
    await restore(); return api<T>(path, options, false);
  }
  let body: unknown = undefined;
  const text = await response.text();
  if (text) { try { body = JSON.parse(text); } catch { body = undefined; } }
  const detail = (body as {detail?: unknown})?.detail;
  if (!response.ok) {
    const retryAfter = Number(response.headers.get("Retry-After"));
    throw new ApiError(describe(detail), response.status, Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : undefined);
  }
  return body as T;
}
export function post<T>(path: string, body: unknown): Promise<T> {
  return api<T>(path, {method: "POST", body: JSON.stringify(body)});
}
export function del(path: string): Promise<void> {
  return api<void>(path, {method: "DELETE"});
}
