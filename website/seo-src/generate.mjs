/* OpenNolan — programmatic page generator.
   Reads seo-src/data/<cluster>/<slug>.json, validates, builds hub-and-spoke
   internal links, and writes static HTML into website/<cluster>/ plus sitemap.xml.

   Run from the website/ dir:  node seo-src/generate.mjs
*/
import { readFileSync, writeFileSync, readdirSync, mkdirSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { renderPage, renderHub } from "./template.mjs";

const __dir = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dir, "..");           // website/
const DATA = join(__dir, "data");
const BASE = "https://www.opennolan.com";
const TODAY = new Date().toISOString().slice(0, 10);

/* ----- cluster config (order defines hub card order + sitemap order) ----- */
export const CLUSTERS = {
  "for": {
    slug: "for", crumb: "Built for",
    order: ["founders", "indie-hackers", "saas", "developers", "agencies", "solopreneurs"],
    hubEyebrow: "Built for how you ship",
    hubH1: "Made for builders, not video editors",
    hubLede: "Whatever you're building, the videos that get it seen follow the same playbook. Pick your world and see how OpenNolan makes the whole reel for you.",
    hubIntro: [
      "Short-form video is how products get discovered now — but the reels that travel follow the same playbook whether you're a solo founder or a six-person team. The hard part was never the strategy. It was the editing.",
      "OpenNolan is the Mac app that removes that step: drop in your clips and an AI agent makes the whole vertical video — B-roll, captions, music and motion — so you can ship content as fast as you ship product. Pick the page closest to how you work.",
    ],
    hubTitle: "OpenNolan for founders, builders & creators | By audience",
    hubMeta: "See how OpenNolan turns raw clips into scroll-stopping Reels and TikToks for founders, indie hackers, SaaS teams, developers and agencies — no editing.",
    hubCtaHeading: "Ship short-form as fast as you ship product.",
    hubCtaSub: "Join the waitlist and be first when the Mac app lands for your stack.",
  },
  "compare": {
    slug: "compare", crumb: "Compare",
    order: ["capcut", "descript", "opus-clip", "submagic", "veed", "adobe-premiere", "final-cut-pro"],
    hubEyebrow: "Honest comparisons",
    hubH1: "OpenNolan vs the tools you're weighing",
    hubLede: "Every editor is good at something. Here's where OpenNolan fits — a Mac-native app where an AI agent makes the whole vertical video, bring-your-own-key, no subscription.",
    hubIntro: [
      "Every video tool is good at something. CapCut gives you templates, Descript edits by transcript, Opus Clip slices long videos into shorts. What none of them do is make the **whole** vertical reel for you from raw clips.",
      "OpenNolan is a Mac-native app where an AI agent does the editing — bring-your-own-key, no subscription, open source. These comparisons are honest: we credit what each tool does well, then show exactly where OpenNolan fits.",
    ],
    hubTitle: "OpenNolan vs CapCut, Descript, Opus Clip & more",
    hubMeta: "Fair, side-by-side comparisons of OpenNolan against CapCut, Descript, Opus Clip, Submagic, Veed and Adobe Premiere for making short-form video.",
    hubCtaHeading: "Skip the editing learning curve entirely.",
    hubCtaSub: "Join the waitlist for the Mac app where the agent makes the whole reel — you just review it.",
  },
  "how-to": {
    slug: "how-to", crumb: "Guides",
    order: ["instagram-reels", "tiktoks", "youtube-shorts", "product-demo-video", "launch-video", "talking-head-video", "add-captions-to-a-video"],
    hubEyebrow: "How-to guides",
    hubH1: "How to make short-form that actually gets watched",
    hubLede: "Practical playbooks for the videos founders actually need — Reels, TikToks, Shorts, demos and launch videos — plus how OpenNolan makes each one for you.",
    hubIntro: [
      "The mechanics of a Reel, a TikTok, a Short or a demo video are different — but they all live or die on the same things: a hook in the first second, fast cuts, captions on mute, and proof that earns the claim.",
      "Each guide below walks the real process step by step, then shows how OpenNolan's AI agent does it for you on a Mac — so you can learn the playbook and skip the editing grind.",
    ],
    hubTitle: "How to make Reels, TikToks, Shorts & demo videos | Guides",
    hubMeta: "Step-by-step guides to making Instagram Reels, TikToks, YouTube Shorts, product demos, launch videos and talking-head videos — and how to do it without editing.",
    hubCtaHeading: "Make the whole video — without the editing grind.",
    hubCtaSub: "Join the waitlist for the Mac app that turns your clips into a finished short-form video.",
  },
  "learn": {
    slug: "learn", crumb: "Glossary",
    order: ["short-form-video", "what-is-a-hook", "b-roll", "jump-cut", "burned-in-captions", "vertical-video"],
    hubEyebrow: "Short-form glossary",
    hubH1: "The short-form video terms worth knowing",
    hubLede: "Plain-English definitions of the concepts behind videos that travel — and how OpenNolan handles each one so you don't have to.",
    hubIntro: [
      "Videos that travel are built from a small set of repeatable moves — the hook, the jump cut, B-roll, burned-in captions and the vertical 9:16 frame. Knowing the vocabulary makes every reel you plan sharper.",
      "These plain-English definitions explain each concept in the context of short-form that actually gets watched — and how OpenNolan handles each one for you, so you don't have to.",
    ],
    hubTitle: "Short-form video glossary: hooks, B-roll, jump cuts & more",
    hubMeta: "Plain-English definitions of short-form video terms — the hook, B-roll, jump cuts, burned-in captions, vertical video and more — for founders and builders.",
    hubCtaHeading: "Know the playbook. Skip the editing.",
    hubCtaSub: "Join the waitlist for the Mac app that bakes the whole short-form playbook in for you.",
  },
};

