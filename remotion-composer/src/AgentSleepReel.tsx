/**
 * AgentSleepReel0531 — "Stop making your AI agent remember everything. Make it sleep."
 * Greg-Isenberg-style editorial reel, 1080×1920, 30 fps, 65.892 s (1977 frames)
 * Warm paper aesthetic · ONE dark drama hit at 3.8% · Source-backed ADK receipt card
 * VO manifest ref: projects/05-31-2026/assets/audio/vo_manifest.json
 */
import React from "react";
import {
  AbsoluteFill, Audio, OffthreadVideo, Sequence, staticFile,
  interpolate, spring, useCurrentFrame, useVideoConfig, Easing,
} from "remotion";
import { loadFont as loadFraunces } from "@remotion/google-fonts/Fraunces";
import { loadFont as loadInter }    from "@remotion/google-fonts/Inter";

const { fontFamily: SERIF } = loadFraunces("normal", { weights: ["400", "600", "900"], subsets: ["latin"] });
const { fontFamily: SANS  } = loadInter(  "normal", { weights: ["400", "600", "700", "800"], subsets: ["latin"] });

const C = {
  paper:      "#F4EEE4",
  paperWarm:  "#F8EFE6",
  forest:     "#173D35",
  forestDeep: "#0C241F",
  navy:       "#0F1B2D",
  mint:       "#9FD8B5",
  mintStrong: "#5FAE86",
  teal:       "#3F9C82",
  coral:      "#D96D5F",
  gold:       "#F0BE3C",
  gray:       "#8C8A82",
  white:      "#FFFBF3",
  ink:        "#211C16",
};

const PAD  = 80;
const ease = Easing.bezier(0.22, 0.9, 0.24, 1);

// ─── helpers ──────────────────────────────────────────────────────────────────

/** Clamped interpolation with editorial ease. */
const A = (
  f: number, a: number, b: number, from: number, to: number,
  opts: Parameters<typeof interpolate>[3] = {}
) => interpolate(f, [a, b], [from, to], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: ease, ...opts });

/** One-liner spring entrance value (uses hooks — call only at top of component). */
function useEnter(delay = 0, dur = 16) {
  const f         = useCurrentFrame();
  const { fps }   = useVideoConfig();
  return spring({ frame: f - delay, fps, config: { damping: 200, mass: 0.7 }, durationInFrames: dur });
}

// ─── design primitives ────────────────────────────────────────────────────────

const Phrase: React.FC<{
  children: React.ReactNode;
  delay?: number; size?: number; color?: string; weight?: number;
  serif?: boolean; lh?: number; style?: React.CSSProperties; italic?: boolean;
}> = ({ children, delay = 0, size = 64, color = C.ink, weight = 600, serif = true, lh = 1.0, style, italic }) => {
  const f = useCurrentFrame();
  const p = useEnter(delay, 18);
  return (
    <div style={{
      fontFamily: serif ? SERIF : SANS, fontWeight: weight, fontSize: size, color,
      lineHeight: lh, letterSpacing: serif ? "-0.02em" : "-0.01em",
      fontStyle: italic ? "italic" : "normal",
      opacity: p, transform: `translateY(${(1 - p) * 16}px)`, filter: `blur(${(1 - p) * 3}px)`,
      ...style,
    }}>{children}</div>
  );
};

const Pill: React.FC<{
  children: React.ReactNode; delay?: number; bg?: string; color?: string; border?: string;
}> = ({ children, delay = 0, bg = C.white, color = C.forest, border = "rgba(23,61,53,.18)" }) => {
  const p = useEnter(delay, 14);
  return (
    <div style={{
      display: "inline-flex", alignItems: "center", gap: 8, padding: "10px 22px",
      borderRadius: 999, background: bg, color, border: `1.5px solid ${border}`,
      fontFamily: SANS, fontWeight: 700, fontSize: 24,
      letterSpacing: ".02em", textTransform: "uppercase", whiteSpace: "nowrap",
      opacity: p, transform: `translateY(${(1 - p) * 10}px) scale(${0.96 + p * 0.04})`,
      boxShadow: "0 6px 20px rgba(20,16,12,.08)",
    }}>{children}</div>
  );
};

