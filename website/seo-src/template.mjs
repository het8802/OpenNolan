/* OpenNolan — HTML template for programmatic / content pages.
   Pure render functions. No I/O. generate.mjs feeds these validated page data. */

const BASE = "https://www.opennolan.com";
const GITHUB = "https://github.com/het8802/OpenNolan";
const OG_IMAGE = BASE + "/assets/og-image.png";

/* ----- tiny helpers ----- */
export function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
/* inline markdown subset on already-trusted-but-escaped text: **bold** and [text](/path) */
export function inline(s) {
  let out = esc(s);
  out = out.replace(/\[([^\]]+)\]\((\/[^)\s]+|https?:\/\/[^)\s]+)\)/g,
    (_m, t, href) => `<a href="${href}">${t}</a>`);
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  return out;
}
/* strip the inline-markdown subset to plain text (for JSON-LD values) */
export function stripMd(s) {
  return String(s == null ? "" : s)
    .replace(/\[([^\]]+)\]\((?:\/[^)\s]+|https?:\/\/[^)\s]+)\)/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1");
}
const paras = (arr) => (arr || []).map((p) => `<p>${inline(p)}</p>`).join("\n        ");
const url = (cluster, slug) => `/${cluster}/${slug}`;

/* ----- shared chrome ----- */
function head({ title, description, canonicalPath, jsonld }) {
  const canonical = BASE + canonicalPath;
  return `  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>${esc(title)}</title>
  <meta name="description" content="${esc(description)}" />
  <link rel="canonical" href="${canonical}" />
  <meta name="robots" content="index, follow" />
  <meta property="og:type" content="website" />
  <meta property="og:url" content="${canonical}" />
  <meta property="og:title" content="${esc(title)}" />
  <meta property="og:description" content="${esc(description)}" />
  <meta property="og:site_name" content="OpenNolan" />
  <meta property="og:image" content="${OG_IMAGE}" />
  <meta property="og:image:width" content="1200" />
  <meta property="og:image:height" content="630" />
  <meta property="og:image:alt" content="OpenNolan — the Mac app that turns your raw clips into a finished, scroll-stopping Reel or TikTok." />
  <meta name="twitter:card" content="summary_large_image" />
  <meta name="twitter:title" content="${esc(title)}" />
  <meta name="twitter:description" content="${esc(description)}" />
  <meta name="twitter:image" content="${OG_IMAGE}" />
  <meta name="theme-color" content="#FBF8F1" />
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%23D9694A'/%3E%3Cpath d='M13 10.5l9 5.5-9 5.5z' fill='%23FBF8F1'/%3E%3C/svg%3E" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600;9..144,700&family=Inter:wght@400;450;500;600;700&display=swap" rel="stylesheet" />
  <link rel="stylesheet" href="/assets/site.css" />
  <script type="application/ld+json">
${JSON.stringify(jsonld, null, 2)}
  </script>`;
}

function nav() {
  return `  <header class="nav" id="nav">
    <div class="wrap nav-inner">
      <a href="/" class="brand" aria-label="OpenNolan home">
        <svg class="mark" viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="9" fill="#D9694A"/><path d="M13 10.2l9.2 5.8-9.2 5.8z" fill="#FBF8F1"/></svg>
        OpenNolan
      </a>
      <nav class="nav-links">
        <a href="/for" class="navlink">Built for</a>
        <a href="/compare" class="navlink">Compare</a>
        <a href="/how-to" class="navlink">Guides</a>
        <a href="${GITHUB}" class="navlink" data-github hidden>GitHub</a>
      </nav>
      <div class="nav-cta">
        <a href="${GITHUB}" class="btn btn-ghost" data-github hidden style="padding:10px 16px;">GitHub</a>
        <a href="#waitlist" class="btn btn-primary">Join the waitlist</a>
      </div>
    </div>
  </header>`;
}

function crumbs(items) {
  const lis = items.map((c, i) => {
    if (i === items.length - 1) return `<li aria-current="page">${esc(c.name)}</li>`;
    return `<li><a href="${c.path}">${esc(c.name)}</a></li>`;
  }).join("");
  return `  <nav class="crumbs" aria-label="Breadcrumb"><div class="wrap"><ol>${lis}</ol></div></nav>`;
}

