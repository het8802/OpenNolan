/**
 * OpenAI Ads Manager Reel — Greg-editorial narration-led informational reel (1080×1920).
 * 7 fully animated scenes, no talking head. Warm editorial palette with forest/mint/coral accents.
 * 49.95s total · 1498 frames · 30fps
 */
import React from "react";
import {
  AbsoluteFill,
  Audio,
  Sequence,
  Series,
  staticFile,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
  Easing,
} from "remotion";
import { loadFont as loadFraunces } from "@remotion/google-fonts/Fraunces";
import { loadFont as loadInter } from "@remotion/google-fonts/Inter";

const { fontFamily: SERIF } = loadFraunces("normal", {
  weights: ["400", "600", "900"],
  subsets: ["latin"],
});
const { fontFamily: SANS } = loadInter("normal", {
  weights: ["400", "600", "700", "800"],
  subsets: ["latin"],
});

// Greg editorial palette
const C = {
  paper:       "#F5EFE6",
  paperWarm:   "#F7EDE7",
  mint:        "#9FD8B5",
  mintStrong:  "#68B894",
  teal:        "#4FAE91",
  forest:      "#173D35",
  forestDeep:  "#0E2B25",
  coral:       "#D96D5F",
  gold:        "#F4C84A",
  charcoal:    "#111111",
  gray:        "#898984",
  whiteWarm:   "#FFF8EC",
  ink:         "#1A1714",
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

const PAD = 80;
const ease = Easing.bezier(0.22, 0.9, 0.24, 1);

const A = (f: number, a: number, b: number, from: number, to: number) =>
  interpolate(f, [a, b], [from, to], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: ease,
  });

function useEnter(delay = 0, dur = 18) {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  return spring({
    frame: f - delay,
    fps,
    config: { damping: 200, mass: 0.7 },
    durationInFrames: dur,
  });
}

// ─── Shared UI atoms ──────────────────────────────────────────────────────────

const Grain: React.FC = () => (
  <AbsoluteFill
    style={{
      backgroundImage:
        "radial-gradient(circle at 18% 12%, rgba(255,255,255,.55), transparent 42%), " +
        "radial-gradient(circle at 82% 78%, rgba(217,109,95,.04), transparent 48%)",
      mixBlendMode: "soft-light",
      opacity: 0.65,
      pointerEvents: "none",
    }}
  />
);

const Stage: React.FC<{
  children: React.ReactNode;
  bg?: string;
  justify?: string;
  align?: string;
}> = ({ children, bg = C.paper, justify = "center", align = "flex-start" }) => (
  <AbsoluteFill
    style={{
      background: bg,
      padding: PAD,
      display: "flex",
      flexDirection: "column",
      justifyContent: justify as React.CSSProperties["justifyContent"],
      alignItems: align as React.CSSProperties["alignItems"],
    }}
  >
    {children}
  </AbsoluteFill>
);

const Phrase: React.FC<{
  children: React.ReactNode;
  delay?: number;
  size?: number;
  color?: string;
  weight?: number;
  serif?: boolean;
  lh?: number;
  ls?: string;
  italic?: boolean;
  style?: React.CSSProperties;
}> = ({
  children,
  delay = 0,
  size = 72,
  color = C.ink,
  weight = 700,
  serif = true,
  lh = 1.0,
  ls,
  italic = false,
  style,
}) => {
  const p = useEnter(delay, 18);
  return (
    <div
      style={{
        fontFamily: serif ? SERIF : SANS,
        fontWeight: weight,
        fontSize: size,
        color,
        lineHeight: lh,
        letterSpacing: ls ?? (serif ? "-0.02em" : "-0.01em"),
        fontStyle: italic ? "italic" : "normal",
        opacity: p,
        transform: `translateY(${(1 - p) * 18}px)`,
        filter: `blur(${(1 - p) * 3}px)`,
        ...style,
      }}
    >
      {children}
    </div>
  );
};

const Pill: React.FC<{
  children: React.ReactNode;
  delay?: number;
  bg?: string;
  color?: string;
  border?: string;
  size?: number;
}> = ({
  children,
  delay = 0,
  bg = C.whiteWarm,
  color = C.forest,
  border = "rgba(23,61,53,.18)",
  size = 24,
}) => {
  const p = useEnter(delay, 14);
  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
        padding: "10px 22px",
        borderRadius: 999,
        background: bg,
        color,
        border: `1.5px solid ${border}`,
        fontFamily: SANS,
        fontWeight: 700,
        fontSize: size,
        letterSpacing: ".03em",
        textTransform: "uppercase",
        whiteSpace: "nowrap",
        opacity: p,
        transform: `translateY(${(1 - p) * 10}px) scale(${0.96 + p * 0.04})`,
        boxShadow: "0 6px 20px rgba(20,16,12,.08)",
      }}
    >
      {children}
    </div>
  );
};