const Grain: React.FC = () => (
  <AbsoluteFill style={{
    backgroundImage: [
      "radial-gradient(circle at 20% 15%, rgba(255,255,255,.5), transparent 40%)",
      "radial-gradient(circle at 85% 80%, rgba(217,109,95,.05), transparent 45%)",
    ].join(", "),
    mixBlendMode: "soft-light", opacity: 0.6, pointerEvents: "none",
  }} />
);

const Stage: React.FC<{
  children: React.ReactNode; bg?: string; justify?: string; align?: string;
}> = ({ children, bg = C.paper, justify = "center", align = "flex-start" }) => (
  <AbsoluteFill style={{
    background: bg, padding: PAD,
    display: "flex", flexDirection: "column",
    justifyContent: justify as React.CSSProperties["justifyContent"],
    alignItems:     align   as React.CSSProperties["alignItems"],
  }}>
    {children}
  </AbsoluteFill>
);

type QuotePart = { t: string; hl?: boolean };
const ReceiptCard: React.FC<{
  brand: React.ReactNode; url: string; date: string; headline: string;
  quote: QuotePart[]; rotate?: number; delay?: number; width?: number;
  hlStart?: number; accent?: string;
}> = ({ brand, url, date, headline, quote, rotate = -2, delay = 0, width = 880, hlStart = 18, accent = C.gold }) => {
  const f = useCurrentFrame();
  const p = useEnter(delay, 20);
  const w = A(f, delay + hlStart, delay + hlStart + 22, 0, 100);
  return (
    <div style={{
      width, background: C.white, borderRadius: 26, padding: "0 0 36px 0", overflow: "hidden",
      boxShadow: "0 30px 70px rgba(20,16,12,.22)", border: "1px solid rgba(20,16,12,.06)",
      transform: `translateY(${(1 - p) * 60}px) rotate(${rotate * (1 - p)}deg) scale(${0.94 + p * 0.06})`,
      opacity: p,
    }}>
      {/* browser chrome */}
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "16px 22px", background: "#EFEAE1", borderBottom: "1px solid rgba(20,16,12,.06)",
      }}>
        <div style={{ width: 13, height: 13, borderRadius: 99, background: C.coral }} />
        <div style={{ width: 13, height: 13, borderRadius: 99, background: C.gold }} />
        <div style={{ width: 13, height: 13, borderRadius: 99, background: C.mintStrong }} />
        <div style={{
          flex: 1, marginLeft: 12, background: C.white, borderRadius: 999,
          padding: "8px 18px", fontFamily: SANS, fontSize: 22, color: C.gray,
          border: "1px solid rgba(20,16,12,.06)",
        }}>
          🔒 {url}
        </div>
      </div>
      {/* content */}
      <div style={{ padding: "30px 40px 0 40px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
          <div>{brand}</div>
          <div style={{ fontFamily: SANS, fontSize: 22, color: C.gray, fontWeight: 600 }}>{date}</div>
        </div>
        <div style={{
          fontFamily: SERIF, fontWeight: 600, fontSize: 42, lineHeight: 1.10,
          color: C.ink, letterSpacing: "-0.02em", marginBottom: 20,
        }}>{headline}</div>
        <div style={{ fontFamily: SANS, fontSize: 28, lineHeight: 1.45, color: "#4A453E", fontWeight: 400 }}>
          {quote.map((q, i) =>
            q.hl ? (
              <span key={i} style={{ position: "relative", display: "inline" }}>
                <span style={{
                  position: "relative",
                  background: `linear-gradient(${accent} 0 0) left/${w}% 100% no-repeat`,
                  color: C.ink, fontWeight: 700, padding: "0 2px",
                  WebkitBoxDecorationBreak: "clone", boxDecorationBreak: "clone",
                  borderRadius: 2,
                }}>{q.t}</span>
              </span>
            ) : <span key={i}>{q.t}</span>
          )}
        </div>
      </div>
    </div>
  );
};

