/**
 * CheapInferenceReel — "5 Questions Every AI Founder Should Be Asking"
 * 1080×1920 · 67.3s · Greg-isenberg-product-explainer style
 * Warm editorial palette: Fraunces + Inter, paper/coral/teal/gold
 */
import React from "react";
import {
  AbsoluteFill, Audio, OffthreadVideo, Sequence, staticFile,
  interpolate, spring, useCurrentFrame, useVideoConfig, Easing,
} from "remotion";
import { loadFont as loadFraunces } from "@remotion/google-fonts/Fraunces";
import { loadFont as loadInter } from "@remotion/google-fonts/Inter";
import type { CalculateMetadataFunction } from "remotion";

const { fontFamily: SERIF } = loadFraunces("normal", { weights: ["400", "600", "900"], subsets: ["latin"] });
const { fontFamily: SANS }  = loadInter("normal",   { weights: ["400", "600", "800"], subsets: ["latin"] });

// ─── palette ─────────────────────────────────────────────────────────────────
const C = {
  paper:      "#F4EEE4",
  paperWarm:  "#F8EFE6",
  forest:     "#173D35",
  forestDeep: "#0C241F",
  mint:       "#9FD8B5",
  mintStrong: "#5FAE86",
  teal:       "#3F9C82",
  coral:      "#D96D5F",
  gold:       "#F0BE3C",
  gray:       "#8C8A82",
  white:      "#FFFBF3",
  ink:        "#211C16",
};

// ─── helpers ──────────────────────────────────────────────────────────────────
const ease = Easing.bezier(0.22, 0.9, 0.24, 1);
const PAD = 84;

function useEnter(delay = 0, dur = 18) {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  return spring({ frame: f - delay, fps, config: { damping: 200, mass: 0.7 }, durationInFrames: dur });
}

const A = (f: number, a: number, b: number, from: number, to: number, opts: Record<string, unknown> = {}) =>
  interpolate(f, [a, b], [from, to], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: ease, ...opts });

// ─── design primitives ────────────────────────────────────────────────────────

const Grain: React.FC = () => (
  <AbsoluteFill style={{
    backgroundImage:
      "radial-gradient(circle at 20% 15%, rgba(255,255,255,.5), transparent 40%), " +
      "radial-gradient(circle at 85% 80%, rgba(217,109,95,.05), transparent 45%)",
    mixBlendMode: "soft-light",
    opacity: 0.6,
  }} />
);

const BrollBg: React.FC<{ src: string; opacity?: number; tint?: string }> = ({
  src, opacity = 0.18, tint = C.forestDeep,
}) => (
  <AbsoluteFill>
    <OffthreadVideo src={staticFile(src)} muted
      style={{ width: "100%", height: "100%", objectFit: "cover", opacity }} />
    <AbsoluteFill style={{ background: tint, opacity: 0.5, mixBlendMode: "multiply" }} />
  </AbsoluteFill>
);

const Stage: React.FC<{ children: React.ReactNode; bg?: string; justify?: string; align?: string }> =
  ({ children, bg = C.paper, justify = "center", align = "flex-start" }) => (
    <AbsoluteFill style={{
      background: bg, padding: PAD,
      display: "flex", flexDirection: "column",
      justifyContent: justify as React.CSSProperties["justifyContent"],
      alignItems:     align   as React.CSSProperties["alignItems"],
    }}>
      {children}
    </AbsoluteFill>
  );

const Phrase: React.FC<{
  children: React.ReactNode; delay?: number; size?: number; color?: string;
  weight?: number; serif?: boolean; lh?: number; style?: React.CSSProperties; italic?: boolean;
}> = ({ children, delay = 0, size = 64, color = C.ink, weight = 600, serif = true, lh = 1.0, style, italic }) => {
  const p = useEnter(delay, 18);
  return (
    <div style={{
      fontFamily: serif ? SERIF : SANS, fontWeight: weight, fontSize: size, color, lineHeight: lh,
      letterSpacing: serif ? "-0.02em" : "-0.01em",
      fontStyle: italic ? "italic" : "normal",
      opacity: p,
      transform: `translateY(${(1 - p) * 16}px)`,
      filter: `blur(${(1 - p) * 3}px)`,
      ...style,
    }}>{children}</div>
  );
};

const Pill: React.FC<{ children: React.ReactNode; delay?: number; bg?: string; color?: string; border?: string }> =
  ({ children, delay = 0, bg = C.white, color = C.forest, border = "rgba(23,61,53,.18)" }) => {
    const p = useEnter(delay, 14);
    return (
      <div style={{
        display: "inline-flex", alignItems: "center", gap: 8,
        padding: "10px 20px", borderRadius: 999,
        background: bg, color, border: `1.5px solid ${border}`,
        fontFamily: SANS, fontWeight: 700, fontSize: 24,
        letterSpacing: ".02em", textTransform: "uppercase", whiteSpace: "nowrap",
        opacity: p,
        transform: `translateY(${(1 - p) * 10}px) scale(${0.96 + p * 0.04})`,
        boxShadow: "0 6px 20px rgba(20,16,12,.08)",
      }}>{children}</div>
    );
  };

const Mast: React.FC<{ name: string; color?: string; serif?: boolean; weight?: number; size?: number; italic?: boolean }> =
  ({ name, color = C.ink, serif = false, weight = 800, size = 34, italic }) => (
    <span style={{
      fontFamily: serif ? SERIF : SANS, fontWeight: weight, fontSize: size, color,
      letterSpacing: serif ? "-0.01em" : "0.02em",
      fontStyle: italic ? "italic" : "normal",
    }}>{name}</span>
  );

