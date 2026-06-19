# OpenNolan — Landing Page

A self-contained marketing + waitlist site for the **OpenNolan** Mac app.
Static `index.html` (no build step) + one Vercel serverless function for waitlist capture.

```
website/
├── index.html        # the whole landing page (HTML + CSS + JS, no dependencies)
├── api/
│   └── waitlist.js    # serverless: POST stores a signup, GET returns the count
├── package.json       # declares @vercel/kv for the function
├── vercel.json        # clean URLs + basic security headers
└── README.md
```

## Messaging

The copy follows the **StoryBrand SB7** framework — the customer is the hero, OpenNolan
is the guide. Hero promise: *"Make videos as fast as you think them."* The villain is the
render queue; the plan is *bring your key → direct the agent → edit at the speed of thought.*

## Local preview

The page is plain static HTML — open it directly, or for the `/api/waitlist` route to work
locally, use the Vercel CLI:

```bash
cd website
npm install
npx vercel dev      # serves index.html + /api/waitlist at http://localhost:3000
```

(Without `vercel dev`, the form will just show a network error locally — the page itself
renders fine from `open index.html`.)

## Deploy to Vercel

```bash
cd website
npx vercel            # first run links/creates the project — set the root dir to "website/"
npx vercel --prod     # ship it
```

Vercel auto-detects the static site and the `api/` function. No framework preset needed.

## Waitlist storage + confirmation email (Resend)

Signups are stored as contacts in a **Resend Audience**, and each new signup is auto-sent a
branded "you're on the waitlist" confirmation email — both handled by `api/waitlist.js` over
the Resend REST API (no SDK, no npm install). All of this fits on Resend's **free tier**
(verified 2026-06-18): 1,000 contacts, 3,000 emails/mo (100/day cap), no forced footer.

**With nothing configured, the function logs signups and returns success** so the page works
in local dev. To go live:

### One-time Resend setup (~10 min)

1. **Create a Resend account** at [resend.com](https://resend.com).
2. **Verify your sending domain.** Domains → Add Domain (e.g. `opennolan.app`), then add the
   shown **SPF + DKIM** (and ideally DMARC) records to your DNS. *This step is required* —
   until the domain is verified, Resend only lets you email your own address, not real signups.
3. **Create an Audience** (Audiences → Create). Copy its **Audience ID**.
4. **Create an API key** (API Keys → Create), with "Sending access".
5. **Set the env vars** on the Vercel project (Settings → Environment Variables), then redeploy:

```
RESEND_API_KEY=re_xxxxxxxx
RESEND_AUDIENCE_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
WAITLIST_FROM_EMAIL=OpenNolan <waitlist@opennolan.app>   # domain must match the verified one
WAITLIST_REPLY_TO=hello@opennolan.app                    # optional
WAITLIST_NOTIFY_EMAIL=you@example.com                    # optional — ping yourself per signup
```

Locally: `cp .env.example .env.local` (or set them in `.env.local`) and run `npx vercel dev`.

### How it behaves

- **New signup** → added to the Audience → confirmation email sent → `{ ok: true }`.
- **Duplicate** (already in the Audience) → no email re-sent → `{ ok: true, alreadyJoined: true }`
  (the page shows "you're already on the list").
- The `GET /api/waitlist` count powers the "N creators waiting" line (only appears once the
  list reaches 25).
- **Export your list** any time from the Resend dashboard (Audiences → … → Export) or the API.

### Notes

- The 100 emails/day free cap is plenty pre-launch; a launch-day blast to a large list would
  span multiple days or want the $20/mo Pro plan (removes the daily cap).
- The confirmation is a **transactional** send (one email per signup), so no unsubscribe is
  legally required — but the Audience also lets you send a launch broadcast later, which does.

## Before you ship — quick checklist

- [ ] **GitHub link:** in `index.html`, set `CONFIG.githubUrl` (top of the `<script>` block)
      to the public repo URL. While it's `""`, all GitHub buttons stay hidden — no broken links.
- [ ] **Resend:** verify your domain + set the 3 required env vars above (otherwise signups
      only hit the function log and no confirmation is sent).
- [ ] **Download CTA:** the "Download for Mac" button is intentionally disabled with a "Soon"
      badge. When the app ships, swap it for a real download link and flip the waitlist to
      secondary.
- [ ] **OG image (optional):** add an `og:image` meta + asset for nicer link previews.
- [ ] **Domain:** point your domain at the Vercel project (and it doubles as your Resend sender).

## Design notes

Warm, editorial, Anthropic-inspired: ivory paper, terracotta/coral accents, slate ink,
Fraunces (display serif) + Inter (UI). Fully responsive, dark-text-on-light, accessible
(semantic landmarks, labeled inputs, `prefers-reduced-motion` respected, honeypot spam guard).
No tracking scripts, no external JS dependencies.
