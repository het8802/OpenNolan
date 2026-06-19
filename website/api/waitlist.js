// OpenNolan — waitlist capture via Resend (Vercel serverless function)
//
// POST /api/waitlist  { email, ref?, company? }
//   → stores the signup as a contact in a Resend Audience
//   → sends a branded "you're on the waitlist" confirmation email
// GET  /api/waitlist
//   → { count }  (best-effort, for the social-proof line)
//
// Setup (all free on Resend's free tier — see README.md):
//   RESEND_API_KEY        (required) — your Resend API key
//   RESEND_AUDIENCE_ID    (required to store) — the Audience signups are added to
//   WAITLIST_FROM_EMAIL   (required to send)  — e.g. "OpenNolan <waitlist@opennolan.app>"
//                          The domain MUST be verified in Resend (SPF/DKIM DNS).
//   WAITLIST_REPLY_TO     (optional) — reply-to address
//   WAITLIST_NOTIFY_EMAIL (optional) — get a heads-up email on every new signup
//
// With nothing configured, the function logs signups and returns success so the
// page still works in local dev. Uses global fetch (Vercel Node 18+) — no deps.

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const RESEND_API = "https://api.resend.com";

function config() {
  return {
    apiKey: process.env.RESEND_API_KEY,
    audienceId: process.env.RESEND_AUDIENCE_ID,
    from: process.env.WAITLIST_FROM_EMAIL,
    replyTo: process.env.WAITLIST_REPLY_TO || undefined,
    notify: process.env.WAITLIST_NOTIFY_EMAIL || undefined,
  };
}