type QuotePart = { t: string; hl?: boolean };

const ReceiptCard: React.FC<{
  brand: React.ReactNode; url: string; date: string; headline: string;
  quote: QuotePart[]; rotate?: number; delay?: number; width?: number;
  hlStart?: number; accent?: string;
}> = ({ brand, url, date, headline, quote, rotate = -2, delay = 0, width = 860, hlStart = 18, accent = C.gold }) => {
  const f = useCurrentFrame();
  const p = useEnter(delay, 20);
  const w = A(f, delay + hlStart, delay + hlStart + 20, 0, 100);
  return (
    <div style={{
      width, background: C.white, borderRadius: 24, padding: "0 0 32px 0", overflow: "hidden",
      boxShadow: "0 28px 60px rgba(20,16,12,.22)", border: "1px solid rgba(20,16,12,.06)",
      transform: `translateY(${(1 - p) * 60}px) rotate(${rotate * (1 - p)}deg) scale(${0.94 + p * 0.06})`,
opacity: p,
    }}>
      {/* browser chrome */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "14px 20px", background: "#EFEAE1", borderBottom: "1px solid rgba(20,16,12,.06)" }}>
        <div style={{ width: 12, height: 12, borderRadius: 99, background: C.coral }} />
        <div style={{ width: 12, height: 12, borderRadius: 99, background: C.gold }} />
        <div style={{ width: 12, height: 12, borderRadius: 99, background: C.mintStrong }} />
        <div style={{
          flex: 1, marginLeft: 10, background: C.white, borderRadius: 999, padding: "6px 16px",
          fontFamily: SANS, fontSize: 18, color: C.gray, border: "1px solid rgba(20,16,12,.06)",
          overflow: "hidden", whiteSpace: "nowrap" as const, textOverflow: "ellipsis",
        }}>🔒 {url}</div>
      </div>
      <div style={{ padding: "24px 32px 0 32px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
          <div>{brand}</div>
          <div style={{ fontFamily: SANS, fontSize: 20, color: C.gray, fontWeight: 600 }}>{date}</div>
        </div>
        <div style={{ fontFamily: SERIF, fontWeight: 600, fontSize: 40, lineHeight: 1.08, color: C.ink, letterSpacing: "-0.02em", marginBottom: 16 }}>
          {headline}
        </div>
        <div style={{ fontFamily: SANS, fontSize: 24, lineHeight: 1.45, color: "#4A453E", fontWeight: 400 }}>
          {quote.map((q, i) =>
            q.hl ? (
              <span key={i} style={{
                background: `linear-gradient(${accent} 0 0) left/${w}% 100% no-repeat`,
                color: C.ink, fontWeight: 700, padding: "0 2px", borderRadius: 2,
              } as React.CSSProperties}>{q.t}</span>
            ) : <span key={i}>{q.t}</span>
          )}
        </div>
      </div>
    </div>
  );
};

// Compact checklist card — used in Q1–Q5 scenes
// Pass delay={-100} for pre-settled cards; checkDelay={-100} for already-checked
const CheckCard: React.FC<{
  num: number; label: string; question: string;
  delay?: number; checked?: boolean; checkDelay?: number;
}> = ({ num, label, question, delay = 0, checked = false, checkDelay = 0 }) => {
  const f = useCurrentFrame();
  const p   = useEnter(delay, 16);
  const chk = checked ? Math.min(1, A(f, checkDelay, checkDelay + 8, 0, 1)) : 0;
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 18, width: "100%",
      background: C.white, borderRadius: 18, padding: "16px 22px",
      boxShadow: "0 8px 24px rgba(20,16,12,.10)", border: "1px solid rgba(20,16,12,.07)",
      opacity: p,
      transform: `translateX(${(1 - p) * -60}px) scale(${0.96 + p * 0.04})`,
      marginBottom: 12,
    }}>
      <div style={{
        minWidth: 50, height: 50, borderRadius: 12, background: C.coral, flexShrink: 0,
        display: "flex", alignItems: "center", justifyContent: "center",
        fontFamily: SANS, fontWeight: 800, fontSize: 26, color: C.white,
      }}>{num}</div>
      <div style={{ flex: 1 }}>
        <div style={{ fontFamily: SANS, fontWeight: 800, fontSize: 18, color: C.coral, letterSpacing: ".05em", textTransform: "uppercase" as const, marginBottom: 2 }}>{label}</div>
        <div style={{ fontFamily: SERIF, fontWeight: 600, fontSize: 24, color: C.ink, lineHeight: 1.1, letterSpacing: "-0.01em" }}>{question}</div>
      </div>
      <div style={{
        width: 32, height: 32, borderRadius: 8, flexShrink: 0,
        border: `3px solid ${chk > 0.5 ? C.teal : "rgba(20,16,12,.2)"}`,
        background: chk > 0.5 ? C.teal : "transparent",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}>
        {chk > 0.5 && <span style={{ color: C.white, fontSize: 16, fontWeight: 800, lineHeight: 1 }}>✓</span>}
      </div>
    </div>
  );
};

// ─── scene components ─────────────────────────────────────────────────────────

