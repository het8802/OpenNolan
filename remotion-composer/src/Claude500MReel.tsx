/**
 * The $500M Claude Bill — Greg-editorial source-backed news reel (1080x1920).
 * Warm editorial base with dark-drama hits on the invoice / token-burn / budget-burn beats.
 * Deterministic assembler: content + timings live in defaultProps; this file is the design system.
 */
import React from "react";
import {
  AbsoluteFill, Audio, OffthreadVideo, Sequence, staticFile,
  interpolate, spring, useCurrentFrame, useVideoConfig, Easing,
} from "remotion";
import { loadFont as loadFraunces } from "@remotion/google-fonts/Fraunces";
import { loadFont as loadInter } from "@remotion/google-fonts/Inter";

const { fontFamily: SERIF } = loadFraunces("normal", { weights: ["400", "600", "900"], subsets: ["latin"] });
const { fontFamily: SANS } = loadInter("normal", { weights: ["400", "600", "800"], subsets: ["latin"] });

const C = {
  paper: "#F4EEE4", paperWarm: "#F8EFE6", forest: "#173D35", forestDeep: "#0C241F",
  mint: "#9FD8B5", mintStrong: "#5FAE86", teal: "#3F9C82", coral: "#D96D5F",
  gold: "#F0BE3C", charcoal: "#171410", gray: "#8C8A82", white: "#FFFBF3", ink: "#211C16",
};

// ---------- helpers ----------
const ease = Easing.bezier(0.22, 0.9, 0.24, 1);
function useEnter(delay = 0, dur = 16) {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  return spring({ frame: f - delay, fps, config: { damping: 200, mass: 0.7 }, durationInFrames: dur });
}
const A = (f: number, a: number, b: number, from: number, to: number, opts = {}) =>
  interpolate(f, [a, b], [from, to], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: ease, ...opts });

// Designed phrase: translateY + fade + slight blur settle
const Phrase: React.FC<{
  children: React.ReactNode; delay?: number; size?: number; color?: string;
  weight?: number; serif?: boolean; lh?: number; style?: React.CSSProperties; italic?: boolean;
}> = ({ children, delay = 0, size = 64, color = C.ink, weight = 600, serif = true, lh = 1.0, style, italic }) => {
  const f = useCurrentFrame();
  const p = useEnter(delay, 18);
  return (
    <div style={{
      fontFamily: serif ? SERIF : SANS, fontWeight: weight, fontSize: size, color, lineHeight: lh,
      letterSpacing: serif ? "-0.02em" : "-0.01em", fontStyle: italic ? "italic" : "normal",
      opacity: p, transform: `translateY(${(1 - p) * 16}px)`, filter: `blur(${(1 - p) * 3}px)`, ...style,
    }}>{children}</div>
  );
};

// Highlighter sweep behind a phrase (like a marker)
const Highlight: React.FC<{
  children: React.ReactNode; start: number; dur?: number; color?: string; size?: number;
  weight?: number; serif?: boolean; textColor?: string;
}> = ({ children, start, dur = 16, color = C.gold, size = 40, weight = 700, serif = false, textColor = C.ink }) => {
  const f = useCurrentFrame();
  const w = A(f, start, start + dur, 0, 100);
  return (
    <span style={{ position: "relative", display: "inline-block", padding: "0 .12em" }}>
      <span style={{
        position: "absolute", left: 0, bottom: "0.06em", height: "0.72em", width: `${w}%`,
        background: color, opacity: 0.55, borderRadius: 3, transform: "skewX(-6deg)", zIndex: 0,
      }} />
      <span style={{
        position: "relative", zIndex: 1, fontFamily: serif ? SERIF : SANS, fontWeight: weight,
        fontSize: size, color: textColor, letterSpacing: "-0.01em",
      }}>{children}</span>
    </span>
  );
};

const Pill: React.FC<{ children: React.ReactNode; delay?: number; bg?: string; color?: string; border?: string }> =
  ({ children, delay = 0, bg = C.white, color = C.forest, border = "rgba(23,61,53,.18)" }) => {
    const p = useEnter(delay, 14);
    return (
      <div style={{
        display: "inline-flex", alignItems: "center", gap: 8, padding: "10px 20px", borderRadius: 999,
        background: bg, color, border: `1.5px solid ${border}`, fontFamily: SANS, fontWeight: 700,
        fontSize: 24, letterSpacing: ".02em", textTransform: "uppercase", whiteSpace: "nowrap",
        opacity: p, transform: `translateY(${(1 - p) * 10}px) scale(${0.96 + p * 0.04})`,
        boxShadow: "0 6px 20px rgba(20,16,12,.08)",
      }}>{children}</div>
    );
  };