const BrowserBar: React.FC<{ url: string; delay?: number }> = ({ url, delay = 0 }) => {
  const p = useEnter(delay, 14);
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "14px 20px",
        background: "#ECEAE3",
        borderBottom: "1px solid rgba(20,16,12,.07)",
        opacity: p,
      }}
    >
      <div style={{ width: 13, height: 13, borderRadius: 99, background: C.coral }} />
      <div style={{ width: 13, height: 13, borderRadius: 99, background: C.gold }} />
      <div style={{ width: 13, height: 13, borderRadius: 99, background: C.mintStrong }} />
      <div
        style={{
          flex: 1,
          marginLeft: 10,
          background: C.whiteWarm,
          borderRadius: 999,
          padding: "7px 16px",
          fontFamily: SANS,
          fontSize: 22,
          color: C.gray,
          border: "1px solid rgba(20,16,12,.07)",
          letterSpacing: "0.01em",
        }}
      >
        🔒 {url}
      </div>
    </div>
  );
};

// ─── SCENE 1: SplitScreenHook (frames 0-158, 5.3s) ───────────────────────────

const S1SplitScreenHook: React.FC = () => {
  const f = useCurrentFrame();
  const splitProgress = A(f, 0, 20, 0, 1);
  const stampScale = spring({
    frame: f - 18,
    fps: 30,
    config: { damping: 9, mass: 0.8, stiffness: 180 },
  });
  const pillFade = A(f, 8, 24, 0, 1);

  return (
    <AbsoluteFill style={{ background: C.ink, overflow: "hidden" }}>
      {/* Left panel — Google / keyword (greyscale coral tint) */}
      <div
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          width: "50%",
          height: "100%",
          background: "linear-gradient(160deg, #3C2826 0%, #2A1C1A 100%)",
          borderRight: `2px solid rgba(217,109,95,.35)`,
          transform: `translateX(${(1 - splitProgress) * -60}px)`,
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          gap: 24,
          padding: "0 36px",
        }}
      >
        {/* Google "G" wordmark area */}
        <div
          style={{
            width: 100,
            height: 100,
            borderRadius: 24,
            background: "rgba(255,255,255,.06)",
            border: "2px solid rgba(217,109,95,.4)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            opacity: A(f, 6, 20, 0, 1),
          }}
        >
          <div
            style={{
              fontFamily: SANS,
              fontWeight: 800,
              fontSize: 48,
              color: C.coral,
              letterSpacing: "-0.02em",
            }}
          >
            G
          </div>
        </div>
        {/* Fake search bar */}
        <div
          style={{
            width: "90%",
            background: "rgba(255,255,255,.08)",
            borderRadius: 999,
            padding: "14px 20px",
            fontFamily: SANS,
            fontSize: 22,
            color: "rgba(255,255,255,.45)",
            border: "1px solid rgba(217,109,95,.25)",
            opacity: A(f, 10, 24, 0, 1),
          }}
        >
          🔍 best seo agency for saas…
        </div>
        <div
          style={{
            textAlign: "center",
            fontFamily: SANS,
            fontWeight: 700,
            fontSize: 26,
            color: C.coral,
            letterSpacing: ".04em",
            textTransform: "uppercase",
            opacity: A(f, 14, 28, 0, 1),
          }}
        >
          KEYWORD AUCTION
        </div>
        <div
          style={{
            textAlign: "center",
            fontFamily: SANS,
            fontSize: 22,
            color: "rgba(255,255,255,.45)",
            maxWidth: 320,
            opacity: A(f, 18, 32, 0, 1),
          }}
        >
          Fight for position. Bid on keywords.
        </div>
      </div>

      {/* Right panel — ChatGPT / conversation (forest mint) */}
      <div
        style={{
          position: "absolute",
          right: 0,
          top: 0,
          width: "50%",
          height: "100%",
          background: "linear-gradient(160deg, #173D35 0%, #0E2B25 100%)",
          transform: `translateX(${(1 - splitProgress) * 60}px)`,
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          gap: 24,
          padding: "0 36px",
        }}
      >
        {/* ChatGPT icon */}
        <div
          style={{
            width: 100,
            height: 100,
            borderRadius: 24,
            background: "rgba(159,216,181,.12)",
            border: `2px solid ${C.mintStrong}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            opacity: A(f, 6, 20, 0, 1),
          }}
        >
          <div
            style={{
              fontFamily: SANS,
              fontWeight: 800,
              fontSize: 36,
              color: C.mint,
            }}
          >
            AI
          </div>
        </div>
        {/* Chat bubble */}
        <div
          style={{
            background: "rgba(159,216,181,.12)",
            border: `1px solid ${C.mintStrong}`,
            borderRadius: 18,
            padding: "14px 18px",
            fontFamily: SANS,
            fontSize: 22,
            color: C.mint,
            width: "90%",
            opacity: A(f, 10, 24, 0, 1),
          }}
        >
          "How do I get more demo calls for my SaaS?"
        </div>
        <div
          style={{
            textAlign: "center",
            fontFamily: SANS,
            fontWeight: 700,
            fontSize: 26,
            color: C.mintStrong,
            letterSpacing: ".04em",
            textTransform: "uppercase",
            opacity: A(f, 14, 28, 0, 1),
          }}
        >
          CONVERSATION MOMENT
        </div>
        <div
          style={{
            textAlign: "center",
            fontFamily: SANS,
            fontSize: 22,
            color: "rgba(159,216,181,.6)",
            maxWidth: 320,
            opacity: A(f, 18, 32, 0, 1),
          }}
        >
          Show up inside buying decisions.
        </div>
      </div>

      {/* Gold pill at top */}
      <div
        style={{
          position: "absolute",
          top: 100,
          left: "50%",
          transform: "translateX(-50%)",
          opacity: pillFade,
          zIndex: 10,
        }}
      >
        <div
          style={{
            background: C.gold,
            color: C.forestDeep,
            fontFamily: SANS,
            fontWeight: 800,
            fontSize: 22,
            letterSpacing: ".06em",
            textTransform: "uppercase",
            padding: "10px 24px",
            borderRadius: 999,
            whiteSpace: "nowrap",
            boxShadow: "0 8px 24px rgba(244,200,74,.3)",
          }}
        >
          ● NEW AD CHANNEL
        </div>
      </div>

      {/* Stamp text — "KEYWORDS ARE NOT THE GAME ANYMORE" */}
      <div
        style={{
          position: "absolute",
          bottom: 160,
          left: "50%",
          transform: `translateX(-50%) scale(${0.4 + stampScale * 0.6}) rotate(-2deg)`,
          opacity: stampScale,
          zIndex: 20,
          textAlign: "center",
          width: 860,
        }}
      >
        <div
          style={{
            fontFamily: SERIF,
            fontWeight: 900,
            fontSize: 54,
            color: C.whiteWarm,
            lineHeight: 1.05,
            letterSpacing: "-0.01em",
            textShadow: "0 4px 24px rgba(0,0,0,.8)",
            border: `3px solid ${C.gold}`,
            padding: "16px 28px",
            borderRadius: 12,
            background: "rgba(0,0,0,.45)",
            backdropFilter: "blur(4px)",
          }}
        >
          KEYWORDS ARE NOT<br />THE GAME ANYMORE
        </div>
      </div>
    </AbsoluteFill>
  );
};

// ─── SCENE 2: HookStatementCard (frames 0-100, 3.37s) ────────────────────────

const S2HookStatementCard: React.FC = () => {
  const f = useCurrentFrame();
  const cardP = useEnter(14, 22);

  return (
    <Stage bg={C.paperWarm} justify="center">
      <Grain />
      <div style={{ marginBottom: 24 }}>
        <Pill delay={0} bg={`rgba(217,109,95,.14)`} color={C.coral} border={`rgba(217,109,95,.45)`}>
          Breaking now
        </Pill>
      </div>
      <Phrase delay={6} size={84} weight={900} lh={1.0} style={{ marginBottom: 10 }}>
        The beta Ads
      </Phrase>
      <Phrase delay={10} size={84} weight={900} lh={1.0} style={{ marginBottom: 10 }}>
        Manager is live.
      </Phrase>
      <div style={{ marginTop: 28 }}>
        <Phrase delay={20} size={50} weight={600} lh={1.25} color={C.gray} serif={false}>
          But here's what most
        </Phrase>
        <Phrase delay={24} size={50} weight={600} lh={1.25} color={C.gray} serif={false}>
          are missing —
        </Phrase>
      </div>

      {/* Browser card overlay — ads.openai.com */}
      <div
        style={{
          marginTop: 48,
          background: C.whiteWarm,
          borderRadius: 20,
          overflow: "hidden",
          boxShadow: "0 20px 60px rgba(20,16,12,.16)",
          border: "1px solid rgba(20,16,12,.06)",
          opacity: cardP,
          transform: `translateY(${(1 - cardP) * 40}px) scale(${0.96 + cardP * 0.04})`,
        }}
      >
        <BrowserBar url="ads.openai.com" delay={14} />
        <div style={{ padding: "28px 32px 32px" }}>
          <div
            style={{
              fontFamily: SANS,
              fontWeight: 800,
              fontSize: 28,
              color: C.forestDeep,
              marginBottom: 10,
              letterSpacing: ".02em",
            }}
          >
            OpenAI Ads Manager
          </div>
          <div
            style={{
              fontFamily: SANS,
              fontSize: 24,
              color: C.gray,
              lineHeight: 1.4,
              marginBottom: 18,
            }}
          >
            Reach people in their buying moments<br />
            inside ChatGPT conversations.
          </div>
          <div
            style={{
              display: "inline-block",
              background: C.forest,
              color: C.whiteWarm,
              fontFamily: SANS,
              fontWeight: 700,
              fontSize: 22,
              padding: "10px 22px",
              borderRadius: 999,
              letterSpacing: ".02em",
            }}
          >
            Create Advertiser Account →
          </div>
        </div>
      </div>

      {/* Bottom pill */}
      <div style={{ marginTop: 36, opacity: A(f, 50, 66, 0, 1) }}>
        <div
          style={{
            display: "inline-block",
            background: C.coral,
            color: C.whiteWarm,
            fontFamily: SANS,
            fontWeight: 800,
            fontSize: 26,
            padding: "12px 26px",
            borderRadius: 999,
            letterSpacing: ".04em",
            textTransform: "uppercase",
          }}
        >
          NOT GOOGLE ADS 2.0
        </div>
      </div>
    </Stage>
  );
};

// ─── SCENE 3: StatementCard (frames 0-74, 2.5s) ───────────────────────────────

const S3StatementCard: React.FC = () => {
  const f = useCurrentFrame();
  const underlineW = A(f, 18, 52, 0, 100);

  return (
    <Stage bg={C.paper} justify="center">
      <Grain />
      <Phrase delay={0} size={92} weight={900} lh={0.96}>
        This is
      </Phrase>
      <div style={{ position: "relative", display: "inline-block", marginTop: 4 }}>
        <Phrase delay={6} size={92} weight={900} lh={0.96} color={C.coral}>
          not Google Ads
        </Phrase>
        {/* coral underline draw-on */}
        <div
          style={{
            position: "absolute",
            bottom: -6,
            left: 0,
            height: 8,
            width: `${underlineW}%`,
            background: C.coral,
            borderRadius: 4,
            opacity: 0.85,
          }}
        />
      </div>
      <Phrase delay={12} size={92} weight={900} lh={0.96} style={{ marginTop: 4 }}>
        with a new logo.
      </Phrase>

      {/* Small browser mockup */}
      <div
        style={{
          marginTop: 56,
          background: C.whiteWarm,
          borderRadius: 16,
          overflow: "hidden",
          boxShadow: "0 14px 40px rgba(20,16,12,.13)",
          border: "1px solid rgba(20,16,12,.06)",
          opacity: A(f, 20, 36, 0, 1),
          transform: `translateY(${A(f, 20, 36, 20, 0)}px)`,
          width: "100%",
        }}
      >
        <BrowserBar url="ads.openai.com" delay={20} />
        <div
          style={{
            padding: "20px 26px",
            fontFamily: SANS,
            fontSize: 22,
            color: C.gray,
            display: "flex",
            alignItems: "center",
            gap: 12,
          }}
        >
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: 8,
              background: C.mint,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontWeight: 700,
              color: C.forestDeep,
              fontSize: 16,
            }}
          >
            AI
          </div>
          <span>OpenAI Ads Manager — Beta</span>
          <div
            style={{
              marginLeft: "auto",
              background: C.gold,
              color: C.forestDeep,
              fontWeight: 700,
              fontSize: 18,
              padding: "4px 12px",
              borderRadius: 999,
            }}
          >
            Beta
          </div>
        </div>
      </div>
    </Stage>
  );
};

// ─── SCENE 4: ComparisonDiagram (frames 0-260, 8.7s) ─────────────────────────

const S4ComparisonDiagram: React.FC = () => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();

  const leftP = spring({ frame: f - 14, fps, config: { damping: 18, mass: 0.9, stiffness: 100 } });
  const rightP = spring({ frame: f - 30, fps, config: { damping: 18, mass: 0.9, stiffness: 100 } });
  const arrowP = A(f, 48, 64, 0, 1);
  const labelP = A(f, 60, 80, 0, 1);
  const stampP = spring({ frame: f - 70, fps, config: { damping: 10, mass: 0.7, stiffness: 160 } });

  return (
    <Stage bg={C.paper} justify="center" align="center">
      <Grain />
      <div style={{ textAlign: "center", marginBottom: 50, width: "100%" }}>
        <Phrase delay={2} size={68} weight={900} style={{ textAlign: "center" }}>
          Different platforms.
        </Phrase>
        <Phrase delay={8} size={68} weight={900} color={C.teal} style={{ textAlign: "center" }}>
          Different intent.
        </Phrase>
      </div>

      {/* Two pill cards */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 0,
          width: "100%",
        }}
      >
        {/* Google card */}
        <div
          style={{
            flex: 1,
            background: "linear-gradient(145deg, rgba(217,109,95,.12) 0%, rgba(217,109,95,.06) 100%)",
            border: `2px solid ${C.coral}`,
            borderRadius: 24,
            padding: "36px 28px",
            textAlign: "center",
            boxShadow: "0 16px 40px rgba(217,109,95,.12)",
            opacity: leftP,
            transform: `translateX(${(1 - leftP) * -60}px) scale(${0.92 + leftP * 0.08})`,
          }}
        >
          <div
            style={{
              width: 72,
              height: 72,
              borderRadius: 18,
              background: "rgba(217,109,95,.15)",
              border: `2px solid ${C.coral}`,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              margin: "0 auto 20px",
              fontFamily: SANS,
              fontWeight: 800,
              fontSize: 32,
              color: C.coral,
            }}
          >
            G
          </div>
          <div
            style={{
              fontFamily: SANS,
              fontWeight: 800,
              fontSize: 28,
              color: C.coral,
              letterSpacing: ".04em",
              textTransform: "uppercase",
              marginBottom: 14,
            }}
          >
            Google
          </div>
          <div
            style={{
              fontFamily: SERIF,
              fontWeight: 700,
              fontSize: 38,
              color: C.ink,
              lineHeight: 1.1,
              marginBottom: 12,
              letterSpacing: "-0.02em",
            }}
          >
            Keyword<br />auction
          </div>
          <div
            style={{
              fontFamily: SANS,
              fontSize: 22,
              color: C.gray,
              lineHeight: 1.4,
            }}
          >
            Fight for search position<br />against every competitor
          </div>
        </div>

        {/* Arrow */}
        <div
          style={{
            padding: "0 16px",
            fontFamily: SANS,
            fontWeight: 800,
            fontSize: 40,
            color: C.forest,
            opacity: arrowP,
            flexShrink: 0,
          }}
        >
          →
        </div>

        {/* ChatGPT card */}
        <div
          style={{
            flex: 1,
            background: "linear-gradient(145deg, rgba(79,174,145,.15) 0%, rgba(23,61,53,.08) 100%)",
            border: `2px solid ${C.teal}`,
            borderRadius: 24,
            padding: "36px 28px",
            textAlign: "center",
            boxShadow: "0 16px 40px rgba(79,174,145,.12)",
            opacity: rightP,
            transform: `translateX(${(1 - rightP) * 60}px) scale(${0.92 + rightP * 0.08})`,
          }}
        >
          <div
            style={{
              width: 72,
              height: 72,
              borderRadius: 18,
              background: "rgba(79,174,145,.18)",
              border: `2px solid ${C.teal}`,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              margin: "0 auto 20px",
              fontFamily: SANS,
              fontWeight: 800,
              fontSize: 28,
              color: C.mintStrong,
            }}
          >
            AI
          </div>
          <div
            style={{
              fontFamily: SANS,
              fontWeight: 800,
              fontSize: 28,
              color: C.teal,
              letterSpacing: ".04em",
              textTransform: "uppercase",
              marginBottom: 14,
            }}
          >
            ChatGPT
          </div>
          <div
            style={{
              fontFamily: SERIF,
              fontWeight: 700,
              fontSize: 38,
              color: C.ink,
              lineHeight: 1.1,
              marginBottom: 12,
              letterSpacing: "-0.02em",
            }}
          >
            Conversation<br />moment
          </div>
          <div
            style={{
              fontFamily: SANS,
              fontSize: 22,
              color: C.gray,
              lineHeight: 1.4,
            }}
          >
            Show up inside buying<br />decisions in real time
          </div>
        </div>
      </div>

      {/* KEYWORDS → CONVERSATIONS label */}
      <div
        style={{
          marginTop: 48,
          opacity: labelP,
          display: "flex",
          alignItems: "center",
          gap: 20,
          justifyContent: "center",
          width: "100%",
        }}
      >
        <div
          style={{
            fontFamily: SANS,
            fontWeight: 800,
            fontSize: 28,
            color: C.coral,
            letterSpacing: ".04em",
            textTransform: "uppercase",
          }}
        >
          KEYWORDS
        </div>
        <div style={{ fontFamily: SANS, fontWeight: 700, fontSize: 28, color: C.gray }}>→</div>
        <div
          style={{
            fontFamily: SANS,
            fontWeight: 800,
            fontSize: 28,
            color: C.teal,
            letterSpacing: ".04em",
            textTransform: "uppercase",
          }}
        >
          CONVERSATIONS
        </div>
      </div>

      {/* Save-worthy stamp */}
      <div
        style={{
          position: "absolute",
          bottom: 110,
          right: PAD,
          opacity: stampP,
          transform: `scale(${0.5 + stampP * 0.5}) rotate(4deg)`,
        }}
      >
        <div
          style={{
            border: `3px solid ${C.forest}`,
            color: C.forest,
            fontFamily: SANS,
            fontWeight: 800,
            fontSize: 20,
            padding: "8px 16px",
            borderRadius: 8,
            letterSpacing: ".05em",
            textTransform: "uppercase",
            background: "transparent",
          }}
        >
          SAVE THIS
        </div>
      </div>
    </Stage>
  );
};

// ─── SCENE 5: UIWalkthroughCard (frames 0-241, 8.07s) ────────────────────────

const S5UIWalkthroughCard: React.FC = () => {
  const f = useCurrentFrame();
  const cardP = useEnter(4, 20);

  // Steps appear at intervals — ~34 frames apart aligned to click SFX
  // Click SFX at absolute: 21.2, 22.6, 23.7, 24.6, 25.5s
  // In scene local frames (scene starts at 596 = 19.87s):
  // 21.2 - 19.87 = 1.33s = ~40f; 22.6-19.87=2.73s=~82f; 23.7-19.87=3.83s=~115f; 24.6-19.87=4.73s=~142f; 25.5-19.87=4.63s=~169f
  const STEP_DELAYS = [32, 76, 110, 138, 164];

  const steps = [
    { n: "1", label: "ads.openai.com" },
    { n: "2", label: "Create advertiser account" },
    { n: "3", label: "Add business details" },
    { n: "4", label: "Set billing" },
    { n: "5", label: "Build campaign" },
  ];

  return (
    <Stage bg={C.paperWarm} justify="center">
      <Grain />
      <div style={{ marginBottom: 20 }}>
        <Pill delay={0} bg={C.forest} color={C.mint} border="transparent">
          ● Beta Ads Manager
        </Pill>
      </div>
      <Phrase delay={4} size={72} weight={900} lh={1.0} style={{ marginBottom: 32 }}>
        Get started in 5 steps.
      </Phrase>

      {/* Browser chrome card */}
      <div
        style={{
          background: C.whiteWarm,
          borderRadius: 24,
          overflow: "hidden",
          boxShadow: "0 24px 60px rgba(20,16,12,.15)",
          border: "1px solid rgba(20,16,12,.06)",
          opacity: cardP,
          transform: `translateY(${(1 - cardP) * 30}px)`,
          width: "100%",
        }}
      >
        <BrowserBar url="ads.openai.com/campaigns/new" delay={4} />
        <div style={{ padding: "28px 32px 32px" }}>
          {steps.map((step, i) => {
            const p = spring({
              frame: f - STEP_DELAYS[i],
              fps: 30,
              config: { damping: 200, mass: 0.6 },
              durationInFrames: 14,
            });
            return (
              <div
                key={i}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 20,
                  marginBottom: i < 4 ? 22 : 0,
                  opacity: p,
                  transform: `translateX(${(1 - p) * -16}px)`,
                }}
              >
                <div
                  style={{
                    width: 48,
                    height: 48,
                    borderRadius: 14,
                    background: C.forest,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontFamily: SANS,
                    fontWeight: 800,
                    fontSize: 24,
                    color: C.mint,
                    flexShrink: 0,
                  }}
                >
                  {step.n}
                </div>
                <div
                  style={{
                    fontFamily: SANS,
                    fontWeight: 600,
                    fontSize: 28,
                    color: C.ink,
                    letterSpacing: "-0.01em",
                  }}
                >
                  {step.label}
                </div>
                {i === 4 && (
                  <div
                    style={{
                      marginLeft: "auto",
                      background: C.mint,
                      color: C.forestDeep,
                      fontWeight: 700,
                      fontSize: 18,
                      padding: "4px 14px",
                      borderRadius: 999,
                    }}
                  >
                    ✓ Live
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Bottom stat pills */}
      <div
        style={{
          display: "flex",
          gap: 16,
          marginTop: 30,
          flexWrap: "wrap",
          opacity: A(f, 174, 195, 0, 1),
        }}
      >
        <Pill delay={174} bg={C.gold} color={C.forestDeep} border="transparent">
          CPC bidding
        </Pill>
        <Pill delay={180} bg={C.whiteWarm} color={C.forest}>
          Performance tracking ✓
        </Pill>
      </div>
    </Stage>
  );
};

// ─── SCENE 6: PromptBubbles (frames 0-403, 13.47s) ───────────────────────────

const S6PromptBubbles: React.FC = () => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Bubble pop SFX at absolute: 30.0, 33.0, 36.5s
  // Scene starts at 27.94s
  // Local: 30.0-27.94=2.06s=~62f; 33.0-27.94=5.06s=~152f; 36.5-27.94=8.56s=~257f
  const BUBBLE_DELAYS = [55, 148, 250];

  const bubbles = [
    "My landing page isn't converting.",
    "Compare SEO agencies for a SaaS startup.",
    "How do I get more demo calls?",
  ];

  const cursor = (delay: number) => {
    const age = f - delay;
    if (age < 0) return null;
    const blink = Math.floor(age / 15) % 2 === 0;
    return (
      <span
        style={{
          display: "inline-block",
          width: 2,
          height: "1em",
          background: blink ? C.mint : "transparent",
          verticalAlign: "text-bottom",
          marginLeft: 2,
        }}
      />
    );
  };

  return (
    <AbsoluteFill
      style={{
        background: `linear-gradient(160deg, ${C.forestDeep} 0%, #091E19 100%)`,
        padding: PAD,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
      }}
    >
      <Grain />
      {/* Headline */}
      <div style={{ marginBottom: 20 }}>
        <Phrase delay={0} size={54} weight={700} color={C.mint} serif={false} lh={1.1}>
          The real unlock is context.
        </Phrase>
      </div>
      <Phrase delay={6} size={40} weight={400} color="rgba(159,216,181,.65)" serif={false} lh={1.3} italic style={{ marginBottom: 48 }}>
        Ask: what conversation should my product appear in?
      </Phrase>

      {/* Chat bubbles */}
      <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
        {bubbles.map((text, i) => {
          const p = spring({
            frame: f - BUBBLE_DELAYS[i],
            fps,
            config: { damping: 20, mass: 0.8, stiffness: 100 },
          });
          return (
            <div
              key={i}
              style={{
                opacity: p,
                transform: `translateY(${(1 - p) * 30}px) scale(${0.94 + p * 0.06})`,
                display: "flex",
                alignItems: "flex-start",
                gap: 16,
              }}
            >
              {/* User icon */}
              <div
                style={{
                  width: 48,
                  height: 48,
                  borderRadius: 12,
                  background: "rgba(159,216,181,.15)",
                  border: `1.5px solid ${C.mintStrong}`,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontFamily: SANS,
                  fontWeight: 700,
                  fontSize: 22,
                  color: C.mint,
                  flexShrink: 0,
                  marginTop: 6,
                }}
              >
                U
              </div>
              {/* Bubble */}
              <div
                style={{
                  background: "rgba(255,251,243,.06)",
                  border: "1.5px solid rgba(159,216,181,.3)",
                  borderRadius: 18,
                  borderTopLeftRadius: 4,
                  padding: "18px 24px",
                  fontFamily: SANS,
                  fontWeight: 500,
                  fontSize: 30,
                  color: C.whiteWarm,
                  lineHeight: 1.35,
                  backdropFilter: "blur(4px)",
                  maxWidth: 760,
                }}
              >
                {text}
                {cursor(BUBBLE_DELAYS[i])}
              </div>
            </div>
          );
        })}
      </div>

      {/* Bottom label */}
      <div
        style={{
          position: "absolute",
          bottom: 100,
          left: PAD,
          right: PAD,
          opacity: A(f, 270, 295, 0, 1),
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 18,
            borderTop: `1.5px solid rgba(159,216,181,.2)`,
            paddingTop: 24,
          }}
        >
          <div
            style={{
              fontFamily: SANS,
              fontWeight: 700,
              fontSize: 26,
              color: C.coral,
              letterSpacing: ".04em",
              textTransform: "uppercase",
            }}
          >
            Search intent
          </div>
          <div style={{ fontFamily: SANS, fontSize: 26, color: C.gray }}>→</div>
          <div
            style={{
              fontFamily: SANS,
              fontWeight: 700,
              fontSize: 26,
              color: C.teal,
              letterSpacing: ".04em",
              textTransform: "uppercase",
            }}
          >
            Conversation intent
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
};