/** sc01 · Hook · 3.813s */
const Sc01Hook: React.FC = () => {
  const f = useCurrentFrame();
  const arrowIn = A(f, 24, 40, 0, 1);
  return (
    <Stage bg={C.paperWarm} justify="center" align="center">
      <Grain />
      <div style={{ textAlign: "center" }}>
        <div style={{ fontFamily: SANS, fontWeight: 700, fontSize: 26, color: C.coral, letterSpacing: ".1em", textTransform: "uppercase" as const, marginBottom: 22, opacity: A(f, 0, 12, 0, 1) }}>
          Stop asking this
        </div>
        <Phrase delay={4} size={82} weight={900} lh={1.0} style={{ textAlign: "center" }}>
          You're asking the
        </Phrase>
        <Phrase delay={10} size={82} weight={900} lh={1.0} style={{ textAlign: "center" }}>
          <span style={{ color: C.coral }}>wrong question</span>
        </Phrase>
        <Phrase delay={16} size={72} weight={600} lh={1.1} style={{ textAlign: "center", marginTop: 6 }}>
          about your AI product.
        </Phrase>
        <div style={{
          marginTop: 44, display: "inline-flex", alignItems: "center",
          background: C.coral, borderRadius: 16, padding: "16px 36px",
          opacity: arrowIn, transform: `scale(${0.88 + arrowIn * 0.12})`,
        }}>
          <span style={{ fontFamily: SANS, fontWeight: 800, fontSize: 34, color: C.white, letterSpacing: ".04em" }}>
            WRONG QUESTION →
          </span>
        </div>
      </div>
    </Stage>
  );
};

/** sc02 · Wrong layer stamp · 4.919s — bass-hit SFX at 2.5s in (frame 75) */
const Sc02WrongLayer: React.FC = () => {
  const f = useCurrentFrame();
  const questionIn = A(f, 4, 22, 0, 1);
  // stamp reveals at ~72 frames (2.4s), SFX fires at 75 (2.5s) — visually in sync
  const stamp = spring({ frame: f - 72, fps: 30, config: { damping: 9, mass: 0.6, stiffness: 200 } });
  return (
    <AbsoluteFill style={{ background: C.paperWarm }}>
      <Grain />
      <AbsoluteFill style={{ display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", padding: PAD }}>
        <div style={{
          background: C.white, borderRadius: 24, padding: "48px 52px", width: 900,
          boxShadow: "0 24px 60px rgba(20,16,12,.15)", border: "1px solid rgba(20,16,12,.07)",
          position: "relative",
          opacity: questionIn,
          transform: `translateY(${(1 - questionIn) * 30}px)`,
        }}>
          <div style={{ fontFamily: SANS, fontWeight: 600, fontSize: 26, color: C.gray, letterSpacing: ".06em", textTransform: "uppercase" as const, marginBottom: 20 }}>
            Most founders ask:
          </div>
          <div style={{ fontFamily: SERIF, fontWeight: 900, fontSize: 72, color: C.ink, lineHeight: 1.0, letterSpacing: "-0.02em" }}>
            "Which model is best?"
          </div>
          {/* WRONG LAYER stamp */}
          <div style={{
            position: "absolute", right: 44, top: 40,
            transform: `rotate(-14deg) scale(${0.45 + stamp * 0.55})`,
            opacity: stamp,
            border: `5px solid ${C.coral}`, color: C.coral, borderRadius: 12,
            padding: "10px 18px", background: "rgba(255,251,243,.96)",
            fontFamily: SANS, fontWeight: 800, fontSize: 36, letterSpacing: ".04em",
            textAlign: "center" as const,
          }}>
            WRONG<br />LAYER
          </div>
        </div>
        <div style={{ marginTop: 40, opacity: A(f, 108, 128, 0, 1) }}>
          <Pill bg={C.forest} color={C.white} border="transparent">
            You're optimizing the wrong thing
          </Pill>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

/** sc03 · 5 Questions hero title · 2.576s — whoosh SFX at entry */
const Sc03FiveQuestions: React.FC = () => {
  const f = useCurrentFrame();
  const bigFive = A(f, 2, 24, 0.8, 1.0);
  return (
    <Stage bg={C.paperWarm} justify="center" align="center">
      <Grain />
      {/* Giant "5" texture */}
      <div style={{
        position: "absolute", fontFamily: SERIF, fontWeight: 900, fontSize: 780,
        color: "rgba(217,109,95,.07)", lineHeight: 1,
        top: "50%", left: "50%",
        transform: `translate(-50%, -50%) scale(${bigFive})`,
        pointerEvents: "none", userSelect: "none" as const,
      }}>5</div>
      <div style={{ textAlign: "center", position: "relative" }}>
        <Phrase delay={2} size={98} weight={900} lh={1.0} style={{ textAlign: "center" }}>
          5 Questions
        </Phrase>
        <Phrase delay={8} size={70} weight={600} lh={1.1} color={C.coral} style={{ textAlign: "center", marginTop: 6 }}>
          That Actually Matter
        </Phrase>
        <div style={{ marginTop: 36, display: "flex", justifyContent: "center", opacity: A(f, 32, 52, 0, 1) }}>
          <Pill bg={C.forest} color={C.white} border="transparent">For AI founders</Pill>
        </div>
      </div>
    </Stage>
  );
};

/** sc04 · Q1 Route Cheap · 4.13s */
const Sc04Q1Route: React.FC = () => {
  const f = useCurrentFrame();
  const diagIn = A(f, 28, 50, 0, 1);
  const connDraw = A(f, 44, 68, 0, 1);
  return (
    <Stage bg={C.paper} justify="center">
      <Grain />
      <CheckCard num={1} label="Route Cheap" question="Can you route easy tasks to cheaper models?" delay={0} checked checkDelay={80} />
      {/* Routing diagram */}
      <div style={{ opacity: diagIn, transform: `translateY(${(1 - diagIn) * 16}px)` }}>
        <div style={{ background: C.white, borderRadius: 14, padding: "16px 22px", border: "1px solid rgba(20,16,12,.08)", boxShadow: "0 6px 18px rgba(20,16,12,.07)" }}>
          <div style={{ fontFamily: SANS, fontWeight: 700, fontSize: 18, color: C.gray, letterSpacing: ".06em", textTransform: "uppercase" as const, marginBottom: 14 }}>Routing logic</div>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 12 }}>
            <div style={{ background: C.paperWarm, border: `2px solid ${C.coral}`, borderRadius: 10, padding: "10px 16px", fontFamily: SANS, fontWeight: 700, fontSize: 20, color: C.ink }}>
              Request
            </div>
            <div style={{ width: `${connDraw * 40}px`, height: 2, background: C.gray, flexShrink: 0 }} />
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ background: C.mint, borderRadius: 8, padding: "8px 14px", fontFamily: SANS, fontWeight: 700, fontSize: 18, color: C.forest, opacity: connDraw }}>
                ⚡ Cheap model
              </div>
              <div style={{ background: C.coral, borderRadius: 8, padding: "8px 14px", fontFamily: SANS, fontWeight: 700, fontSize: 18, color: C.white, opacity: connDraw }}>
                🎯 Frontier model
              </div>
            </div>
          </div>
          <div style={{ marginTop: 12, fontFamily: SANS, fontSize: 18, color: C.gray, textAlign: "center" as const, opacity: connDraw }}>
            route by task complexity
          </div>
        </div>
      </div>
    </Stage>
  );
};