const Grain: React.FC = () => (
  <AbsoluteFill style={{
    backgroundImage:
      "radial-gradient(circle at 20% 15%, rgba(255,255,255,.5), transparent 40%), radial-gradient(circle at 85% 80%, rgba(217,109,95,.05), transparent 45%)",
    mixBlendMode: "soft-light", opacity: 0.6,
  }} />
);

const BrollBg: React.FC<{ src: string; opacity?: number; tint?: string }> = ({ src, opacity = 0.16, tint = C.forestDeep }) => (
  <AbsoluteFill>
    <OffthreadVideo src={staticFile(src)} muted style={{ width: "100%", height: "100%", objectFit: "cover", opacity }} />
    <AbsoluteFill style={{ background: tint, opacity: 0.55, mixBlendMode: "multiply" }} />
  </AbsoluteFill>
);

// Source-receipt card (faithful reproduction: masthead + headline + date + url + quote w/ highlight)
type QuotePart = { t: string; hl?: boolean };
const ReceiptCard: React.FC<{
  brand: React.ReactNode; url: string; date: string; headline: string;
  quote: QuotePart[]; rotate?: number; delay?: number; width?: number; hlStart?: number; accent?: string;
}> = ({ brand, url, date, headline, quote, rotate = -2, delay = 0, width = 880, hlStart = 18, accent = C.gold }) => {
  const f = useCurrentFrame();
  const p = useEnter(delay, 20);
  // animate highlight width across the marked run
  const w = A(f, delay + hlStart, delay + hlStart + 16, 0, 100);
  return (
    <div style={{
      width, background: C.white, borderRadius: 26, padding: "0 0 36px 0", overflow: "hidden",
      boxShadow: "0 30px 70px rgba(20,16,12,.22)", border: "1px solid rgba(20,16,12,.06)",
      transform: `translateY(${(1 - p) * 60}px) rotate(${rotate * (1 - p * 0.0)}deg) scale(${0.94 + p * 0.06})`,
      opacity: p,
    }}>
      {/* browser chrome */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "16px 22px", background: "#EFEAE1", borderBottom: "1px solid rgba(20,16,12,.06)" }}>
        <div style={{ width: 13, height: 13, borderRadius: 99, background: C.coral }} />
        <div style={{ width: 13, height: 13, borderRadius: 99, background: C.gold }} />
        <div style={{ width: 13, height: 13, borderRadius: 99, background: C.mintStrong }} />
        <div style={{ flex: 1, marginLeft: 12, background: C.white, borderRadius: 999, padding: "8px 18px", fontFamily: SANS, fontSize: 22, color: C.gray, border: "1px solid rgba(20,16,12,.06)" }}>
          🔒 {url}
        </div>
      </div>
      <div style={{ padding: "30px 40px 0 40px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 18 }}>
          <div>{brand}</div>
          <div style={{ fontFamily: SANS, fontSize: 22, color: C.gray, fontWeight: 600 }}>{date}</div>
        </div>
        <div style={{ fontFamily: SERIF, fontWeight: 600, fontSize: 52, lineHeight: 1.05, color: C.ink, letterSpacing: "-0.02em", marginBottom: 22 }}>
          {headline}
        </div>
        <div style={{ fontFamily: SANS, fontSize: 30, lineHeight: 1.4, color: "#4A453E", fontWeight: 400 }}>
          {quote.map((q, i) =>
            q.hl ? (
              <span key={i} style={{ position: "relative", display: "inline" }}>
                <span style={{ position: "relative", background: `linear-gradient(${accent} 0 0) left/${w}% 100% no-repeat`, color: C.ink, fontWeight: 700, padding: "0 2px", boxDecorationBreak: "clone", WebkitBoxDecorationBreak: "clone", borderRadius: 2 }}>
                  {q.t}
                </span>
              </span>
            ) : <span key={i}>{q.t}</span>
          )}
        </div>
      </div>
    </div>
  );
};

const Mast: React.FC<{ name: string; color?: string; serif?: boolean; weight?: number; size?: number; italic?: boolean }> =
  ({ name, color = C.ink, serif = false, weight = 800, size = 34, italic }) => (
    <span style={{ fontFamily: serif ? SERIF : SANS, fontWeight: weight, fontSize: size, color, letterSpacing: serif ? "-0.01em" : "0.02em", fontStyle: italic ? "italic" : "normal" }}>{name}</span>
  );

const PAD = 84;
const Stage: React.FC<{ children: React.ReactNode; bg?: string; justify?: string; align?: string }> =
  ({ children, bg = C.paper, justify = "center", align = "flex-start" }) => (
    <AbsoluteFill style={{ background: bg, padding: PAD, display: "flex", flexDirection: "column", justifyContent: justify as any, alignItems: align as any }}>
      {children}
    </AbsoluteFill>
  );

// ============================ SCENES ============================

const S1Hook: React.FC = () => {
  const f = useCurrentFrame();
  const slam = spring({ frame: f - 4, fps: 30, config: { damping: 12, mass: 0.9, stiffness: 120 } });
  const stamp = spring({ frame: f - 18, fps: 30, config: { damping: 9, mass: 0.6, stiffness: 200 } });
  const shake = f < 12 ? Math.sin(f * 3) * (12 - f) * 0.6 : 0;
  return (
    <AbsoluteFill style={{ background: C.forestDeep }}>
      <BrollBg src="the-500m-claude-bill/broll/burning-money.mp4" opacity={0.28} tint={C.forestDeep} />
      <Grain />
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", padding: PAD }}>
        <div style={{ position: "absolute", top: 150, opacity: A(f, 0, 12, 0, 1) }}>
          <Pill bg="rgba(217,109,95,.16)" color={C.coral} border="rgba(217,109,95,.5)">● Founder warning</Pill>
        </div>
        {/* invoice card */}
        <div style={{
          width: 912, background: C.white, borderRadius: 28, padding: "54px 56px 64px", position: "relative",
          boxShadow: "0 40px 90px rgba(0,0,0,.45)", transform: `translateY(${(1 - slam) * -700}px) translateX(${shake}px) rotate(${(1 - slam) * -3}deg)`,
        }}>
          <div style={{ fontFamily: SANS, fontWeight: 700, fontSize: 26, color: C.gray, letterSpacing: ".18em", textTransform: "uppercase" }}>Invoice · Anthropic Claude</div>
          <div style={{ height: 2, background: "rgba(20,16,12,.1)", margin: "26px 0 30px" }} />
          <div style={{ fontFamily: SANS, fontWeight: 600, fontSize: 30, color: C.ink }}>CLAUDE USAGE</div>
          <div style={{ fontFamily: SERIF, fontWeight: 900, fontSize: 104, color: C.coral, lineHeight: 0.96, letterSpacing: "-0.03em", margin: "8px 0 8px", whiteSpace: "nowrap" }}>$500,000,000</div>
          <div style={{ fontFamily: SANS, fontWeight: 600, fontSize: 32, color: C.gray }}>billing period · 30 days</div>
          {/* NO LIMITS stamp — top-right corner, classic overdue-stamp look */}
          <div style={{
            position: "absolute", right: 36, top: 96, transform: `rotate(-13deg) scale(${0.5 + stamp * 0.5})`, opacity: stamp,
            border: `5px solid ${C.coral}`, color: C.coral, borderRadius: 12, padding: "8px 18px",
            fontFamily: SANS, fontWeight: 800, fontSize: 34, letterSpacing: ".04em",
          }}>NO LIMITS?</div>
        </div>
        <div style={{ position: "absolute", bottom: 150, opacity: A(f, 24, 36, 0, 1) }}>
          <Pill bg="rgba(255,251,243,.1)" color={C.white} border="rgba(255,251,243,.3)">Reported by Axios · May 28</Pill>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

const S2Reported: React.FC = () => {
  const f = useCurrentFrame();
  return (
    <Stage bg={C.paper} justify="center">
      <Grain />
      <div style={{ marginBottom: 40 }}>
        <Phrase delay={2} size={86} weight={900} color={C.ink}>Don’t repeat it</Phrase>
        <Phrase delay={8} size={86} weight={900} color={C.ink} style={{ marginTop: 4 }}>
          like it’s <span style={{ color: C.coral }}>gospel.</span>
        </Phrase>
      </div>
      <div style={{ display: "flex", justifyContent: "center" }}>
        <ReceiptCard
          delay={14} rotate={-2.2} width={900} hlStart={26} accent={C.gold}
          brand={<Mast name="AXIOS" color="#0B57D0" weight={800} size={40} />}
          url="axios.com/2026/05/28/ai-spending-roi-enterprise-costs" date="May 28, 2026"
          headline="AI sticker shock hits corporate America"
          quote={[
            { t: "An " }, { t: "AI consultant told Axios", hl: true },
            { t: " a single client spent " }, { t: "~$500M on Claude", hl: true },
            { t: " in one month — the company is unnamed." },
          ]}
        />
      </div>
      <div style={{ marginTop: 44, textAlign: "center", width: "100%" }}>
        <span style={{ opacity: A(f, 50, 64, 0, 1) }}>
          <Pill bg={C.forest} color={C.white} border="transparent" delay={50}>Reported ≠ verified</Pill>
        </span>
      </div>
    </Stage>
  );
};

const S3Electricity: React.FC = () => {
  const f = useCurrentFrame();
  const morph = A(f, 30, 70, 0, 1);
  return (
    <Stage bg={C.paperWarm} justify="center">
      <Grain />
      <div style={{ marginBottom: 56 }}>
        <Phrase delay={2} size={92} weight={900}>AI isn’t SaaS.</Phrase>
        <div style={{ marginTop: 6, display: "flex", gap: 0, alignItems: "baseline" }}>
          <Phrase delay={12} size={92} weight={900}>It’s&nbsp;</Phrase>
          <Phrase delay={16} size={92} weight={900} color={C.teal}>electricity.</Phrase>
        </div>
      </div>
      {/* SaaS box -> meter */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 40 }}>
        <div style={{
          width: 300, height: 240, borderRadius: 24, background: C.white, border: `2px solid rgba(217,109,95,.4)`,
          display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", gap: 10,
          opacity: 1 - morph * 0.55, transform: `scale(${1 - morph * 0.08})`, boxShadow: "0 14px 36px rgba(20,16,12,.1)",
        }}>
          <div style={{ fontFamily: SANS, fontWeight: 800, fontSize: 30, color: C.gray }}>SaaS plan</div>
          <div style={{ fontFamily: SERIF, fontWeight: 900, fontSize: 56, color: C.ink }}>$X/mo</div>
          <div style={{ fontFamily: SANS, fontSize: 26, color: C.coral, fontWeight: 700 }}>flat ❌</div>
        </div>
        <div style={{ fontFamily: SANS, fontSize: 48, color: C.gray, opacity: morph }}>→</div>
        <div style={{
          width: 300, height: 240, borderRadius: 24, background: C.forest, border: `2px solid ${C.mintStrong}`,
          display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", gap: 8,
          opacity: 0.4 + morph * 0.6, transform: `scale(${0.92 + morph * 0.08})`, boxShadow: "0 18px 40px rgba(23,61,53,.3)",
        }}>
          <div style={{ fontFamily: SANS, fontWeight: 800, fontSize: 30, color: C.mint }}>Metered compute</div>
          <div style={{ fontFamily: SERIF, fontWeight: 900, fontSize: 56, color: C.white }}>$ / token</div>
          {/* meter ticks */}
          <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
            {[0, 1, 2, 3, 4, 5, 6].map((i) => (
              <div key={i} style={{ width: 12, height: 26 + (i % 3) * 8, borderRadius: 3, background: i / 7 < morph ? C.gold : "rgba(159,216,181,.25)" }} />
            ))}
          </div>
          <div style={{ fontFamily: SANS, fontSize: 26, color: C.mint, fontWeight: 700 }}>usage ✅</div>
        </div>
      </div>
    </Stage>
  );
};

const S4Tokens: React.FC = () => {
  const f = useCurrentFrame();
  const target = 41_000_000_000_000;
  const val = Math.floor(A(f, 6, 200, 0, target, { easing: Easing.bezier(0.3, 0.05, 0.2, 1) }));
  const num = val.toLocaleString("en-US");
  const cards = ["every prompt", "every file", "every agent loop"];
  return (
    <AbsoluteFill style={{ background: C.forestDeep }}>
      <BrollBg src="the-500m-claude-bill/broll/server-room.mp4" opacity={0.22} tint={C.forestDeep} />
      <Grain />
      <AbsoluteFill style={{ padding: PAD, justifyContent: "center", alignItems: "center" }}>
        <div style={{ fontFamily: SANS, fontWeight: 700, fontSize: 30, color: C.mint, letterSpacing: ".06em", marginBottom: 18, opacity: A(f, 0, 10, 0, 1) }}>
          AT PUBLIC CLAUDE PRICES
        </div>
        <div style={{
          fontFamily: SANS, fontWeight: 800, fontSize: 64, color: C.gold, fontVariantNumeric: "tabular-nums",
          letterSpacing: "-0.01em", textShadow: "0 6px 30px rgba(240,190,60,.3)",
        }}>{num}</div>
        <div style={{ fontFamily: SERIF, fontStyle: "italic", fontSize: 44, color: C.white, marginTop: 4 }}>tokens burned</div>

        {/* multiplying mini chat cards */}
        <div style={{ display: "flex", gap: 16, marginTop: 50, flexWrap: "wrap", justifyContent: "center", maxWidth: 820 }}>
          {cards.map((c, i) => {
            const p = spring({ frame: f - (40 + i * 14), fps: 30, config: { damping: 14, mass: 0.6 } });
            return (
              <div key={i} style={{
                opacity: p, transform: `translateY(${(1 - p) * 30}px) scale(${0.9 + p * 0.1})`,
                background: "rgba(255,251,243,.08)", border: "1.5px solid rgba(159,216,181,.35)", borderRadius: 16,
                padding: "18px 24px", fontFamily: SANS, fontWeight: 700, fontSize: 30, color: C.white, backdropFilter: "blur(4px)",
              }}>
                <span style={{ color: C.mint }}>▍</span> {c}
              </div>
            );
          })}
        </div>

        <div style={{ position: "absolute", bottom: 170, textAlign: "center", width: "100%", opacity: A(f, 70, 86, 0, 1) }}>
          <div style={{ fontFamily: SERIF, fontWeight: 900, fontSize: 60, color: C.white }}>
            $500M ≈ <span style={{ color: C.gold }}>20T–100T+ tokens</span><span style={{ fontSize: 32, verticalAlign: "super", color: C.gray }}>*</span>
          </div>
          <div style={{ fontFamily: SANS, fontSize: 24, color: C.gray, marginTop: 8 }}>*rough public-rate math; model / mix dependent</div>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

const Bar: React.FC<{ label: string; pct: number; color: string; value: string; delay: number; flat?: boolean }> =
  ({ label, pct, color, value, delay, flat }) => {
    const f = useCurrentFrame();
    const grow = A(f, delay, delay + 26, 0, pct);
    return (
      <div style={{ display: "flex", alignItems: "center", gap: 22, marginBottom: 26, width: "100%" }}>
        <div style={{ width: 330, textAlign: "right", fontFamily: SANS, fontWeight: 700, fontSize: 30, color: C.ink }}>{label}</div>
        <div style={{ flex: 1, height: 64, background: "rgba(20,16,12,.06)", borderRadius: 14, position: "relative", overflow: "hidden" }}>
          <div style={{ height: "100%", width: `${grow}%`, background: color, borderRadius: 14, boxShadow: flat ? "none" : `0 0 24px ${color}66` }} />
          <div style={{ position: "absolute", right: 18, top: 0, height: "100%", display: "flex", alignItems: "center", fontFamily: SANS, fontWeight: 800, fontSize: 30, color: flat ? C.coral : C.ink }}>{value}</div>
        </div>
      </div>
    );
  };

const S5Leaderboard: React.FC = () => {
  const f = useCurrentFrame();
  return (
    <Stage bg={C.paper} justify="center">
      <Grain />
      <div style={{ marginBottom: 14 }}>
        <Pill delay={0} bg="rgba(217,109,95,.14)" color={C.coral} border="rgba(217,109,95,.4)">Internal leaderboard</Pill>
      </div>
      <Phrase delay={4} size={84} weight={900} style={{ marginBottom: 50 }}>
        Reward usage,<br />get <span style={{ color: C.coral }}>usage.</span>
      </Phrase>
      <div style={{ width: "100%" }}>
        <Bar label="Tokens burned" pct={100} color={C.coral} value="📈" delay={20} />
        <Bar label="Features shipped" pct={16} color={C.gray} value="flat" delay={38} flat />
      </div>
      <div style={{ marginTop: 38, opacity: A(f, 60, 74, 0, 1) }}>
        <Pill bg={C.white} color={C.forest}>Uber ranked teams by AI usage · Fortune</Pill>
      </div>
      <div style={{ marginTop: 24, opacity: A(f, 72, 86, 0, 1) }}>
        <Phrase delay={72} serif={false} size={36} weight={800} color={C.ink}>More prompts ≠ more value</Phrase>
      </div>
    </Stage>
  );
};

const S6Split: React.FC = () => {
  const f = useCurrentFrame();
  return (
    <Stage bg={C.paper} justify="center">
      <Grain />
      <div style={{ textAlign: "center", width: "100%", marginBottom: 36 }}>
        <Phrase delay={2} size={78} weight={900}>This isn’t isolated.</Phrase>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 34, alignItems: "center" }}>
        <ReceiptCard
          delay={8} rotate={-2} width={900} hlStart={20} accent={C.coral}
          brand={<Mast name="The Verge" serif weight={600} size={40} italic />}
          url="theverge.com · Notepad by Tom Warren" date="May 14, 2026"
          headline="Microsoft is winding down Claude Code"
          quote={[{ t: "Microsoft begins " }, { t: "canceling Claude Code licenses", hl: true }, { t: ", shifting devs to Copilot CLI." }]}
        />
        <ReceiptCard
          delay={48} rotate={1.8} width={900} hlStart={20} accent={C.coral}
          brand={<Mast name="FORTUNE" serif weight={900} size={38} />}
          url="fortune.com/2026/05/26/uber-ai-spending" date="May 26, 2026"
          headline="Uber and the runaway AI bill"
          quote={[{ t: "Uber " }, { t: "burned through its entire 2026 AI budget in four months", hl: true }, { t: "." }]}
        />
      </div>
    </Stage>
  );
};

const NodeBox: React.FC<{ label: string; sub?: string; bg: string; color: string; delay: number; w?: number }> =
  ({ label, sub, bg, color, delay, w = 300 }) => {
    const p = useEnter(delay, 14);
    return (
      <div style={{
        width: w, padding: "20px 22px", borderRadius: 18, background: bg, color,
        opacity: p, transform: `translateY(${(1 - p) * 18}px) scale(${0.92 + p * 0.08})`,
        boxShadow: "0 14px 30px rgba(20,16,12,.12)", textAlign: "center",
      }}>
        <div style={{ fontFamily: SANS, fontWeight: 800, fontSize: 30 }}>{label}</div>
        {sub && <div style={{ fontFamily: SANS, fontSize: 22, opacity: 0.8, marginTop: 4 }}>{sub}</div>}
      </div>
    );
  };

const S7Router: React.FC = () => {
  const f = useCurrentFrame();
  const line1 = A(f, 30, 46, 0, 1);
  const line2 = A(f, 60, 78, 0, 1);
  return (
    <Stage bg={C.paperWarm} justify="center" align="center">
      <Grain />
      <div style={{ textAlign: "center", marginBottom: 50 }}>
        <Phrase delay={2} size={70} weight={900}>Route the right task</Phrase>
        <Phrase delay={10} size={70} weight={900} color={C.teal}>to the right model.</Phrase>
      </div>
      {/* diagram inside dashed boundary */}
      <div style={{ position: "relative", width: 880, border: "2.5px dashed rgba(23,61,53,.35)", borderRadius: 28, padding: "40px 30px", display: "flex", flexDirection: "column", alignItems: "center", gap: 26 }}>
        <div style={{ position: "absolute", top: -18, left: 28, background: C.paperWarm, padding: "0 14px", fontFamily: SANS, fontWeight: 800, fontSize: 24, color: C.forest }}>AI ROUTER</div>
        <NodeBox label="Employee request" bg={C.white} color={C.ink} delay={20} w={360} />
        <div style={{ width: 3, height: 26, background: C.forest, opacity: line1 }} />
        <NodeBox label="Router + budget cap" bg={C.forest} color={C.white} delay={30} w={360} />
        <div style={{ display: "flex", gap: 30, opacity: line2 }}>
          <div style={{ width: 3, height: 26, background: C.forest }} />
          <div style={{ width: 3, height: 26, background: C.forest }} />
          <div style={{ width: 3, height: 26, background: C.forest }} />
        </div>
        <div style={{ display: "flex", gap: 18, justifyContent: "center" }}>
          <NodeBox label="Cheap model" bg={C.mint} color={C.forest} delay={62} w={250} />
          <NodeBox label="Strong model" bg={C.teal} color={C.white} delay={70} w={250} />
          <NodeBox label="Human review" bg={C.gold} color={C.ink} delay={78} w={250} />
        </div>
      </div>
      <div style={{ display: "flex", gap: 14, marginTop: 34, opacity: A(f, 88, 102, 0, 1) }}>
        <Pill bg={C.white} color={C.forest}>budget cap</Pill>
        <Pill bg={C.white} color={C.forest}>alerts</Pill>
        <Pill bg={C.white} color={C.forest}>✓ ROI per workflow</Pill>
      </div>
    </Stage>
  );
};

const S8Payoff: React.FC = () => {
  const f = useCurrentFrame();
  const push = A(f, 0, 200, 1, 1.05);
  const sweep = A(f, 40, 64, 0, 100);
  return (
    <Stage bg={C.paper} justify="center" align="center">
      <Grain />
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", padding: PAD, transform: `scale(${push})` }}>
        <Phrase delay={4} size={66} weight={600} serif color={C.gray} italic style={{ marginBottom: 18, textAlign: "center" }}>Stop measuring adoption.</Phrase>
        <div style={{ textAlign: "center", position: "relative" }}>
          <Phrase delay={16} size={104} weight={900} lh={1.0} style={{ textAlign: "center" }}>
            Start measuring
          </Phrase>
          <div style={{ position: "relative", display: "inline-block", marginTop: 6 }}>
            <span style={{ position: "absolute", left: 0, bottom: 14, height: 26, width: `${sweep}%`, background: C.gold, opacity: 0.6, borderRadius: 4, transform: "skewX(-5deg)" }} />
            <Phrase delay={24} size={104} weight={900} color={C.forest} lh={1.0} style={{ position: "relative" }}>
              output&nbsp;per&nbsp;dollar.
            </Phrase>
          </div>
        </div>
        <div style={{ marginTop: 60, opacity: A(f, 80, 96, 0, 1) }}>
          <Pill bg={C.forest} color={C.white} border="transparent">Output per dollar &gt; tokens per employee</Pill>
        </div>
      </AbsoluteFill>
    </Stage>
  );
};

// ============================ MAIN ============================

export interface Claude500MReelProps {
  scenes: { id: string; comp: string; start: number; dur: number }[];
  sfx: { src: string; at: number; vol?: number }[];
  narration: string;
  music: string;
}

const SCENE_MAP: Record<string, React.FC> = {
  s1_hook: S1Hook, s2_reported: S2Reported, s3_electricity: S3Electricity, s4_tokens: S4Tokens,
  s5_leaderboard: S5Leaderboard, s6_split: S6Split, s7_router: S7Router, s8_payoff: S8Payoff,
};

export const Claude500MReel: React.FC<Claude500MReelProps> = ({ scenes, sfx, narration, music }) => {
  const { fps, durationInFrames } = useVideoConfig();
  return (
    <AbsoluteFill style={{ background: C.paper }}>
      {scenes.map((s) => {
        const Comp = SCENE_MAP[s.comp];
        if (!Comp) return null;
        return (
          <Sequence key={s.id} from={Math.round(s.start * fps)} durationInFrames={Math.round(s.dur * fps)} name={s.id}>
            <Comp />
          </Sequence>
        );
      })}

      {/* narration */}
      {narration && <Audio src={staticFile(narration)} volume={1} />}
      {/* music bed */}
      {music && (
        <Audio
          src={staticFile(music)}
          volume={(fr) => {
            const fin = interpolate(fr, [0, 45], [0, 0.09], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
            const fout = interpolate(fr, [durationInFrames - 75, durationInFrames], [0.09, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
            return Math.min(fin, fout);
          }}
        />
      )}
      {/* SFX */}
      {sfx.map((s, i) => (
        <Sequence key={`sfx-${i}`} from={Math.round(s.at * fps)} durationInFrames={fps * 3} name={`sfx-${i}`}>
          <Audio src={staticFile(s.src)} volume={s.vol ?? 0.7} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