// ─── SCENE 7: CTABoard (frames 0-255, 8.53s) ─────────────────────────────────

const S7CTABoard: React.FC = () => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Tick SFX at absolute: 42.0, 43.0, 44.0s → scene starts at 41.4s
  // Local: 42-41.4=0.6s=18f; 43-41.4=1.6s=48f; 44-41.4=2.6s=78f
  // outro-payoff at 45.5s → 45.5-41.4=4.1s=~123f
  const CHECK_DELAYS = [20, 50, 80];
  const OPENAI_DELAY = 120;

  const checkItems = ["Founder", "Agency", "SaaS operator"];

  const openaiBlockP = spring({
    frame: f - OPENAI_DELAY,
    fps,
    config: { damping: 10, mass: 0.9, stiffness: 150 },
  });

  return (
    <Stage bg={C.paper} justify="center">
      <Grain />
      <Phrase delay={0} size={68} weight={900} lh={1.05} style={{ marginBottom: 8 }}>
        Founder, agency,
      </Phrase>
      <Phrase delay={4} size={68} weight={900} lh={1.05} color={C.teal} style={{ marginBottom: 48 }}>
        SaaS operator?
      </Phrase>

      {/* Checklist */}
      <div style={{ display: "flex", flexDirection: "column", gap: 20, marginBottom: 48 }}>
        {checkItems.map((item, i) => {
          const p = spring({
            frame: f - CHECK_DELAYS[i],
            fps,
            config: { damping: 200, mass: 0.7 },
            durationInFrames: 14,
          });
          return (
            <div
              key={i}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 20,
                opacity: p,
                transform: `translateX(${(1 - p) * -20}px)`,
              }}
            >
              <div
                style={{
                  width: 48,
                  height: 48,
                  borderRadius: 14,
                  background: C.mint,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontWeight: 800,
                  fontSize: 28,
                  color: C.forestDeep,
                  flexShrink: 0,
                }}
              >
                ✓
              </div>
              <div
                style={{
                  fontFamily: SANS,
                  fontWeight: 700,
                  fontSize: 44,
                  color: C.ink,
                  letterSpacing: "-0.01em",
                }}
              >
                {item}
              </div>
            </div>
          );
        })}
      </div>

      {/* COMMENT OPENAI block */}
      <div
        style={{
          opacity: openaiBlockP,
          transform: `scale(${0.6 + openaiBlockP * 0.4}) translateY(${(1 - openaiBlockP) * 30}px)`,
        }}
      >
        <div
          style={{
            background: C.gold,
            borderRadius: 20,
            padding: "24px 32px",
            display: "flex",
            flexDirection: "column",
            gap: 8,
            boxShadow: "0 20px 60px rgba(244,200,74,.3)",
          }}
        >
          <div
            style={{
              fontFamily: SANS,
              fontWeight: 800,
              fontSize: 28,
              color: C.forestDeep,
              letterSpacing: ".06em",
              textTransform: "uppercase",
            }}
          >
            Comment
          </div>
          <div
            style={{
              fontFamily: SERIF,
              fontWeight: 900,
              fontSize: 72,
              color: C.forestDeep,
              lineHeight: 0.9,
              letterSpacing: "-0.02em",
            }}
          >
            OPENAI
          </div>
          <div
            style={{
              fontFamily: SANS,
              fontSize: 24,
              color: C.forest,
              lineHeight: 1.3,
              marginTop: 8,
              fontWeight: 500,
            }}
          >
            I'll send you the signup link +<br />my prompt-context checklist.
          </div>
        </div>
      </div>
    </Stage>
  );
};

