import { timingSafeEqual } from "node:crypto";

const SUBJECTS: Record<string, string> = {
  verify: "Confirm your email",
  reset: "Reset your password",
  invite: "You've been invited",
};

const BODIES: Record<string, (url: string) => string> = {
  verify: (url) =>
    `<p>Confirm your email address to finish creating your account.</p><p><a href="${url}">Confirm email</a></p><p>This link expires in one hour.</p>`,
  reset: (url) =>
    `<p>Use the link below to reset your password.</p><p><a href="${url}">Reset password</a></p><p>This link expires in one hour. If you did not request this, ignore this email.</p>`,
  invite: (url) =>
    `<p>You've been invited to join an organization.</p><p><a href="${url}">Accept invitation</a></p><p>This link expires in one hour.</p>`,
};

export async function POST(request: Request) {
  const expected = `Bearer ${process.env.MAIL_WEBHOOK_SECRET || ""}`;
  const actual = request.headers.get("authorization") || "";
  if (
    !process.env.MAIL_WEBHOOK_SECRET ||
    expected.length !== actual.length ||
    !timingSafeEqual(Buffer.from(expected), Buffer.from(actual))
  ) {
    return Response.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { to, template, url } = await request.json();
  if (
    typeof to !== "string" ||
    typeof url !== "string" ||
    typeof template !== "string" ||
    !(template in SUBJECTS)
  ) {
    return Response.json({ error: "Invalid mail request" }, { status: 400 });
  }

  const apiKey = process.env.RESEND_API_KEY;
  if (!apiKey) return Response.json({ error: "Mail is not configured" }, { status: 503 });

  const response = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      from: process.env.MAIL_FROM_ADDRESS || "onboarding@resend.dev",
      to,
      subject: SUBJECTS[template],
      html: BODIES[template](url),
    }),
  });
  if (!response.ok) return Response.json({ error: "Mail delivery failed" }, { status: 502 });
  return Response.json({ accepted: true });
}