function waitlistForm(placement, center) {
  const c = center ? " center" : "";
  return `<form class="waitlist-form${c}" data-analytics-placement="${esc(placement)}" novalidate>
          <div class="field-row">
            <input type="email" name="email" placeholder="you@startup.com" autocomplete="email" required aria-label="Email address" />
            <input type="text" name="company" class="hp" tabindex="-1" autocomplete="off" aria-hidden="true" />
            <button type="submit" class="btn btn-primary">Join the waitlist</button>
          </div>
          <p class="form-note${c}">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>
            Be first when the Mac app ships. No spam — just the launch.
          </p>
          <p class="form-status" role="status" aria-live="polite"></p>
        </form>
        <div class="form-success-card${c}" role="status">
          <p class="fs-h">You're on the list 🎬</p>
          <p>Check your inbox — we just sent a confirmation. We'll email you the moment OpenNolan is ready to download.</p>
        </div>`;
}

function finalCta(heading, sub, placement) {
  return `  <section class="cta-final" id="waitlist">
    <div class="wrap">
      <div class="cta-card reveal">
        <span class="eyebrow" style="display:inline-flex;">Coming soon to Mac</span>
        <h2>${esc(heading)}</h2>
        <p>${esc(sub)}</p>
        ${waitlistForm(placement, true)}
      </div>
    </div>
  </section>`;
}

function footer() {
  return `  <footer>
    <div class="wrap">
      <div class="foot-grid">
        <div class="foot-brand">
          <a href="/" class="brand" style="font-size:20px;">
            <svg class="mark" viewBox="0 0 32 32" aria-hidden="true" style="width:28px;height:28px;"><rect width="32" height="32" rx="9" fill="#D9694A"/><path d="M13 10.2l9.2 5.8-9.2 5.8z" fill="#FBF8F1"/></svg>
            OpenNolan
          </a>
          <p>The Mac app that makes the whole reel for you — drop in your clips, the AI agent does the rest.</p>
          <span class="badge-progress" style="margin-top:16px;"><span class="dot-live"></span> In active development</span>
        </div>
        <div class="foot-cols">
          <div class="foot-col">
            <h3>Product</h3>
            <a href="/#problem">Why OpenNolan</a>
            <a href="/#playbook">The playbook</a>
            <a href="/#features">Features</a>
            <a href="/#waitlist">Join the waitlist</a>
          </div>
          <div class="foot-col">
            <h3>Built for</h3>
            <a href="/for/founders">Founders</a>
            <a href="/for/indie-hackers">Indie hackers</a>
            <a href="/for/saas">SaaS</a>
            <a href="/for">All audiences →</a>
          </div>
          <div class="foot-col">
            <h3>Compare</h3>
            <a href="/compare/capcut">vs CapCut</a>
            <a href="/compare/descript">vs Descript</a>
            <a href="/compare/opus-clip">vs Opus Clip</a>
            <a href="/compare">All comparisons →</a>
          </div>
          <div class="foot-col">
            <h3>Resources</h3>
            <a href="/how-to">How-to guides</a>
            <a href="/learn">Glossary</a>
            <a href="${GITHUB}" data-github hidden>GitHub</a>
          </div>
        </div>
      </div>
      <div class="foot-bottom">
        <span>© <span id="year">2026</span> OpenNolan. Open source.</span>
        <span>The Mac app is in active development — join the waitlist for launch.</span>
      </div>
    </div>
  </footer>`;
}

function scripts() {
  return `  <script src="/assets/posthog.js"></script>
  <script src="/assets/waitlist.js"></script>
  <script defer src="/_vercel/insights/script.js"></script>`;
}

function htmlDoc({ title, description, canonicalPath, jsonld, body }) {
  return `<!DOCTYPE html>
<html lang="en">
<head>
${head({ title, description, canonicalPath, jsonld })}
</head>
<body>
${nav()}
${body}
${footer()}
${scripts()}
</body>
</html>
`;
}

/* ----- section renderers ----- */
function h1WithAccent(h1, accent) {
  if (accent && h1.includes(accent)) {
    return esc(h1).replace(esc(accent), `<span class="accent">${esc(accent)}</span>`);
  }
  return esc(h1);
}