// ─── SFX entries — timings scaled ×1.2116 from original 49.93s composition ────

const SFX_ENTRIES: { src: string; at: number; vol: number }[] = [
  { src: "openai-ads-manager-reel/sfx/impact-cinematic.mp3", at: 0.0,   vol: 0.9  },
  { src: "openai-ads-manager-reel/sfx/whoosh-fast.mp3",      at: 6.42,  vol: 0.7  },
  { src: "openai-ads-manager-reel/sfx/swipe-paper.mp3",      at: 13.52, vol: 0.75 },
  { src: "openai-ads-manager-reel/sfx/swipe-paper.mp3",      at: 15.75, vol: 0.75 },
  { src: "openai-ads-manager-reel/sfx/click-soft.mp3",       at: 25.69, vol: 0.65 },
  { src: "openai-ads-manager-reel/sfx/click-soft.mp3",       at: 27.38, vol: 0.65 },
  { src: "openai-ads-manager-reel/sfx/click-soft.mp3",       at: 28.71, vol: 0.65 },
  { src: "openai-ads-manager-reel/sfx/click-soft.mp3",       at: 29.80, vol: 0.65 },
  { src: "openai-ads-manager-reel/sfx/click-soft.mp3",       at: 30.90, vol: 0.65 },
  { src: "openai-ads-manager-reel/sfx/pop-bubble.mp3",       at: 36.35, vol: 0.8  },
  { src: "openai-ads-manager-reel/sfx/pop-bubble.mp3",       at: 39.98, vol: 0.8  },
  { src: "openai-ads-manager-reel/sfx/pop-bubble.mp3",       at: 44.22, vol: 0.8  },
  { src: "openai-ads-manager-reel/sfx/tick-check.mp3",       at: 50.89, vol: 0.85 },
  { src: "openai-ads-manager-reel/sfx/tick-check.mp3",       at: 52.10, vol: 0.85 },
  { src: "openai-ads-manager-reel/sfx/tick-check.mp3",       at: 53.31, vol: 0.85 },
  { src: "openai-ads-manager-reel/sfx/outro-payoff.mp3",     at: 55.13, vol: 0.9  },
];