/* ----- load + validate ----- */
const REQUIRED = ["cluster", "slug", "targetKeyword", "title", "metaDescription",
  "eyebrow", "h1", "lede", "intro", "sections", "faqs", "ctaHeading", "ctaSub"];

function loadData() {
  const pages = {};
  const warnings = [];
  for (const cluster of Object.keys(CLUSTERS)) {
    const dir = join(DATA, cluster);
    pages[cluster] = [];
    if (!existsSync(dir)) { warnings.push(`! no data dir for cluster "${cluster}"`); continue; }
    const files = readdirSync(dir).filter((f) => f.endsWith(".json") && !f.startsWith("_"));
    const bySlug = {};
    for (const f of files) {
      let raw = readFileSync(join(dir, f), "utf8").trim();
      if (raw.startsWith("```")) raw = raw.replace(/^```[a-z]*\s*/i, "").replace(/```\s*$/, "");
      let d;
      try { d = JSON.parse(raw); } catch (e) { throw new Error(`Bad JSON in ${cluster}/${f}: ${e.message}`); }
      for (const k of REQUIRED) {
        if (d[k] == null || (Array.isArray(d[k]) && d[k].length === 0) || d[k] === "")
          throw new Error(`${cluster}/${f}: missing/empty required field "${k}"`);
      }
      if (d.cluster !== cluster) throw new Error(`${cluster}/${f}: cluster mismatch ("${d.cluster}")`);
      if (!Array.isArray(d.sections) || d.sections.length < 2) throw new Error(`${cluster}/${f}: needs >=2 sections`);
      if (!Array.isArray(d.faqs) || d.faqs.length < 3) throw new Error(`${cluster}/${f}: needs >=3 FAQs`);
      if (cluster === "compare" && (!d.comparison || !Array.isArray(d.comparison.rows) || d.comparison.rows.length < 4))
        throw new Error(`${cluster}/${f}: compare page needs comparison.rows >=4`);
      if (d.title.length > 62) warnings.push(`  title ${d.title.length}c (>62): ${cluster}/${d.slug}`);
      if (d.metaDescription.length > 160) warnings.push(`  meta ${d.metaDescription.length}c (>160): ${cluster}/${d.slug}`);
      if (d.metaDescription.length < 70) warnings.push(`  meta ${d.metaDescription.length}c (<70): ${cluster}/${d.slug}`);
      bySlug[d.slug] = d;
    }
    // order per config, then any extras
    const ordered = [];
    for (const slug of CLUSTERS[cluster].order) if (bySlug[slug]) { ordered.push(bySlug[slug]); delete bySlug[slug]; }
    for (const slug of Object.keys(bySlug)) { ordered.push(bySlug[slug]); warnings.push(`  extra (not in order): ${cluster}/${slug}`); }
    pages[cluster] = ordered;
  }
  return { pages, warnings };
}

