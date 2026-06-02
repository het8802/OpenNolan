import React from "react";
import {
  AbsoluteFill,
  Audio,
  OffthreadVideo,
  Img,
  staticFile,
  useCurrentFrame,
  interpolate,
  Easing,
  Sequence,
} from "remotion";

const FPS = 30;
const W = 1080;
const H = 1920;
const videoSrc = staticFile("het-openai-finance-clicko/het-main-h264.mp4");
const audioSrc = staticFile("het-openai-finance-clicko/original.wav");

const sec = (s: number) => Math.round(s * FPS);
const inRange = (f: number, a: number, b: number) => f >= sec(a) && f < sec(b);

const beats = [
  { start: 0.5, end: 5.3, mode: "full", caption: "PERSONAL FINANCE", headline: "ChatGPT can now\nsee your money", sub: "OpenAI's new finance preview" },
  { start: 5.3, end: 16.2, mode: "source", card: "openai", caption: "REAL ACCOUNTS", kicker: "OPENAI SOURCE", title: "Personal finance in ChatGPT", sub: "Pro users in the U.S. can securely connect financial accounts through Plaid." },
  { start: 16.2, end: 23.0, mode: "full", caption: "NOT BUDGETING TIPS", headline: "This isn't\nabout budgeting tips", sub: "The model could already give generic advice." },
  { start: 23.0, end: 31.6, mode: "diagram", caption: "REAL DATA + MEMORY", title: "The shift", sub: "AI now gets permissioned access to personal data, remembers context, and sits inside the decision workflow." },
  { start: 31.6, end: 44.6, mode: "layers", caption: "CONNECTOR LAYER", title: "Consumer AI stack", sub: "The product is no longer just the model." },
  { start: 44.6, end: 56.1, mode: "source", card: "caveat", caption: "HIGH TRUST CATEGORY", kicker: "OPENAI CAVEAT", title: "Not financial advice", sub: "Money is one of the highest-trust categories. OpenAI is careful about the boundary." },
  { start: 56.1, end: 64.2, mode: "trust", caption: "SENSITIVE CONTEXT", title: "The winner is trusted", sub: "The smartest AI app won't win alone. The trusted one wins." },
  { start: 64.2, end: 78.3, mode: "checklist", caption: "BUILDING AI?", title: "Founder checklist", sub: "What real system can your agent connect to?" },
  { start: 78.3, end: 85.2, mode: "full", caption: "COMMENT FINANCE", headline: "Would you connect\nyour bank account?", sub: "Comment FINANCE and I'll send the checklist." },
];

const captions = [
  { t: 0.5, d: 2.3, text: "OPENAI LAUNCHED" },
  { t: 2.4, d: 2.5, text: "PERSONAL FINANCE" },
  { t: 6.0, d: 2.4, text: "REAL ACCOUNTS" },
  { t: 8.4, d: 2.2, text: "THROUGH PLAID" },
  { t: 11.2, d: 3.0, text: "YOUR ACTUAL SPENDING" },
  { t: 16.4, d: 3.4, text: "NOT BUDGETING TIPS" },
  { t: 22.7, d: 2.5, text: "PERMISSION ACCESS" },
  { t: 26.2, d: 2.8, text: "REAL PERSONAL DATA" },
  { t: 30.0, d: 2.4, text: "WORKFLOW DECISIONS" },
  { t: 34.0, d: 2.4, text: "NOT JUST THE MODEL" },
  { t: 37.0, d: 2.3, text: "CONNECTOR LAYER" },
  { t: 40.2, d: 2.2, text: "TRUST LAYER" },
  { t: 50.7, d: 2.6, text: "MONEY = TRUST" },
  { t: 57.0, d: 3.4, text: "SENSITIVE CONTEXT" },
  { t: 64.4, d: 2.8, text: "AI STARTUP?" },
  { t: 69.9, d: 2.6, text: "REMEMBER SAFELY" },
  { t: 75.0, d: 2.4, text: "BLANK CHATBOT" },
  { t: 78.5, d: 2.7, text: "WOULD YOU CONNECT?" },
  { t: 81.1, d: 3.2, text: "COMMENT FINANCE" },
];