// Scene frame map — scaled ×1.2116 to match 60.5s VO (narration_fast.mp3)
const SCENES = [
  { comp: S1SplitScreenHook,    dur: 193 },
  { comp: S2HookStatementCard,  dur: 122 },
  { comp: S3StatementCard,      dur: 91  },
  { comp: S4ComparisonDiagram,  dur: 316 },
  { comp: S5UIWalkthroughCard,  dur: 293 },
  { comp: S6PromptBubbles,      dur: 490 },
  { comp: S7CTABoard,           dur: 310 },
];

// ─── MAIN EXPORT ──────────────────────────────────────────────────────────────

export const OpenAIAdsMgrReel: React.FC = () => {
  const { durationInFrames, fps } = useVideoConfig();

  return (
    <AbsoluteFill style={{ background: C.paper }}>
      {/* ── Scene sequence ── */}
      <Series>
        {SCENES.map((s, i) => {
          const Comp = s.comp;
          return (
            <Series.Sequence key={i} durationInFrames={s.dur} name={Comp.name}>
              <Comp />
            </Series.Sequence>
          );
        })}
      </Series>

      {/* ── Continuous narration VO ── */}
      <Audio
        src={staticFile("openai-ads-manager-reel/audio/voiceover_final.mp3")}
        volume={1.0}
      />

      {/* ── Background music with fade-in / fade-out / ducking ── */}
      <Audio
        src={staticFile("openai-ads-manager-reel/music/background.mp3")}
        volume={(fr: number) => {
          const musicVol = 0.12;
          const fadeIn  = interpolate(fr, [0, 30],                          [0, musicVol], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
          const fadeOut = interpolate(fr, [durationInFrames - 90, durationInFrames], [musicVol, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
          return Math.min(fadeIn, fadeOut);
        }}
      />

      {/* ── SFX layer ── */}
      {SFX_ENTRIES.map((s, i) => (
        <Sequence
          key={`sfx-${i}`}
          from={Math.round(s.at * fps)}
          durationInFrames={fps * 3}
          name={`sfx-${i}`}
        >
          <Audio src={staticFile(s.src)} volume={s.vol} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