// ─── SCENES ───────────────────────────────────────────────────────────────────

/** SC01 — 28% stat slam on cream bg (0.0 – 4.795 s) */
const SC01Hook: React.FC = () => {
  const f = useCurrentFrame();
  const slam  = spring({ frame: f - 2, fps: 30, config: { damping: 10, mass: 0.9, stiffness: 160 } });
  const subIn = A(f, 18, 28, 0, 1);
  const pillP = useEnter(0, 14);
  return (
    <AbsoluteFill style={{ background: C.paperWarm }}>
      <Grain />
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", flexDirection: "column", padding: PAD }}>
        <div style={{ marginBottom: 12, opacity: pillP, transform: `translateY(${(1 - pillP) * 10}px)` }}>
          <Pill bg="rgba(217,109,95,.14)" color={C.coral} border="rgba(217,109,95,.4)">
            CHI-Bench · Enterprise Agents
          </Pill>
        </div>
        <div style={{
          fontFamily: SANS, fontWeight: 900, fontSize: 256, color: C.ink,
          lineHeight: 0.84, letterSpacing: "-0.05em",
          transform: `scale(${0.5 + slam * 0.5})`,
          textShadow: "0 6px 40px rgba(33,28,22,.14)",
        }}>
          28<span style={{ fontSize: 136, color: C.coral }}>%</span>
        </div>
        <div style={{ opacity: subIn, transform: `translateY(${(1 - subIn) * 12}px)`, textAlign: "center", marginTop: 8 }}>
          <div style={{ fontFamily: SERIF, fontWeight: 600, fontSize: 52, color: C.gray, fontStyle: "italic" }}>
            resolved.
          </div>
          <div style={{
            fontFamily: SANS, fontWeight: 700, fontSize: 26, color: C.gray,
            letterSpacing: ".04em", marginTop: 6, textTransform: "uppercase",
          }}>
            Best AI Agent in a Real Enterprise Benchmark
          </div>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

/** SC02 — KPI Grid with healthcare B-roll at 20% opacity (4.795 – 12.961 s) */
const SC02Context: React.FC = () => {
  const f = useCurrentFrame();
  // 3 KPI cards — no hook inside loop: compute springs directly
  const kpis = [
    { n: "20",     label: "real apps" },
    { n: "87",     label: "MCP tools" },
    { n: "1,290+", label: "policy docs" },
  ];
  const kpiPs = [
    A(f,  8, 24, 0, 1),
    A(f, 18, 34, 0, 1),
    A(f, 28, 44, 0, 1),
  ];
  return (
    <AbsoluteFill style={{ background: C.paperWarm }}>
      {/* Healthcare B-roll — 20% opacity, fills frame via objectFit cover */}
      <AbsoluteFill>
        <OffthreadVideo
          src={staticFile("05-31-2026/video/healthcare-dashboard.mp4")}
          muted
          style={{ width: "100%", height: "100%", objectFit: "cover", opacity: 0.20 }}
        />
      </AbsoluteFill>
      <Grain />
      <Stage bg="transparent" justify="center">
        <div style={{ marginBottom: 28 }}>
          <Phrase delay={2} size={44} weight={700} serif={false} color={C.gray}
            style={{ letterSpacing: ".06em", textTransform: "uppercase" }}>
            CHI-Bench tested agents on:
          </Phrase>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 20, width: "100%" }}>
          {kpis.map((k, i) => (
            <div key={i} style={{
              display: "flex", alignItems: "center", gap: 24,
              background: "rgba(255,251,243,.90)", borderRadius: 20, padding: "26px 34px",
              boxShadow: "0 12px 36px rgba(20,16,12,.10)",
              border: "1px solid rgba(20,16,12,.06)",
              opacity:    kpiPs[i],
              transform: `translateX(${(1 - kpiPs[i]) * -40}px) scale(${0.96 + kpiPs[i] * 0.04})`,
            }}>
              <div style={{
                fontFamily: SANS, fontWeight: 900, fontSize: 80,
                color: C.coral, lineHeight: 1, letterSpacing: "-0.03em", minWidth: 170, textAlign: "right",
              }}>{k.n}</div>
              <div style={{ fontFamily: SERIF, fontWeight: 600, fontSize: 46, color: C.ink }}>{k.label}</div>
            </div>
          ))}
        </div>
        <div style={{ marginTop: 32, opacity: A(f, 56, 68, 0, 1) }}>
          <Pill bg={C.forest} color={C.white} border="transparent" delay={56}>
            operational policy · real healthcare apps
          </Pill>
        </div>
      </Stage>
    </AbsoluteFill>
  );
};

