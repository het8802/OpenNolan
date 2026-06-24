const DEFAULT_CAPTURE_HOST = "https://us.i.posthog.com";
const DEFAULT_UI_HOST = "https://us.posthog.com";

function readPublicKey() {
  return (
    process.env.POSTHOG_KEY ||
    process.env.POSTHOG_PROJECT_KEY ||
    process.env.NEXT_PUBLIC_POSTHOG_KEY ||
    process.env.VITE_POSTHOG_KEY ||
    ""
  ).trim();
}

function safeHttpsOrigin(value, fallback) {
  if (!value) return fallback;
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.origin : fallback;
  } catch {
    return fallback;
  }
}

function inferredUiHost(captureHost) {
  if (process.env.POSTHOG_UI_HOST) {
    return safeHttpsOrigin(process.env.POSTHOG_UI_HOST, DEFAULT_UI_HOST);
  }
  if (captureHost.includes("eu.i.posthog.com")) return "https://eu.posthog.com";
  if (captureHost.includes("us.i.posthog.com")) return DEFAULT_UI_HOST;
  return undefined;
}

export default function handler(req, res) {
  if (req.method !== "GET") {
    res.setHeader("Allow", "GET");
    return res.status(405).json({ error: "Method not allowed" });
  }

  const key = readPublicKey();
  const host = safeHttpsOrigin(process.env.POSTHOG_HOST, DEFAULT_CAPTURE_HOST);

  res.setHeader("Cache-Control", "no-store");
  return res.status(200).json({
    enabled: Boolean(key),
    key: key || null,
    host,
    uiHost: inferredUiHost(host),
  });
}