/** sc05 · Q2 Cache Context · 3.548s */
const Sc05Q2Cache: React.FC = () => {
  const f = useCurrentFrame();
  return (
    <Stage bg={C.paper} justify="center">
      <Grain />
      <CheckCard num={1} label="Route Cheap" question="Route easy tasks to cheap models" delay={-100} checked checkDelay={-100} />
      <CheckCard num={2} label="Cache Context" question="Can you cache repeated context?" delay={0} checked checkDelay={68} />
      <div style={{ marginTop: 12, opacity: A(f, 42, 62, 0, 1) }}>
        <div style={{ display: "flex", gap: 8, alignItems: "flex-end", marginBottom: 10 }}>
          {[0, 1, 2].map(i => (
            <div key={i} style={{
              width: 64 + i * 10, height: 12 + i * 4, background: i === 0 ? C.teal : i === 1 ? C.gold : C.paper,
              borderRadius: 3, border: "1.5px solid rgba(20,16,12,.12)", opacity: 0.7 + i * 0.1,
            }} />
          ))}
        </div>
        <Phrase delay={44} serif={false} size={26} weight={700} color={C.gray} style={{ marginTop: 4 }}>
          Same context? Pay once, reuse everywhere.
        </Phrase>
      </div>
    </Stage>
  );
};