function renderSections(sections) {
  return (sections || []).map((s) => {
    const parts = [];
    if (s.h2) parts.push(`<h2>${esc(s.h2)}</h2>`);
    if (s.quote) parts.push(`<p class="lead-quote">${inline(s.quote)}</p>`);
    if (s.body && s.body.length) parts.push(paras(s.body));
    if (s.checks && s.checks.length) {
      parts.push(`<ul class="checks">\n          ${s.checks.map((c) => `<li>${inline(c)}</li>`).join("\n          ")}\n        </ul>`);
    }
    return `      <section class="reveal">\n        ${parts.join("\n        ")}\n      </section>`;
  }).join("\n");
}

function renderComparison(cmp) {
  if (!cmp || !cmp.rows || !cmp.rows.length) return "";
  const head = `<thead><tr><th>&nbsp;</th><th>OpenNolan</th><th>${esc(cmp.competitor)}</th></tr></thead>`;
  const rows = cmp.rows.map((r) =>
    `<tr><td>${esc(r.feature)}</td><td class="on">${inline(r.openNolan)}</td><td class="on">${inline(r.competitor)}</td></tr>`
  ).join("\n            ");
  const intro = cmp.intro ? `<p>${inline(cmp.intro)}</p>\n        ` : "";
  return `      <section class="reveal">
        <h2>OpenNolan vs ${esc(cmp.competitor)}, at a glance</h2>
        ${intro}<div class="cmp-wrap">
          <table class="cmp">
            ${head}
            <tbody>
            ${rows}
            </tbody>
          </table>
        </div>
      </section>`;
}

function renderSteps(steps, heading) {
  if (!steps || !steps.length) return "";
  const items = steps.map((s) =>
    `<li><h3>${esc(s.title)}</h3><p>${inline(s.body)}</p></li>`
  ).join("\n          ");
  return `      <section class="reveal">
        <h2>${esc(heading || "How it works")}</h2>
        <ol class="steps-list">
          ${items}
        </ol>
      </section>`;
}

function renderFaq(faqs) {
  if (!faqs || !faqs.length) return "";
  const items = faqs.map((f) =>
    `<details><summary>${esc(f.q)}</summary><p>${inline(f.a)}</p></details>`
  ).join("\n          ");
  return `      <section class="reveal">
        <h2>Frequently asked questions</h2>
        <div class="faq">
          ${items}
        </div>
      </section>`;
}

function renderRelated(related) {
  if (!related || !related.length) return "";
  const cards = related.map((r) =>
    `<a class="rel-card" href="${r.path}"><span class="rl">${esc(r.kicker)}</span><span class="rt">${esc(r.title)}</span><span class="rd">${esc(r.desc)}</span></a>`
  ).join("\n          ");
  return `  <section class="related reveal">
    <div class="wrap read">
      <h2>Keep exploring</h2>
      <p class="sub">More ways OpenNolan helps you ship short-form that gets seen.</p>
      <div class="rel-grid">
          ${cards}
      </div>
    </div>
  </section>`;
}

/* ----- JSON-LD ----- */
function jsonldFor(data, canonicalPath, breadcrumb) {
  const graph = [
    {
      "@type": "SoftwareApplication",
      name: "OpenNolan",
      applicationCategory: "MultimediaApplication",
      operatingSystem: "macOS",
      description: data.metaDescription,
      url: BASE + canonicalPath,
      image: OG_IMAGE,
      offers: { "@type": "Offer", price: "0", priceCurrency: "USD" },
    },
    {
      "@type": "BreadcrumbList",
      itemListElement: breadcrumb.map((c, i) => ({
        "@type": "ListItem",
        position: i + 1,
        name: c.name,
        item: BASE + (c.path || canonicalPath),
      })),
    },
  ];
  if (data.faqs && data.faqs.length) {
    graph.push({
      "@type": "FAQPage",
      mainEntity: data.faqs.map((f) => ({
        "@type": "Question",
        name: f.q,
        acceptedAnswer: { "@type": "Answer", text: stripMd(f.a) },
      })),
    });
  }
  // HowTo for guide pages that define a step sequence (rich results + AI extraction)
  if (data.cluster === "how-to" && Array.isArray(data.steps) && data.steps.length) {
    graph.push({
      "@type": "HowTo",
      name: data.h1,
      description: data.metaDescription,
      image: OG_IMAGE,
      step: data.steps.map((s, i) => ({
        "@type": "HowToStep",
        position: i + 1,
        name: s.title,
        text: stripMd(s.body),
      })),
    });
  }
  // DefinedTerm for glossary pages (strong answer-engine signal for "what is X")
  if (data.cluster === "learn") {
    graph.push({
      "@type": "DefinedTerm",
      name: data.term || data.targetKeyword,
      description: data.metaDescription,
      url: BASE + canonicalPath,
      inDefinedTermSet: {
        "@type": "DefinedTermSet",
        name: "OpenNolan short-form video glossary",
        url: BASE + "/learn",
      },
    });
  }
  return { "@context": "https://schema.org", "@graph": graph };
}