function sceneProgress(frame: number, start: number, end: number) {
  return interpolate(frame, [sec(start), sec(end)], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
}

const bgGradient: React.CSSProperties = {
  background: "radial-gradient(circle at 20% 10%, rgba(47, 189, 130, .35), transparent 30%), radial-gradient(circle at 90% 20%, rgba(54, 120, 255, .32), transparent 32%), linear-gradient(180deg, #07120f 0%, #081a16 48%, #050807 100%)",
};

function FullHet({ start, end, headline, sub }: { start: number; end: number; headline?: string; sub?: string }) {
  const frame = useCurrentFrame();
  const p = sceneProgress(frame, start, end);
  const scale = interpolate(p, [0, 1], [1.13, 1.20]);
  return (
    <AbsoluteFill style={{ overflow: "hidden", background: "#050807" }}>
      <OffthreadVideo
        src={videoSrc}
        startFrom={sec(start)}
        muted
        style={{ width: W, height: H, objectFit: "cover", transform: `scale(${scale}) translateY(-38px)`, filter: "contrast(1.08) saturate(1.08) brightness(.96)" }}
      />
      <div style={{ position: "absolute", inset: 0, background: "linear-gradient(180deg, rgba(0,0,0,.25), transparent 26%, rgba(0,0,0,.52) 100%)" }} />
      {headline && <HookText headline={headline} sub={sub || ""} />}
    </AbsoluteFill>
  );
}

function PIP({ start, x = 590, y = 1090, w = 410, h = 730 }: { start: number; x?: number; y?: number; w?: number; h?: number }) {
  return (
    <div style={{ position: "absolute", left: x, top: y, width: w, height: h, borderRadius: 34, overflow: "hidden", boxShadow: "0 26px 70px rgba(0,0,0,.55)", border: "3px solid rgba(255,255,255,.18)", background: "#111" }}>
      <OffthreadVideo src={videoSrc} startFrom={sec(start)} muted style={{ width: "100%", height: "100%", objectFit: "cover", transform: "scale(1.28) translateY(-36px)", filter: "contrast(1.08) saturate(1.1)" }} />
    </div>
  );
}

function HookText({ headline, sub }: { headline: string; sub: string }) {
  const frame = useCurrentFrame();
  const pop = interpolate(frame, [0, 14, 25], [0.88, 1.06, 1], { extrapolateRight: "clamp", easing: Easing.out(Easing.cubic) });
  return (
    <div style={{ position: "absolute", left: 52, right: 52, bottom: 145, transform: `scale(${pop})`, transformOrigin: "left bottom" }}>
      <div style={{ fontFamily: "Inter, Arial", color: "#E8FFF5", fontSize: 78, fontWeight: 950, lineHeight: .88, letterSpacing: -4, textShadow: "0 8px 34px rgba(0,0,0,.7)", whiteSpace: "pre-line", textTransform: "uppercase" }}>{headline}</div>
      <div style={{ marginTop: 22, display: "inline-block", color: "#07120f", background: "#7CFFA6", padding: "12px 22px", borderRadius: 999, fontSize: 30, fontWeight: 900, letterSpacing: -1 }}>{sub}</div>
    </div>
  );
}

function SourceCard({ start, kicker, title, sub, card }: { start: number; kicker: string; title: string; sub: string; card?: string }) {
  const frame = useCurrentFrame();
  const local = frame - sec(start);
  const y = interpolate(local, [0, 18], [48, 0], { extrapolateRight: "clamp", easing: Easing.out(Easing.cubic) });
  const scale = interpolate(local, [0, 20], [.96, 1], { extrapolateRight: "clamp" });
  const accent = card === "caveat" ? "#FFB55A" : "#7CFFA6";
  return (
    <AbsoluteFill style={bgGradient}>
      <div style={{ position: "absolute", left: 56, top: 115, width: 835, transform: `translateY(${y}px) scale(${scale})`, transformOrigin: "top left" }}>
        <div style={{ background: "#F8FAF7", borderRadius: 36, padding: 34, boxShadow: "0 30px 90px rgba(0,0,0,.45)", border: "2px solid rgba(255,255,255,.25)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 22 }}>
            <div style={{ width: 42, height: 42, borderRadius: 12, background: accent }} />
            <div style={{ color: "#0F261D", fontWeight: 950, fontSize: 25, letterSpacing: 2.5 }}>{kicker}</div>
          </div>
          <div style={{ height: 9, width: 260, background: "#D6DDE0", borderRadius: 99, marginBottom: 24 }} />
          <div style={{ fontSize: 58, lineHeight: .96, fontWeight: 950, letterSpacing: -3, color: "#07120f", fontFamily: "Inter, Arial" }}>{title}</div>
          <div style={{ marginTop: 22, color: "#44534D", fontSize: 34, lineHeight: 1.16, fontWeight: 700 }}>{sub}</div>
          <div style={{ marginTop: 32, padding: "20px 24px", borderRadius: 24, background: "#081A16", color: "white", fontSize: 28, lineHeight: 1.25, fontWeight: 800 }}>
            {card === "caveat" ? "ChatGPT can help you stay informed — but it is not a replacement for professional financial advice." : "Securely connect accounts, see a money dashboard, and ask ChatGPT questions grounded in financial context."}
          </div>
        </div>
      </div>
      <FinanceMiniPanel start={start + 1.6} />
      <PIP start={start} />
    </AbsoluteFill>
  );
}

function FinanceMiniPanel({ start }: { start: number }) {
  const frame = useCurrentFrame();
  const p = sceneProgress(frame, start, start + 1);
  const x = interpolate(p, [0, 1], [80, 0], { extrapolateRight: "clamp" });
  return (
    <div style={{ position: "absolute", left: 86 + x, bottom: 138, width: 440, borderRadius: 30, padding: 24, background: "rgba(9, 24, 20, .82)", border: "1px solid rgba(124,255,166,.28)", boxShadow: "0 24px 60px rgba(0,0,0,.35)" }}>
      <div style={{ color: "#7CFFA6", fontWeight: 900, fontSize: 26, marginBottom: 18 }}>FINANCE DASHBOARD</div>
      {["Spending", "Subscriptions", "Investments"].map((n, i) => <div key={n} style={{ margin: "14px 0" }}><div style={{ display: "flex", justifyContent: "space-between", color: "white", fontSize: 24, fontWeight: 800 }}><span>{n}</span><span>{[72,43,64][i]}%</span></div><div style={{ height: 12, background: "rgba(255,255,255,.14)", borderRadius: 99, marginTop: 8 }}><div style={{ width: `${[72,43,64][i]}%`, height: "100%", background: i===1 ? "#FFB55A" : "#7CFFA6", borderRadius: 99 }} /></div></div>)}
    </div>
  );
}

function DiagramScene({ start, title, sub }: { start: number; title: string; sub: string }) {
  const nodes = ["Model", "Plaid", "Memory", "Dashboard", "Decisions"];
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={bgGradient}>
      <TitleBlock title={title} sub={sub} />
      <div style={{ position: "absolute", left: 75, top: 540, width: 710 }}>
        {nodes.map((n, i) => {
          const p = sceneProgress(frame, start + i * .45, start + i * .45 + .35);
          return <div key={n} style={{ opacity: p, transform: `translateX(${interpolate(p,[0,1],[-45,0])}px)`, marginBottom: 24, display: "flex", alignItems: "center", gap: 20 }}>
            <div style={{ width: 68, height: 68, borderRadius: 22, background: i===0 ? "#fff" : "#7CFFA6", color: "#07120f", display: "grid", placeItems: "center", fontSize: 30, fontWeight: 950 }}>{i+1}</div>
            <div style={{ flex: 1, background: "rgba(255,255,255,.10)", border: "1px solid rgba(255,255,255,.16)", borderRadius: 24, padding: "20px 24px", color: "white", fontSize: 40, fontWeight: 950 }}>{n}</div>
          </div>;
        })}
      </div>
      <PIP start={start} x={620} y={1040} w={380} h={675} />
    </AbsoluteFill>
  );
}

function LayersScene({ start, title, sub }: { start: number; title: string; sub: string }) {
  const layers = ["Connector", "Memory", "Dashboard", "Approval", "Workflow"];
  const frame = useCurrentFrame();
  return <AbsoluteFill style={bgGradient}>
    <TitleBlock title={title} sub={sub} />
    <div style={{ position: "absolute", left: 70, top: 585, width: 580 }}>
      {layers.map((l, i) => {
        const p = sceneProgress(frame, start + i*.65, start + i*.65 + .45);
        return <div key={l} style={{ opacity: p, transform: `translateY(${interpolate(p,[0,1],[35,0])}px)`, marginBottom: -4, width: 520 - i*20, marginLeft: i*20, height: 126, borderRadius: 26, background: i%2 ? "#111F1A" : "#F6FFF9", color: i%2 ? "#E9FFF2" : "#07120f", display: "flex", alignItems: "center", paddingLeft: 30, fontSize: 38, fontWeight: 950, boxShadow: "0 20px 55px rgba(0,0,0,.25)", border: "2px solid rgba(124,255,166,.3)" }}>{l} layer</div>;
      })}
    </div>
    <PIP start={start} />
  </AbsoluteFill>;
}

function TrustScene({ start, title, sub }: { start: number; title: string; sub: string }) {
  return <AbsoluteFill style={bgGradient}>
    <TitleBlock title={title} sub={sub} />
    <div style={{ position: "absolute", left: 78, top: 580, width: 520, borderRadius: 38, padding: 34, background: "rgba(255,255,255,.94)", color: "#07120f", boxShadow: "0 25px 80px rgba(0,0,0,.45)" }}>
      <div style={{ fontSize: 96, fontWeight: 950, lineHeight: .8 }}>$</div>
      <div style={{ marginTop: 18, fontSize: 42, fontWeight: 950, lineHeight: 1 }}>Money is not a normal category.</div>
      <div style={{ marginTop: 24, fontSize: 30, color: "#44534D", fontWeight: 760 }}>It requires permission, context, control, and trust.</div>
      <div style={{ marginTop: 26, display: "flex", gap: 12, flexWrap: "wrap" }}>{["Secure", "Private", "Revocable", "Useful"].map(x=><span key={x} style={{ background: "#DDFBE6", padding: "10px 16px", borderRadius: 999, fontWeight: 900 }}>{x}</span>)}</div>
    </div>
    <PIP start={start} />
  </AbsoluteFill>;
}

function ChecklistScene({ start, title, sub }: { start: number; title: string; sub: string }) {
  const frame = useCurrentFrame();
  const items = ["What real system can your agent connect to?", "What context can it remember safely?", "What decision can it make better than a blank chatbot?"];
  return <AbsoluteFill style={bgGradient}>
    <TitleBlock title={title} sub={sub} />
    <div style={{ position: "absolute", left: 64, top: 565, width: 900 }}>
      {items.map((it,i)=>{
        const p=sceneProgress(frame,start+i*2.1,start+i*2.1+.6);
        return <div key={it} style={{ opacity:p, transform:`scale(${interpolate(p,[0,1],[.94,1])}) translateY(${interpolate(p,[0,1],[35,0])}px)`, marginBottom:26, borderRadius:32, padding:"28px 30px", background:"#F8FAF7", color:"#07120f", boxShadow:"0 20px 70px rgba(0,0,0,.38)", display:"flex", gap:20, alignItems:"center" }}><div style={{ width:58, height:58, borderRadius:18, background:"#7CFFA6", display:"grid", placeItems:"center", fontSize:32, fontWeight:950 }}>✓</div><div style={{ fontSize:34, fontWeight:900, lineHeight:1.08 }}>{it}</div></div>
      })}
    </div>
    <PIP start={start} x={650} y={1135} w={330} h={585}/>
  </AbsoluteFill>
}

function TitleBlock({ title, sub }: { title: string; sub: string }) {
  return <div style={{ position: "absolute", left: 64, right: 64, top: 105 }}>
    <div style={{ color: "#7CFFA6", fontSize: 28, fontWeight: 950, letterSpacing: 3 }}>HET'S AI OPERATOR NOTE</div>
    <div style={{ marginTop: 18, color: "#F0FFF5", fontSize: 76, lineHeight: .88, fontWeight: 950, letterSpacing: -4 }}>{title}</div>
    <div style={{ marginTop: 24, color: "rgba(240,255,245,.76)", fontSize: 31, lineHeight: 1.18, fontWeight: 700, maxWidth: 840 }}>{sub}</div>
  </div>
}

function ActiveVisual() {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const b = beats.find(x => t >= x.start && t < x.end) || beats[beats.length-1];
  if (b.mode === "full") return <FullHet start={b.start} end={b.end} headline={b.headline} sub={b.sub} />;
  if (b.mode === "source") return <SourceCard start={b.start} kicker={b.kicker!} title={b.title!} sub={b.sub!} card={b.card} />;
  if (b.mode === "diagram") return <DiagramScene start={b.start} title={b.title!} sub={b.sub!} />;
  if (b.mode === "layers") return <LayersScene start={b.start} title={b.title!} sub={b.sub!} />;
  if (b.mode === "trust") return <TrustScene start={b.start} title={b.title!} sub={b.sub!} />;
  return <ChecklistScene start={b.start} title={b.title!} sub={b.sub!} />;
}

function PunchCaption() {
  const frame = useCurrentFrame();
  const t = frame / FPS;
  const c = captions.find(x => t >= x.t && t < x.t + x.d);
  if (!c) return null;
  const local = frame - sec(c.t);
  const scale = interpolate(local, [0, 8, 16], [.86, 1.08, 1], { extrapolateRight: "clamp", easing: Easing.out(Easing.cubic) });
  return <div style={{ position: "absolute", left: 70, right: 70, bottom: 260, display: "flex", justifyContent: "center", pointerEvents: "none" }}>
    <div style={{ transform: `scale(${scale})`, color: "#FFFFFF", fontFamily: "Inter, Arial", fontSize: c.text.length > 18 ? 56 : 68, lineHeight: .95, fontWeight: 950, letterSpacing: -2, textAlign: "center", textShadow: "0 5px 0 #000, 0 -3px 0 #000, 3px 0 0 #000, -3px 0 0 #000, 0 12px 34px rgba(0,0,0,.75)", textTransform: "uppercase" }}>{c.text}</div>
  </div>
}

function ProgressStrip() {
  const frame = useCurrentFrame();
  const p = interpolate(frame, [0, sec(85.2)], [0, 1], { extrapolateRight: "clamp" });
  return <div style={{ position: "absolute", left: 72, right: 72, bottom: 74, height: 8, borderRadius: 99, background: "rgba(255,255,255,.18)", overflow: "hidden" }}><div style={{ width: `${p*100}%`, height: "100%", background: "#7CFFA6" }} /></div>;
}

export const HetOpenAIFinanceClicko: React.FC = () => {
  return <AbsoluteFill style={{ background: "#050807", fontFamily: "Inter, Arial, sans-serif" }}>
    <Audio src={audioSrc} />
    <ActiveVisual />
    <PunchCaption />
    <ProgressStrip />
  </AbsoluteFill>;
};