/** SC03 — Stats cascade: 28% resolved + <20% strict pass (12.961 – 17.500 s) */
const SC03Stats: React.FC = () => {
  const f = useCurrentFrame();
  const p1 = A(f,  2, 18, 0, 1);
  const p2 = A(f, 40, 56, 0, 1);
  return (
    <Stage bg={C.paper} justify="center">
      <Grain />
      <div style={{ marginBottom: 36 }}>
        <Phrase delay={2} size={38} weight={700} serif={false} color={C.gray}
          style={{ letterSpacing: ".06em", textTransform: "uppercase", marginBottom: 14 }}>
          Best configuration
        </Phrase>
        <div style={{
          display: "flex", alignItems: "baseline", gap: 14,
          opacity: p1, transform: `translateX(${(1 - p1) * -30}px)`,
        }}>
          <span style={{
            fontFamily: SANS, fontWeight: 900, fontSize: 164,
            color: C.ink, lineHeight: 0.86, letterSpacing: "-0.04em",
          }}>
            28<span style={{ fontSize: 84, color: C.coral }}>%</span>
          </span>
          <span style={{ fontFamily: SERIF, fontWeight: 600, fontSize: 44, color: C.gray, fontStyle: "italic" }}>
            resolved
          </span>
        </div>
      </div>
      <div style={{
        opacity: p2, transform: `translateX(${(1 - p2) * -30}px)`,
        background: "rgba(217,109,95,.08)", borderRadius: 18, padding: "24px 32px",
        border: "1.5px solid rgba(217,109,95,.28)",
      }}>
        <div style={{ fontFamily: SANS, fontWeight: 700, fontSize: 30, color: C.coral, marginBottom: 4, letterSpacing: ".04em" }}>
          STRICT PASS
        </div>
        <div style={{ fontFamily: SANS, fontWeight: 900, fontSize: 84, color: C.coral, lineHeight: 1 }}>
          &lt;20%
        </div>
      </div>
    </Stage>
  );
};