/* ----- related links (hub-and-spoke + cross-cluster) ----- */
function shortTitle(d) { return d.hubCardTitle || d.relTitle || d.h1; }
function shortDesc(d) { return d.relDesc || d.hubCardDesc || d.lede; }

function buildRelated(d, pages) {
  if (Array.isArray(d.related) && d.related.length) {
    // explicit "cluster/slug" refs
    return d.related.map((ref) => {
      const [c, s] = ref.split("/");
      const t = (pages[c] || []).find((p) => p.slug === s);
      return t ? { path: `/${c}/${s}`, kicker: CLUSTERS[c].crumb, title: shortTitle(t), desc: shortDesc(t) } : null;
    }).filter(Boolean);
  }
  const out = [];
  const siblings = pages[d.cluster].filter((p) => p.slug !== d.slug);
  const idx = pages[d.cluster].findIndex((p) => p.slug === d.slug);
  // 2 siblings (rotate by index so pages don't all link to the same two)
  for (let i = 1; i <= 2 && siblings.length; i++) {
    const t = siblings[(idx + i) % siblings.length];
    if (t && !out.find((o) => o.path === `/${t.cluster}/${t.slug}`)) out.push(t);
  }
  // 2 cross-cluster picks
  const otherClusters = Object.keys(CLUSTERS).filter((c) => c !== d.cluster);
  for (let i = 0; i < otherClusters.length && out.length < 4; i++) {
    const c = otherClusters[(idx + i) % otherClusters.length];
    const list = pages[c];
    if (list && list.length) { const t = list[idx % list.length]; if (t) out.push(t); }
  }
  return out.slice(0, 4).map((t) => ({
    path: `/${t.cluster}/${t.slug}`, kicker: CLUSTERS[t.cluster].crumb, title: shortTitle(t), desc: shortDesc(t),
  }));
}

/* ----- write ----- */
function write(path, content) {
  const full = join(ROOT, path);
  mkdirSync(dirname(full), { recursive: true });
  writeFileSync(full, content);
}

function sitemap(allPaths) {
  const urls = allPaths.map(({ loc, priority, changefreq }) =>
    `  <url>\n    <loc>${BASE}${loc}</loc>\n    <lastmod>${TODAY}</lastmod>\n    <changefreq>${changefreq}</changefreq>\n    <priority>${priority}</priority>\n  </url>`
  ).join("\n");
  return `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
  <url>
    <loc>${BASE}/</loc>
    <lastmod>${TODAY}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
    <image:image>
      <image:loc>${BASE}/assets/og-image.png</image:loc>
    </image:image>
    <image:image>
      <image:loc>${BASE}/assets/gallery/app-screenshot-1.webp</image:loc>
    </image:image>
    <image:image>
      <image:loc>${BASE}/assets/gallery/app-screenshot-2.webp</image:loc>
    </image:image>
  </url>
${urls}
</urlset>
`;
}

/* ----- main ----- */
function main() {
  const { pages, warnings } = loadData();
  const sitemapPaths = [];
  let count = 0;

  for (const cluster of Object.keys(CLUSTERS)) {
    const cfg = CLUSTERS[cluster];
    const list = pages[cluster];
    // hub
    write(`${cluster}/index.html`, renderHub(cfg, list));
    sitemapPaths.push({ loc: `/${cluster}`, priority: "0.8", changefreq: "weekly" });
    // spokes
    for (const d of list) {
      const ctx = { cluster: cfg, placement: `${cluster}_${d.slug}`, related: buildRelated(d, pages) };
      write(`${cluster}/${d.slug}.html`, renderPage(d, ctx));
      sitemapPaths.push({ loc: `/${cluster}/${d.slug}`, priority: "0.7", changefreq: "monthly" });
      count++;
    }
  }

  writeFileSync(join(ROOT, "sitemap.xml"), sitemap(sitemapPaths));

  console.log(`✓ Generated ${count} spoke pages + ${Object.keys(CLUSTERS).length} hubs + sitemap.xml`);
  for (const c of Object.keys(CLUSTERS)) console.log(`   /${c}: ${pages[c].length} pages`);
  if (warnings.length) { console.log("\nWarnings:"); warnings.forEach((w) => console.log(w)); }
  else console.log("\nNo warnings — all titles/metas within length budgets.");
}

main();