/** sc06 · Q3 Retrieve First · 4.166s */
const Sc06Q3Retrieve: React.FC = () => {
  const f = useCurrentFrame();
  const diagIn  = A(f, 30, 50, 0, 1);
  const arrDraw = A(f, 42, 70, 0, 1);
  return (
    <Stage bg={C.paper} justify="center">
      <Grain />
      <CheckCard num={1} label="Route Cheap"    question="Route easy tasks to cheap models" delay={-100} checked checkDelay={-100} />
      <CheckCard num={2} label="Cache Context"   question="Cache repeated context"           delay={-100} checked checkDelay={-100} />
      <CheckCard num={3} label="Retrieve First"  question="Can retrieval shrink the prompt before the model sees it?" delay={0} checked checkDelay={82} />
      {/* RAG pipeline diagram */}
      <div style={{ opacity: diagIn, transform: `translateY(${(1 - diagIn) * 14}px)`, marginTop: 10 }}>
        <div style={{ background: C.white, borderRadius: 12, padding: "14px 18px", border: "1px solid rgba(20,16,12,.08)", boxShadow: "0 6px 16px rgba(20,16,12,.07)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, justifyContent: "center", flexWrap: "wrap" as const }}>
            <div style={{ background: C.paperWarm, border: `1.5px solid rgba(217,109,95,.4)`, borderRadius: 8, padding: "7px 12px", fontFamily: SANS, fontSize: 16, fontWeight: 700, color: C.ink }}>Corpus</div>
            <div style={{ width: `${arrDraw * 28}px`, height: 2, background: C.teal, flexShrink: 0 }} />
            <div style={{ opacity: arrDraw, background: C.teal, borderRadius: 8, padding: "7px 12px", fontFamily: SANS, fontSize: 16, fontWeight: 700, color: C.white }}>Retrieval</div>
            <div style={{ width: `${arrDraw * 28}px`, height: 2, background: C.teal, flexShrink: 0 }} />
            <div style={{ opacity: arrDraw, background: C.mint, borderRadius: 8, padding: "7px 12px", fontFamily: SANS, fontSize: 16, fontWeight: 700, color: C.forest }}>Focused chunk</div>
            <div style={{ width: `${arrDraw * 24}px`, height: 2, background: C.coral, flexShrink: 0 }} />
            <div style={{ opacity: arrDraw, background: C.coral, borderRadius: 8, padding: "7px 12px", fontFamily: SANS, fontSize: 16, fontWeight: 700, color: C.white }}>Model</div>
          </div>
        </div>
      </div>
    </Stage>
  );
};

/** sc07 · Q4 Cost Per Workflow · 6.778s */
const Sc07Q4Workflow: React.FC = () => {
  const f = useCurrentFrame();
  const compA = A(f, 44, 64, 0, 1);
  const compB = A(f, 64, 84, 0, 1);
  return (
    <Stage bg={C.paper} justify="center">
      <Grain />
      <CheckCard num={1} label="Route Cheap"    question="Route easy tasks to cheap models" delay={-100} checked checkDelay={-100} />
      <CheckCard num={2} label="Cache Context"   question="Cache repeated context"           delay={-100} checked checkDelay={-100} />
      <CheckCard num={3} label="Retrieve First"  question="Retrieval shrinks the prompt"     delay={-100} checked checkDelay={-100} />
      <CheckCard num={4} label="Cost Per Workflow" question="Can you measure cost per workflow — not just per token?" delay={0} checked checkDelay={100} />
      {/* Comparison strip */}
      <div style={{ marginTop: 14, display: "flex", gap: 12 }}>
        <div style={{
          flex: 1, background: "rgba(20,16,12,.04)", borderRadius: 14, padding: "16px 18px",
          border: `2px solid ${compA > 0.5 ? "rgba(217,109,95,.3)" : "rgba(20,16,12,.08)"}`,
          opacity: compA,
        }}>
          <div style={{ fontFamily: SANS, fontWeight: 800, fontSize: 22, color: C.coral }}>Cost / token ✗</div>
          <div style={{ fontFamily: SANS, fontSize: 18, color: C.gray, marginTop: 4 }}>Doesn't show value</div>
        </div>
<div style={{ display: "flex", alignItems: "center", fontFamily: SANS, fontSize: 26, color: C.teal, fontWeight: 800, opacity: compB }}>→</div>
        <div style={{
          flex: 1, background: C.teal, borderRadius: 14, padding: "16px 18px",
          boxShadow: "0 6px 20px rgba(63,156,130,.3)", opacity: compB,
        }}>
          <div style={{ fontFamily: SANS, fontWeight: 800, fontSize: 22, color: C.white }}>Cost / workflow ✓</div>
          <div style={{ fontFamily: SANS, fontSize: 18, color: "rgba(255,255,255,.8)", marginTop: 4 }}>Shows real ROI</div>
        </div>
      </div>
    </Stage>
  );
};

/** sc08 · Q5 Reserve the Frontier · 5.891s — all 5 cards check in sequence */
const Sc08Q5Frontier: React.FC = () => {
  const f = useCurrentFrame();
  const glowIn = A(f, 152, 172, 0, 1);
  return (
    <Stage bg={C.paper} justify="center">
      <Grain />
      {/* Cards 1–4: pre-settled visually; checkboxes check in rapid stagger */}
      <CheckCard num={1} label="Route Cheap"      question="Route easy tasks to cheap models"  delay={-100} checked checkDelay={100} />
      <CheckCard num={2} label="Cache Context"     question="Cache repeated context"            delay={-100} checked checkDelay={112} />
      <CheckCard num={3} label="Retrieve First"    question="Retrieval shrinks the prompt"      delay={-100} checked checkDelay={124} />
      <CheckCard num={4} label="Cost Per Workflow" question="Measure cost per workflow"         delay={-100} checked checkDelay={136} />
      {/* Card 5: springs in fresh */}
      <CheckCard num={5} label="Reserve the Frontier" question="Can you reserve frontier models for steps that actually change the outcome?" delay={0} checked checkDelay={148} />
      {/* Completion badge */}
      <div style={{ marginTop: 16, opacity: glowIn, transform: `scale(${0.9 + glowIn * 0.1})` }}>
        <Pill bg={C.teal} color={C.white} border="transparent">
          ✓ Inference-efficient stack
        </Pill>
      </div>
    </Stage>
  );
};

/** sc09a · Groq proof card · 6.3s */
const Sc09aGroq: React.FC = () => (
  <AbsoluteFill style={{ background: C.paperWarm }}>
    <BrollBg src="cheap-inference/video/server-racks.mp4" opacity={0.18} tint={C.forestDeep} />
    <Grain />
    <AbsoluteFill style={{ display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", padding: PAD }}>
      <div style={{ marginBottom: 26 }}>
        <Pill bg="rgba(217,109,95,.15)" color={C.coral} border="rgba(217,109,95,.4)">
          📰 Market signal
        </Pill>
      </div>
      <ReceiptCard
        delay={0} rotate={-2.5} width={900} hlStart={24} accent={C.gold}
        brand={<Mast name="TechCrunch" color="#0F9D58" weight={800} size={34} />}
        url="techcrunch.com · May 29, 2026"
        date="May 29, 2026"
        headline="AI chip startup Groq reportedly raising $650M"
        quote={[
          { t: "Groq is building out its " },
          { t: "inference cloud business", hl: true },
          { t: " as demand for fast, cheap inference surges." },
        ]}
      />
    </AbsoluteFill>
  </AbsoluteFill>
);

/** sc09b · OpenRouter 25T stat counter · 6.5s */
const Sc09bOpenRouter: React.FC = () => {
  const f = useCurrentFrame();
  const val     = A(f, 20, 120, 5, 25, { easing: Easing.bezier(0.3, 0.05, 0.2, 1) });
  const badgeIn = A(f, 128, 148, 0, 1);
  const labelIn = A(f, 0, 18, 0, 1);
  return (
    <Stage bg={C.paper} justify="center" align="center">
      <Grain />
      <div style={{ marginBottom: 30, opacity: labelIn, transform: `translateY(${(1 - labelIn) * -14}px)` }}>
        <Pill bg={C.forest} color={C.white} border="transparent">OpenRouter · Official</Pill>
      </div>
      <div style={{
        background: C.white, borderRadius: 28, padding: "50px 56px", width: 900,
        boxShadow: "0 28px 70px rgba(20,16,12,.18)", border: "1px solid rgba(20,16,12,.06)",
        textAlign: "center" as const, position: "relative",
      }}>
        <div style={{ fontFamily: SANS, fontWeight: 700, fontSize: 26, color: C.gray, letterSpacing: ".06em", textTransform: "uppercase" as const, marginBottom: 8 }}>
          Routing weekly
        </div>
        <div style={{
          fontFamily: SANS, fontWeight: 800, fontSize: 112, color: C.coral,
          lineHeight: 0.94, letterSpacing: "-0.02em", fontVariantNumeric: "tabular-nums",
          textShadow: "0 6px 28px rgba(217,109,95,.25)",
        }}>
          {val.toFixed(0)}T
        </div>
        <div style={{ fontFamily: SERIF, fontStyle: "italic", fontSize: 42, color: C.ink, marginTop: 6 }}>
          tokens / week
        </div>
        <div style={{ height: 10, background: "rgba(20,16,12,.06)", borderRadius: 99, margin: "26px 0 10px", overflow: "hidden" }}>
          <div style={{ height: "100%", width: `${((val - 5) / 20) * 100}%`, background: C.coral, borderRadius: 99 }} />
        </div>
        <div style={{ fontFamily: SANS, fontSize: 22, color: C.gray }}>started at 5T / week</div>
        {/* ↑5× badge */}
        <div style={{
          position: "absolute", top: 34, right: 34,
          background: C.teal, borderRadius: 12, padding: "10px 16px",
          fontFamily: SANS, fontWeight: 800, fontSize: 26, color: C.white,
          opacity: badgeIn, transform: `scale(${0.8 + badgeIn * 0.2})`,
        }}>
          ↑5× in 6 months
        </div>
      </div>
    </Stage>
  );
};

/** sc09c · Glean proof card · 6.889s */
const Sc09cGlean: React.FC = () => (
  <AbsoluteFill style={{ background: C.paperWarm }}>
    <BrollBg src="cheap-inference/video/enterprise-dashboard.mp4" opacity={0.16} tint={C.forestDeep} />
    <Grain />
    <AbsoluteFill style={{ display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", padding: PAD }}>
      <div style={{ marginBottom: 26 }}>
        <Pill bg="rgba(63,156,130,.15)" color={C.teal} border="rgba(63,156,130,.4)">
          📰 Market signal
        </Pill>
      </div>
      <ReceiptCard
        delay={0} rotate={1.8} width={900} hlStart={32} accent={C.teal}
        brand={<Mast name="TechCrunch" color="#0F9D58" weight={800} size={34} />}
        url="techcrunch.com · May 28, 2026"
        date="May 28, 2026"
        headline="Glean's top line crosses $300M as AI budget cutting becomes its major selling point"
        quote={[
          { t: "Enterprises are turning to Glean to " },
          { t: "reduce your AI bill significantly", hl: true },
          { t: " — a new enterprise selling point." },
        ]}
      />
    </AbsoluteFill>
  </AbsoluteFill>
);

/** sc10 · DeepSeek -75% dark card · 3.817s */
const Sc10DeepSeek: React.FC = () => {
  const f = useCurrentFrame();
  const cardIn  = A(f, 0, 16, 0, 1);
  const stamp   = spring({ frame: f - 46, fps: 30, config: { damping: 9, mass: 0.6, stiffness: 200 } });
  return (
    <AbsoluteFill style={{ background: C.forestDeep }}>
      <Grain />
      {/* Token stream texture */}
      <AbsoluteFill style={{
        background: "repeating-linear-gradient(90deg, transparent, transparent 40px, rgba(159,216,181,.04) 40px, rgba(159,216,181,.04) 42px)",
        opacity: A(f, 0, 18, 0, 1),
      }} />
      <AbsoluteFill style={{ display: "flex", flexDirection: "column", justifyContent: "center", alignItems: "center", padding: PAD }}>
        <div style={{
          background: "rgba(255,251,243,.06)", borderRadius: 24, padding: "50px 54px", width: 900,
          border: "1px solid rgba(255,251,243,.12)", boxShadow: "0 28px 70px rgba(0,0,0,.45)",
          position: "relative",
          opacity: cardIn, transform: `translateY(${(1 - cardIn) * 40}px)`,
        }}>
          <div style={{ fontFamily: SANS, fontWeight: 600, fontSize: 26, color: C.gray, letterSpacing: ".1em", textTransform: "uppercase" as const, marginBottom: 16 }}>
            DeepSeek pricing
          </div>
          <div style={{ fontFamily: SERIF, fontWeight: 900, fontSize: 108, color: C.coral, lineHeight: 0.92, letterSpacing: "-0.03em" }}>
            −75%
          </div>
          <div style={{ fontFamily: SANS, fontWeight: 700, fontSize: 38, color: C.white, marginTop: 16, letterSpacing: "-0.01em" }}>
            Price cut is now permanent.
          </div>
          {/* PERMANENT stamp */}
          <div style={{
            position: "absolute", right: 36, bottom: 42,
            transform: `rotate(-8deg) scale(${0.4 + stamp * 0.6})`, opacity: stamp,
            border: `4px solid ${C.gold}`, color: C.gold, borderRadius: 10, padding: "8px 16px",
            fontFamily: SANS, fontWeight: 800, fontSize: 30, letterSpacing: ".06em",
            background: "rgba(12,36,31,.96)",
          }}>
            PERMANENT
          </div>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

/** sc11 · Payoff split card · 5.232s */
const Sc11Payoff: React.FC = () => {
  const f = useCurrentFrame();
  const headerIn = A(f, 0, 22, 0, 1);
  const colLeft  = A(f, 18, 42, 0, 1);
  const colRight = A(f, 46, 70, 0, 1);
  const taglineIn = A(f, 88, 108, 0, 1);
  return (
    <Stage bg={C.paperWarm} justify="center">
      <Grain />
      <div style={{ marginBottom: 26, opacity: headerIn, transform: `translateY(${(1 - headerIn) * -18}px)` }}>
        <div style={{ fontFamily: SERIF, fontWeight: 600, fontStyle: "italic", fontSize: 44, color: C.gray, lineHeight: 1.1 }}>
          These 5 questions separate
        </div>
      </div>
      <div style={{ display: "flex", gap: 16, width: "100%" }}>
        <div style={{
          flex: 1, background: C.teal, borderRadius: 22, padding: "34px 26px",
          boxShadow: "0 16px 44px rgba(63,156,130,.3)",
          opacity: colLeft, transform: `translateX(${(1 - colLeft) * -40}px)`,
        }}>
          <div style={{ fontFamily: SANS, fontWeight: 800, fontSize: 52, color: C.white, lineHeight: 1.0, marginBottom: 10 }}>✓</div>
          <div style={{ fontFamily: SERIF, fontWeight: 700, fontSize: 36, color: C.white, lineHeight: 1.05, letterSpacing: "-0.01em" }}>
            Profitable AI Product
          </div>
          <div style={{ fontFamily: SANS, fontSize: 20, color: "rgba(255,255,255,.8)", marginTop: 10 }}>
            Cost-efficient · Measurable ROI
          </div>
        </div>
        <div style={{
          flex: 1, background: "rgba(20,16,12,.05)", borderRadius: 22, padding: "34px 26px",
          border: "2px solid rgba(217,109,95,.3)",
          opacity: colRight, transform: `translateX(${(1 - colRight) * 40}px)`,
        }}>
          <div style={{ fontFamily: SANS, fontWeight: 800, fontSize: 52, color: C.coral, lineHeight: 1.0, marginBottom: 10 }}>✗</div>
          <div style={{ fontFamily: SERIF, fontWeight: 700, fontSize: 36, color: C.ink, lineHeight: 1.05, letterSpacing: "-0.01em" }}>
            Expensive Demo
          </div>
          <div style={{ fontFamily: SANS, fontSize: 20, color: C.gray, marginTop: 10 }}>
            Cost spiral · No clear ROI
          </div>
        </div>
      </div>
      <div style={{ marginTop: 28, opacity: taglineIn }}>
        <Phrase delay={88} serif={false} size={28} weight={700} color={C.forest}>
          The market is already pricing around this.
        </Phrase>
      </div>
    </Stage>
  );
};

/** sc12 · CTA — COMMENT → INFERENCE · 2.743s */
const Sc12CTA: React.FC = () => {
  const f = useCurrentFrame();
  const cardIn  = A(f, 0, 18, 0, 1);
  const inferIn = A(f, 8, 28, 0, 1);
  const pulse   = A(f, 0, 82, 1.0, 1.04, { easing: Easing.bezier(0.45, 0.05, 0.55, 0.95) });
  return (
    <Stage bg={C.paper} justify="center" align="center">
      <Grain />
      <div style={{ textAlign: "center" as const, opacity: cardIn, transform: `translateY(${(1 - cardIn) * 20}px)` }}>
        <div style={{ fontFamily: SANS, fontWeight: 700, fontSize: 26, color: C.gray, letterSpacing: ".08em", textTransform: "uppercase" as const, marginBottom: 18 }}>
          Want the full checklist?
        </div>
        <div style={{
          display: "inline-flex", alignItems: "center", gap: 12,
          background: C.coral, borderRadius: 18, padding: "20px 44px",
          boxShadow: "0 16px 44px rgba(217,109,95,.35)",
          transform: `scale(${pulse})`,
        }}>
          <span style={{ fontFamily: SANS, fontWeight: 800, fontSize: 40, color: C.white, letterSpacing: ".01em" }}>
            COMMENT →
          </span>
        </div>
        <div style={{
          marginTop: 10, fontFamily: SERIF, fontWeight: 900, fontSize: 94, color: C.ink,
          lineHeight: 1.0, letterSpacing: "-0.02em",
          opacity: inferIn,
          transform: `scale(${0.96 + inferIn * 0.04})`,
        }}>
          INFERENCE
        </div>
        <div style={{ marginTop: 14, fontFamily: SANS, fontSize: 24, color: C.gray }}>
          Full 5-question checklist
        </div>
      </div>
    </Stage>
  );
};

// ─── scene registry ───────────────────────────────────────────────────────────
const SCENE_MAP: Record<string, React.FC> = {
  sc01:  Sc01Hook,
  sc02:  Sc02WrongLayer,
  sc03:  Sc03FiveQuestions,
  sc04:  Sc04Q1Route,
  sc05:  Sc05Q2Cache,
  sc06:  Sc06Q3Retrieve,
  sc07:  Sc07Q4Workflow,
  sc08:  Sc08Q5Frontier,
  sc09a: Sc09aGroq,
  sc09b: Sc09bOpenRouter,
  sc09c: Sc09cGlean,
  sc10:  Sc10DeepSeek,
  sc11:  Sc11Payoff,
  sc12:  Sc12CTA,
};

// ─── props & defaults ─────────────────────────────────────────────────────────
export interface CheapInferenceReelProps {
  scenes:    { id: string; comp: string; start: number; dur: number }[];
  sfx:       { src: string; at: number; vol?: number }[];
  narration: string;
}

const SFX_DIR = "cheap-inference/sfx";

export const cheapInferenceReelDefault: CheapInferenceReelProps = {
  narration: "cheap-inference/audio/narration.mp3",
  scenes: [
    { id: "sc01",  comp: "sc01",  start:  0.000, dur: 3.813 },
    { id: "sc02",  comp: "sc02",  start:  3.813, dur: 4.919 },
    { id: "sc03",  comp: "sc03",  start:  8.732, dur: 2.576 },
    { id: "sc04",  comp: "sc04",  start: 11.308, dur: 4.130 },
    { id: "sc05",  comp: "sc05",  start: 15.438, dur: 3.548 },
    { id: "sc06",  comp: "sc06",  start: 18.986, dur: 4.166 },
    { id: "sc07",  comp: "sc07",  start: 23.152, dur: 6.778 },
    { id: "sc08",  comp: "sc08",  start: 29.930, dur: 5.891 },
    { id: "sc09a", comp: "sc09a", start: 35.821, dur: 6.300 },
    { id: "sc09b", comp: "sc09b", start: 42.121, dur: 6.500 },
    { id: "sc09c", comp: "sc09c", start: 48.621, dur: 6.889 },
    { id: "sc10",  comp: "sc10",  start: 55.510, dur: 3.817 },
    { id: "sc11",  comp: "sc11",  start: 59.327, dur: 5.232 },
    { id: "sc12",  comp: "sc12",  start: 64.559, dur: 2.743 },
  ],
  sfx: [
    { src: `${SFX_DIR}/bass-hit.mp3`,     at:  6.313, vol: 0.60 }, // sc02 stamp
    { src: `${SFX_DIR}/whoosh-fast.mp3`,  at:  8.732, vol: 0.50 }, // sc03 entry
    { src: `${SFX_DIR}/card-slam.mp3`,    at: 11.508, vol: 0.45 }, // sc04 Q1 card
    { src: `${SFX_DIR}/card-slam.mp3`,    at: 15.638, vol: 0.45 }, // sc05 Q2 card
    { src: `${SFX_DIR}/card-slam.mp3`,    at: 19.186, vol: 0.45 }, // sc06 Q3 card
    { src: `${SFX_DIR}/card-slam.mp3`,    at: 23.352, vol: 0.45 }, // sc07 Q4 card
    { src: `${SFX_DIR}/card-slam.mp3`,    at: 30.130, vol: 0.45 }, // sc08 Q5 card
    { src: `${SFX_DIR}/resolve-chime.mp3`,at: 34.930, vol: 0.55 }, // sc08 all-5 complete
    { src: `${SFX_DIR}/card-slam.mp3`,    at: 36.021, vol: 0.55 }, // sc09a Groq card
    { src: `${SFX_DIR}/marker-sweep.mp3`, at: 37.821, vol: 0.55 }, // sc09a highlight
    { src: `${SFX_DIR}/data-tick.mp3`,    at: 42.621, vol: 0.40 }, // sc09b counter
    { src: `${SFX_DIR}/card-slam.mp3`,    at: 48.821, vol: 0.55 }, // sc09c Glean card
    { src: `${SFX_DIR}/marker-sweep.mp3`, at: 50.621, vol: 0.55 }, // sc09c highlight
    { src: `${SFX_DIR}/bass-hit.mp3`,     at: 56.010, vol: 0.62 }, // sc10 DeepSeek stamp
    { src: `${SFX_DIR}/resolve-chime.mp3`,at: 59.827, vol: 0.55 }, // sc11 payoff
    { src: `${SFX_DIR}/outro-resolve.mp3`,at: 64.559, vol: 0.50 }, // sc12 CTA
  ],
};

// ─── calculateMetadata ────────────────────────────────────────────────────────
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const calcCheapInferenceMetadata: CalculateMetadataFunction<any> = async ({ props }) => {
  const last = Math.max(...(props.scenes as { start: number; dur: number }[]).map((s) => s.start + s.dur));
  return { durationInFrames: Math.ceil(last * 30), width: 1080, height: 1920, fps: 30 };
};

// ─── main composition ─────────────────────────────────────────────────────────
export const CheapInferenceReel: React.FC<CheapInferenceReelProps> = ({ scenes, sfx, narration }) => {
  const { fps } = useVideoConfig();
  return (
    <AbsoluteFill style={{ background: C.paper }}>
      {/* scenes */}
      {scenes.map((s) => {
        const Comp = SCENE_MAP[s.comp];
        if (!Comp) return null;
        return (
          <Sequence
            key={s.id}
            from={Math.round(s.start * fps)}
            durationInFrames={Math.round(s.dur * fps)}
            name={s.id}
          >
            <Comp />
          </Sequence>
        );
      })}
      {/* narration — continuous single audio track */}
      {narration && <Audio src={staticFile(narration)} volume={1} />}
      {/* SFX */}
      {sfx.map((s, i) => (
        <Sequence key={`sfx-${i}`} from={Math.round(s.at * fps)} durationInFrames={fps * 4} name={`sfx-${i}`}>
          <Audio src={staticFile(s.src)} volume={s.vol ?? 0.55} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