async function resend(apiKey, path, { method = "GET", body } = {}) {
  const res = await fetch(`${RESEND_API}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try {
    const text = await res.text();
    data = text ? JSON.parse(text) : null;
  } catch {
    /* non-JSON body */
  }
  return { ok: res.ok, status: res.status, data };
}

function readBody(req) {
  if (req.body && typeof req.body === "object") return req.body;
  if (typeof req.body === "string") {
    try {
      return JSON.parse(req.body);
    } catch {
      return {};
    }
  }
  return {};
}

function confirmationEmail() {
  const subject = "You're on the OpenNolan waitlist 🎬";
  const text =
    "You're on the list.\n\n" +
    "Thanks for joining the OpenNolan waitlist. You'll be the first to know the " +
    "moment the Mac app is ready to download — local-first AI video that renders " +
    "once and edits instantly, on your machine, with your own key.\n\n" +
    "We'll only email you about the launch.\n\n— The OpenNolan team";

  const html = `<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#FBF8F1;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#FBF8F1;">
    <tr><td align="center" style="padding:40px 16px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;background:#FFFDF8;border:1px solid rgba(32,28,23,0.10);border-radius:18px;overflow:hidden;">
        <tr><td style="padding:36px 36px 8px;">
          <table role="presentation" cellpadding="0" cellspacing="0"><tr>
            <td style="width:34px;height:34px;background:#D9694A;border-radius:9px;text-align:center;vertical-align:middle;font-size:18px;color:#FBF8F1;">&#9658;</td>
            <td style="padding-left:11px;font-family:Georgia,'Times New Roman',serif;font-size:21px;font-weight:600;color:#201C17;">OpenNolan</td>
          </tr></table>
        </td></tr>
        <tr><td style="padding:20px 36px 0;">
          <h1 style="margin:0;font-family:Georgia,'Times New Roman',serif;font-size:30px;line-height:1.1;color:#201C17;font-weight:600;">You're on the list.</h1>
        </td></tr>
        <tr><td style="padding:18px 36px 0;">
          <p style="margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;font-size:16px;line-height:1.65;color:#524B43;">
            Thanks for joining the waitlist. You'll be the first to know the moment <strong style="color:#201C17;">OpenNolan</strong> is ready to download — local-first AI video that renders once and edits instantly, on your Mac, with your own key.
          </p>
        </td></tr>
        <tr><td style="padding:24px 36px 0;">
          <p style="margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;font-size:15px;line-height:1.6;color:#8C8378;">
            We'll only email you about the launch. No spam, ever.
          </p>
        </td></tr>
        <tr><td style="padding:28px 36px 36px;">
          <div style="border-top:1px solid rgba(32,28,23,0.10);padding-top:18px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;font-size:13px;color:#A39A8E;">
            — The OpenNolan team &nbsp;·&nbsp; <span style="color:#BF4E2E;">Render once, edit cheap.</span>
          </div>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>`;

  return { subject, text, html };
}

export default async function handler(req, res) {
  const { apiKey, audienceId, from, replyTo, notify } = config();

  // ---- GET: best-effort count for social proof ----
  if (req.method === "GET") {
    if (!apiKey || !audienceId) return res.status(200).json({ count: null });
    try {
      const r = await resend(apiKey, `/audiences/${audienceId}/contacts`);
      const list = r.ok && Array.isArray(r.data?.data) ? r.data.data : null;
      return res.status(200).json({ count: list ? list.length : null });
    } catch {
      return res.status(200).json({ count: null });
    }
  }

  if (req.method !== "POST") {
    res.setHeader("Allow", "GET, POST");
    return res.status(405).json({ error: "Method not allowed" });
  }

  const { email: rawEmail, company } = readBody(req);

  // Honeypot: bots fill the hidden "company" field. Pretend success, store nothing.
  if (company) return res.status(200).json({ ok: true });

  const email = String(rawEmail || "").trim().toLowerCase();
  if (!EMAIL_RE.test(email) || email.length > 254) {
    return res.status(400).json({ error: "Please enter a valid email address." });
  }

  // ---- Dev fallback: nothing configured ----
  if (!apiKey) {
    console.log("[waitlist] (no RESEND_API_KEY) signup:", email);
    return res.status(200).json({ ok: true, stored: false });
  }

  let alreadyJoined = false;

  // ---- Store the contact in the Resend Audience (and dedupe) ----
  if (audienceId) {
    try {
      // Resend's Get Contact accepts the email as the {id} path param → 200 if it exists, 404 if not.
      const existing = await resend(
        apiKey,
        `/audiences/${audienceId}/contacts/${encodeURIComponent(email)}`
      );
      if (existing.ok && (existing.data?.id || existing.data?.email)) {
        alreadyJoined = true;
      } else {
        const created = await resend(apiKey, `/audiences/${audienceId}/contacts`, {
          method: "POST",
          body: { email, unsubscribed: false },
        });
        if (!created.ok) {
          console.error("[waitlist] create contact failed:", created.status, created.data);
        }
      }
    } catch (err) {
      console.error("[waitlist] audience error:", err?.message || err);
    }
  }

  // ---- Send the confirmation email (only for genuinely new signups) ----
  if (!alreadyJoined && from) {
    try {
      const mail = confirmationEmail();
      const sent = await resend(apiKey, `/emails`, {
        method: "POST",
        body: {
          from,
          to: [email],
          reply_to: replyTo,
          subject: mail.subject,
          html: mail.html,
          text: mail.text,
        },
      });
      if (!sent.ok) {
        console.error("[waitlist] confirmation send failed:", sent.status, sent.data);
      }
    } catch (err) {
      console.error("[waitlist] send error:", err?.message || err);
    }

    // Optional owner notification (best-effort, never blocks the signup)
    if (notify) {
      try {
        await resend(apiKey, `/emails`, {
          method: "POST",
          body: {
            from,
            to: [notify],
            subject: "New OpenNolan waitlist signup",
            text: `${email} just joined the waitlist.`,
          },
        });
      } catch (err) {
        console.error("[waitlist] notify error:", err?.message || err);
      }
    }
  }

  return res.status(200).json({ ok: true, alreadyJoined });
}