/* ----- public: render a spoke page ----- */
export function renderPage(data, ctx) {
  const canonicalPath = url(data.cluster, data.slug);
  const breadcrumb = [
    { name: "Home", path: "/" },
    { name: ctx.cluster.crumb, path: "/" + data.cluster },
    { name: data.breadcrumbLabel || data.eyebrow || data.slug, path: canonicalPath },
  ];
  const jsonld = jsonldFor(data, canonicalPath, breadcrumb);

  const body = `  <main id="top">
${crumbs(breadcrumb)}
    <section class="page-hero">
      <div class="wrap read">
        <span class="eyebrow">${esc(data.eyebrow)}</span>
        <h1>${h1WithAccent(data.h1, data.h1Accent)}</h1>
        <p class="lede">${inline(data.lede)}</p>
        ${waitlistForm(ctx.placement, false)}
        <div class="hero-actions">
          <a href="/#features" class="btn btn-ghost">See it in action</a>
        </div>
      </div>
    </section>

    <div class="prose">
      <div class="wrap read">
        <section class="reveal">
        ${paras(data.intro)}
        </section>
${renderSections(data.sections)}
${data.comparison ? renderComparison(data.comparison) : ""}
${data.steps ? renderSteps(data.steps, data.stepsHeading) : ""}
${renderFaq(data.faqs)}
      </div>
    </div>
${renderRelated(ctx.related)}
  </main>
${finalCta(data.ctaHeading, data.ctaSub, ctx.placement)}`;

  return htmlDoc({
    title: data.title,
    description: data.metaDescription,
    canonicalPath,
    jsonld,
    body,
  });
}

/* ----- public: render a cluster hub page ----- */
export function renderHub(cluster, items) {
  const canonicalPath = "/" + cluster.slug;
  const breadcrumb = [
    { name: "Home", path: "/" },
    { name: cluster.crumb, path: canonicalPath },
  ];
  const jsonld = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "CollectionPage",
        name: cluster.hubTitle,
        description: cluster.hubMeta,
        url: BASE + canonicalPath,
      },
      {
        "@type": "BreadcrumbList",
        itemListElement: breadcrumb.map((c, i) => ({
          "@type": "ListItem", position: i + 1, name: c.name, item: BASE + c.path,
        })),
      },
    ],
  };
  const cards = items.map((it) =>
    `<a class="hub-card" href="${url(cluster.slug, it.slug)}">
          <h2>${esc(it.hubCardTitle || it.h1)}</h2>
          <p>${esc(it.hubCardDesc || it.lede)}</p>
          <span class="go">Read more →</span>
        </a>`
  ).join("\n        ");

  const body = `  <main id="top">
${crumbs(breadcrumb)}
    <section class="page-hero">
      <div class="wrap read">
        <span class="eyebrow">${esc(cluster.hubEyebrow)}</span>
        <h1>${esc(cluster.hubH1)}</h1>
        <p class="lede">${inline(cluster.hubLede)}</p>
      </div>
    </section>
    <div class="prose">
      <div class="wrap read">
        <section class="reveal">
        ${paras(cluster.hubIntro)}
        </section>
      </div>
    </div>
    <div class="wrap">
      <div class="hub-grid reveal">
        ${cards}
      </div>
    </div>
  </main>
${finalCta(cluster.hubCtaHeading, cluster.hubCtaSub, "hub_" + cluster.slug)}`;

  return htmlDoc({
    title: cluster.hubTitle,
    description: cluster.hubMeta,
    canonicalPath,
    jsonld,
    body,
  });
}