/** SC04 — DARK HIT: 3.8% on navy, snaps back to warm at ~4 s (17.500 – 22.974 s) */
const SC04DarkHit: React.FC = () => {
  const f    = useCurrentFrame();
  const slam = spring({ frame: f - 4, fps: 30, config: { damping: 9, mass: 1.0, stiffness: 140 } });
  // Snap-back at ~4 s into scene (f ≈ 120) matches VO "the one that matters"
  const snap = A(f, 116, 126, 0, 1);
  return (
    <AbsoluteFill style={{ background: C.navy }}>
      {/* warm overlay slides in on snap-back */}
      <AbsoluteFill style={{ background: C.paperWarm, opacity: snap }} />
      <Grain />
      {/* Navy 3.8% hero */}
      <AbsoluteFill style={{
        alignItems: "center", justifyContent: "center",
        flexDirection: "column", padding: PAD,
        opacity: Math.max(0, 1 - snap * 2.5),
      }}>
        <div style={{
          fontFamily: SANS, fontWeight: 700, fontSize: 26, letterSpacing: ".10em",
          color: "rgba(159,216,181,.80)", textTransform: "uppercase", marginBottom: 20,
        }}>
          Single session · No external state
        </div>
        <div style={{
          fontFamily: SANS, fontWeight: 900, fontSize: 240, color: "#FFFFFF",
          lineHeight: 0.82, letterSpacing: "-0.05em",
          transform: `scale(${0.35 + slam * 0.65})`,
          textShadow: "0 10px 80px rgba(255,255,255,.28)",
        }}>
          3.8<span style={{ fontSize: 116, verticalAlign: "top", paddingTop: "0.14em", display: "inline-block" }}>%</span>
        </div>
        <div style={{ fontFamily: SERIF, fontWeight: 600, fontSize: 44, color: "rgba(255,251,243,.60)", fontStyle: "italic", marginTop: 14, opacity: A(f, 30, 44, 0, 1) }}>
          just 3.8 in every 100 tasks
        </div>
      </AbsoluteFill>
      {/* Warm snap-back phrase */}
<AbsoluteFill style={{
        alignItems: "center", justifyContent: "center",
        flexDirection: "column", padding: PAD,
        opacity: Math.max(0, snap * 2.5 - 1.5),
      }}>
        <Phrase delay={0} size={86} weight={900} color={C.ink} lh={1.05}>
          the one that <span style={{ color: C.coral }}>matters.</span>
        </Phrase>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

/** SC05 — Diagnosis keyword cascade (22.974 – 30.000 s) */
const SC05Diagnosis: React.FC = () => {
  const f = useCurrentFrame();
  const words  = ["State", "Handoffs", "Policy", "Approvals"];
  const colors = [C.ink, C.coral, C.forest, C.teal];
  const ps = [
    A(f, 10, 26, 0, 1),
    A(f, 22, 38, 0, 1),
    A(f, 34, 50, 0, 1),
    A(f, 46, 62, 0, 1),
  ];
  return (
    <Stage bg={C.paperWarm} justify="center">
      <Grain />
      <div style={{ marginBottom: 24 }}>
        <Phrase delay={2} size={38} weight={700} serif={false} color={C.gray}
          style={{ letterSpacing: ".04em", textTransform: "uppercase" }}>
          The gap is not model intelligence. It's:
        </Phrase>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {words.map((w, i) => (
          <div key={i} style={{
            fontFamily: SERIF, fontWeight: 900, fontSize: 96,
            color: colors[i], lineHeight: 0.92, letterSpacing: "-0.025em",
            opacity:    ps[i],
            transform: `translateX(${(1 - ps[i]) * 50}px)`,
          }}>{w}</div>
        ))}
      </div>
      <div style={{ marginTop: 30, opacity: A(f, 76, 90, 0, 1) }}>
        <Pill bg="rgba(217,109,95,.12)" color={C.coral} border="rgba(217,109,95,.35)" delay={76}>
          spans days · crosses tools · humans in loop
        </Pill>
      </div>
    </Stage>
  );
};

/** SC06 — B-roll insert: developer at laptop (30.000 – 33.642 s) */
const SC06BrollInsert: React.FC = () => {
  const f = useCurrentFrame();
  return (
    <AbsoluteFill>
      <OffthreadVideo
        src={staticFile("05-31-2026/video/laptop-approval.mp4")}
        muted
        style={{ width: "100%", height: "100%", objectFit: "cover" }}
      />
      {/* gradient overlay */}
      <AbsoluteFill style={{
        background: "linear-gradient(to bottom, transparent 55%, rgba(15,27,45,.55) 100%)",
      }} />
      {/* lower-third caption */}
      <AbsoluteFill style={{
        alignItems: "flex-end", justifyContent: "flex-start",
        padding: `0 ${PAD}px ${PAD + 40}px`,
      }}>
        <div style={{ opacity: A(f, 8, 20, 0, 1) }}>
          <div style={{
            fontFamily: SERIF, fontWeight: 600, fontSize: 48,
            color: "#FFFBF3", lineHeight: 1.15, letterSpacing: "-0.01em",
          }}>
            Real workflows span days,<br />cross multiple tools.
          </div>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

/** SC07 — Google ADK article receipt card (33.642 – 43.353 s) */
const SC07AdkReceipt: React.FC = () => {
  return (
    <Stage bg={C.paper} justify="center">
      <Grain />
      <div style={{ marginBottom: 24 }}>
        <Phrase delay={2} size={40} weight={700} serif={false} color={C.gray}
          style={{ textTransform: "uppercase", letterSpacing: ".04em" }}>
          Google's ADK team put it exactly right:
        </Phrase>
      </div>
      <ReceiptCard
        delay={8} rotate={-1.5} width={912} hlStart={28} accent={C.gold}
        brand={
          <span style={{ fontFamily: SANS, fontWeight: 900, fontSize: 34, color: "#1A73E8", letterSpacing: ".01em" }}>
            Google Developers Blog
          </span>
        }
        url="developers.googleblog.com"
        date="May 12, 2026"
        headline="Build Long-running AI agents that pause, resume, and never lose context with ADK"
        quote={[
          { t: "The agent " },
          { t: "needs to sleep – truly sleep – and wake up only when an external event arrives", hl: true },
          { t: "." },
        ]}
      />
    </Stage>
  );
};

/** SC08 — Builder reframe: ❌ Which model? vs ✅ State/Pause/Approve (43.353 – 53.333 s) */
const SC08Reframe: React.FC = () => {
  const f       = useCurrentFrame();
  const wrongIn = useEnter( 4, 18);
  const rightIn = useEnter(32, 18);
  const q1      = A(f, 36, 46, 0, 1);
  const q2      = A(f, 46, 56, 0, 1);
  const q3      = A(f, 56, 66, 0, 1);
  return (
    <Stage bg={C.paperWarm} justify="center">
      <Grain />
      <div style={{ marginBottom: 28 }}>
        <Phrase delay={2} size={40} weight={700} serif={false} color={C.gray}
          style={{ textTransform: "uppercase", letterSpacing: ".04em" }}>
          Building an agent product?
        </Phrase>
      </div>
      {/* Wrong question */}
      <div style={{
        opacity:    wrongIn,
        transform: `translateX(${(1 - wrongIn) * -40}px)`,
        background: "rgba(217,109,95,.10)", borderRadius: 20, padding: "26px 34px", marginBottom: 18,
        border: "2px solid rgba(217,109,95,.28)",
      }}>
        <div style={{ fontFamily: SANS, fontWeight: 700, fontSize: 26, color: C.coral, marginBottom: 6 }}>
          ❌ WRONG QUESTION
        </div>
        <div style={{ fontFamily: SERIF, fontWeight: 900, fontSize: 68, color: C.coral, lineHeight: 0.9, letterSpacing: "-0.025em" }}>
          Which model?
        </div>
      </div>
      {/* Right questions */}
      <div style={{
        opacity:    rightIn,
        transform: `translateX(${(1 - rightIn) * 40}px)`,
        background: "rgba(23,61,53,.09)", borderRadius: 20, padding: "26px 34px",
        border: "2px solid rgba(23,61,53,.22)",
      }}>
        <div style={{ fontFamily: SANS, fontWeight: 700, fontSize: 26, color: C.forest, marginBottom: 12 }}>
          ✅ RIGHT QUESTIONS
        </div>
        {[
          { t: "What state can it see?",       p: q1 },
          { t: "When does it pause?",           p: q2 },
          { t: "Who approves the next step?",   p: q3 },
        ].map(({ t, p }, i) => (
          <div key={i} style={{
            fontFamily: SERIF, fontWeight: 700, fontSize: 50, color: C.forest,
            lineHeight: 1.1, letterSpacing: "-0.02em",
            opacity: p, transform: `translateY(${(1 - p) * 12}px)`,
          }}>{t}</div>
        ))}
      </div>
    </Stage>
  );
};

/** SC09 — 5-item checklist (53.333 – 62.350 s) */
const SC09Checklist: React.FC = () => {
  const f = useCurrentFrame();
  const items = [
    "Explicit workflow state",
    "Scoped tools by phase",
    "Durable checkpoints",
    "Human approval gates",
    "Replay log",
  ];
  const ps = [
    A(f, 12, 24, 0, 1),
    A(f, 28, 40, 0, 1),
    A(f, 44, 56, 0, 1),
    A(f, 60, 72, 0, 1),
    A(f, 76, 88, 0, 1),
  ];
  return (
    <Stage bg={C.paper} justify="center">
      <Grain />
      <div style={{ marginBottom: 24 }}>
        <Phrase delay={2} size={38} weight={700} serif={false} color={C.gray}
          style={{ textTransform: "uppercase", letterSpacing: ".04em" }}>
          5-element checklist 💾
        </Phrase>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {items.map((item, i) => (
          <div key={i} style={{
            display: "flex", alignItems: "center", gap: 22,
            background: "rgba(255,251,243,.92)", borderRadius: 16, padding: "20px 26px",
            boxShadow: "0 8px 24px rgba(20,16,12,.08)", border: "1px solid rgba(20,16,12,.06)",
            opacity:    ps[i],
            transform: `translateX(${(1 - ps[i]) * -24}px)`,
          }}>
            <div style={{
              width: 40, height: 40, borderRadius: 99, background: C.forest, flexShrink: 0,
              display: "flex", alignItems: "center", justifyContent: "center",
            }}>
              <span style={{ color: C.white, fontFamily: SANS, fontWeight: 900, fontSize: 20 }}>✓</span>
            </div>
            <div style={{ fontFamily: SERIF, fontWeight: 600, fontSize: 46, color: C.ink, lineHeight: 1.0, letterSpacing: "-0.01em" }}>
              {item}
            </div>
          </div>
        ))}
      </div>
    </Stage>
  );
};

/** SC10 — CTA: Comment STATE (62.350 – 65.892 s) */
const SC10CTA: React.FC = () => {
  const f   = useCurrentFrame();
  const pop = spring({ frame: f - 2, fps: 30, config: { damping: 12, mass: 0.8, stiffness: 180 } });
  return (
    <AbsoluteFill style={{ background: C.paperWarm }}>
      <Grain />
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", flexDirection: "column", padding: PAD }}>
        <div style={{ opacity: pop, transform: `scale(${0.6 + pop * 0.4})`, textAlign: "center" }}>
          <div style={{ fontFamily: SERIF, fontWeight: 600, fontSize: 46, color: C.gray, fontStyle: "italic", marginBottom: 12 }}>
            Want the full checklist?
          </div>
          <div style={{ fontFamily: SANS, fontWeight: 900, fontSize: 112, color: C.forest, lineHeight: 0.88, letterSpacing: "-0.03em" }}>
            Comment
          </div>
          <div style={{ fontFamily: SANS, fontWeight: 900, fontSize: 148, color: C.coral, lineHeight: 0.84, letterSpacing: "-0.04em" }}>
            STATE
          </div>
          <div style={{ fontSize: 80, marginTop: 12, lineHeight: 1 }}>👇</div>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// ─── MAIN COMPOSITION ─────────────────────────────────────────────────────────

const SCENES = [
  { id: "sc01-hook",         Comp: SC01Hook,        start:  0.000, dur:  4.795 },
  { id: "sc02-context",      Comp: SC02Context,     start:  4.795, dur:  8.166 },
  { id: "sc03-stats",        Comp: SC03Stats,       start: 12.961, dur:  4.539 },
  { id: "sc04-dark-hit",     Comp: SC04DarkHit,     start: 17.500, dur:  5.474 },
  { id: "sc05-diagnosis",    Comp: SC05Diagnosis,   start: 22.974, dur:  7.026 },
  { id: "sc06-broll-insert", Comp: SC06BrollInsert, start: 30.000, dur:  3.642 },
  { id: "sc07-adk-receipt",  Comp: SC07AdkReceipt,  start: 33.642, dur:  9.711 },
  { id: "sc08-reframe",      Comp: SC08Reframe,     start: 43.353, dur:  9.980 },
  { id: "sc09-checklist",    Comp: SC09Checklist,   start: 53.333, dur:  9.017 },
  { id: "sc10-cta",          Comp: SC10CTA,         start: 62.350, dur:  3.542 },
] as const;

const SFX_BASE = "the-500m-claude-bill/audio/sfx";
const SFX_CUES = [
  { src: `${SFX_BASE}/stinger-opener.mp3`,   at:  0.000, vol: 0.60 },
  { src: `${SFX_BASE}/impact-soft.mp3`,      at:  0.500, vol: 0.60 },
  { src: `${SFX_BASE}/tick-check.mp3`,       at:  5.300, vol: 0.50 },
  { src: `${SFX_BASE}/tick-check.mp3`,       at:  6.300, vol: 0.50 },
  { src: `${SFX_BASE}/tick-check.mp3`,       at:  7.300, vol: 0.50 },
  { src: `${SFX_BASE}/impact-soft.mp3`,      at: 13.500, vol: 0.60 },
  { src: `${SFX_BASE}/impact-soft.mp3`,      at: 15.500, vol: 0.60 },
  { src: `${SFX_BASE}/impact-cinematic.mp3`, at: 17.500, vol: 0.70 },
  { src: `${SFX_BASE}/bass-drop-soft.mp3`,   at: 17.700, vol: 0.55 },
  { src: `${SFX_BASE}/whoosh-fast.mp3`,      at: 22.974, vol: 0.50 },
  { src: `${SFX_BASE}/paper-flip.mp3`,       at: 34.100, vol: 0.50 },
  { src: `${SFX_BASE}/whoosh-fast.mp3`,      at: 43.353, vol: 0.50 },
  { src: `${SFX_BASE}/tick-check.mp3`,       at: 54.000, vol: 0.50 },
  { src: `${SFX_BASE}/tick-check.mp3`,       at: 55.200, vol: 0.50 },
  { src: `${SFX_BASE}/tick-check.mp3`,       at: 56.300, vol: 0.50 },
  { src: `${SFX_BASE}/tick-check.mp3`,       at: 57.500, vol: 0.50 },
  { src: `${SFX_BASE}/tick-check.mp3`,       at: 58.700, vol: 0.50 },
  { src: `${SFX_BASE}/outro-payoff.mp3`,     at: 62.500, vol: 0.55 },
];

// Music: 62.04 s — fade in over first 1.5 s (45 f), fade out 59.5 s → 62.04 s
const MUSIC_FADE_START_F = Math.round(59.5  * 30); // 1785
const MUSIC_FADE_END_F   = Math.round(62.04 * 30); // 1861

export const AgentSleepReel0531: React.FC = () => {
  const { fps } = useVideoConfig();
  return (
    <AbsoluteFill style={{ background: C.paperWarm }}>

      {/* ── scenes ── */}
      {SCENES.map(({ id, Comp, start, dur }) => (
        <Sequence
          key={id}
          name={id}
          from={Math.round(start * fps)}
          durationInFrames={Math.round(dur * fps)}
        >
          <Comp />
        </Sequence>
      ))}

      {/* ── narration (full 65.5 s concatenated track) ── */}
      <Audio src={staticFile("05-31-2026/audio/narration.mp3")} volume={1} />

      {/* ── music bed — vol 0.09, fade in + fade out ── */}
      <Audio
        src={staticFile("05-31-2026/music/background_music.mp3")}
        volume={(fr) => {
          const fin  = interpolate(fr, [0, 45],                            [0, 0.09], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
          const fout = interpolate(fr, [MUSIC_FADE_START_F, MUSIC_FADE_END_F], [0.09, 0],  { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
          return Math.min(fin, fout);
        }}
      />

      {/* ── SFX cues ── */}
      {SFX_CUES.map((s, i) => (
        <Sequence
          key={`sfx-${i}`}
          from={Math.round(s.at * fps)}
          durationInFrames={fps * 3}
          name={`sfx-${i}`}
        >
          <Audio src={staticFile(s.src)} volume={s.vol ?? 0.6} />
        </Sequence>
      ))}

    </AbsoluteFill>
  );
};
