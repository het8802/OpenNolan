import { Composition, CalculateMetadataFunction } from "remotion";
import { Explainer, ExplainerProps } from "./Explainer";
import {
  CinematicRenderer,
  calculateCinematicMetadata,
} from "./CinematicRenderer";
import { signalFromTomorrowWithMusicFixture } from "./cinematic/fixtures";
import { TalkingHead, TalkingHeadProps } from "./TalkingHead";
import {
  TitledVideo,
  calculateTitledVideoMetadata,
} from "./TitledVideo";
import { EndTag, EndTagProps } from "./components/EndTag";
import { HeroTitle } from "./components/HeroTitle";
import { ProductReveal, ProductRevealProps } from "./components/ProductReveal";
import { CaptionOverlay, WordCaption } from "./components/CaptionOverlay";
import { CollageBurst, CollageBurstProps } from "./CollageBurst";
import { LyricOverlay, LyricOverlayProps } from "./LyricOverlay";
import { Claude500MReel, Claude500MReelProps } from "./Claude500MReel";
import { OpenAIAdsMgrReel } from "./OpenAIAdsMgrReel";
import { CheapInferenceReel, CheapInferenceReelProps, cheapInferenceReelDefault, calcCheapInferenceMetadata } from "./CheapInferenceReel";
import { AgentSleepReel0531 } from "./AgentSleepReel";

// ---------------------------------------------------------------------------
// Theme System — prevents every video from looking like dark fintech
// ---------------------------------------------------------------------------

export interface ThemeConfig {
  primaryColor: string;
  accentColor: string;
  backgroundColor: string;
  surfaceColor: string;
  textColor: string;
  mutedTextColor: string;
  headingFont: string;
  bodyFont: string;
  monoFont: string;
  chartColors: string[];
  springConfig: { damping: number; stiffness: number; mass: number };
  transitionDuration: number;
  captionHighlightColor: string;
  captionBackgroundColor: string;
}

export const THEMES: Record<string, ThemeConfig> = {
  "clean-professional": {
    primaryColor: "#2563EB",
    accentColor: "#F59E0B",
    backgroundColor: "#FFFFFF",
    surfaceColor: "#F9FAFB",
    textColor: "#1F2937",
    mutedTextColor: "#6B7280",
    headingFont: "Inter",
    bodyFont: "Inter",
    monoFont: "JetBrains Mono",
    chartColors: ["#2563EB", "#F59E0B", "#10B981", "#8B5CF6", "#EC4899", "#06B6D4"],
    springConfig: { damping: 20, stiffness: 120, mass: 1 },
    transitionDuration: 0.4,
    captionHighlightColor: "#2563EB",
    captionBackgroundColor: "rgba(255, 255, 255, 0.85)",
  },
  "flat-motion-graphics": {
    primaryColor: "#7C3AED",
    accentColor: "#EC4899",
    backgroundColor: "#0F172A",
    surfaceColor: "#1E293B",
    textColor: "#F8FAFC",
    mutedTextColor: "#94A3B8",
    headingFont: "Space Grotesk",
    bodyFont: "Space Grotesk",
    monoFont: "Fira Code",
    chartColors: ["#7C3AED", "#EC4899", "#06B6D4", "#F59E0B", "#10B981", "#EF4444"],
    springConfig: { damping: 12, stiffness: 80, mass: 1 },
    transitionDuration: 0.3,
    captionHighlightColor: "#22D3EE",
    captionBackgroundColor: "rgba(15, 23, 42, 0.75)",
  },
  "minimalist-diagram": {
    primaryColor: "#1A1A2E",
    accentColor: "#E94560",
    backgroundColor: "#FAFAFA",
    surfaceColor: "#FFFFFF",
    textColor: "#1A1A2E",
    mutedTextColor: "#6B7280",
    headingFont: "IBM Plex Sans",
    bodyFont: "IBM Plex Sans",
    monoFont: "IBM Plex Mono",
    chartColors: ["#E94560", "#1A1A2E", "#0F3460", "#9CA3AF"],
    springConfig: { damping: 25, stiffness: 150, mass: 1 },
    transitionDuration: 0.5,
    captionHighlightColor: "#E94560",
    captionBackgroundColor: "rgba(250, 250, 250, 0.9)",
  },
  "anime-ghibli": {
    primaryColor: "#2D5016",
    accentColor: "#FFB347",
    backgroundColor: "#0A0A1A",
    surfaceColor: "#1A2332",
    textColor: "#F0E6D3",
    mutedTextColor: "#A8957E",
    headingFont: "Noto Serif JP",
    bodyFont: "Noto Sans",
    monoFont: "Fira Code",
    chartColors: ["#FFB347", "#2D5016", "#FF6B9D", "#A8E6CF", "#6B4C8A", "#E8927C"],
    springConfig: { damping: 18, stiffness: 60, mass: 1 },
    transitionDuration: 1.0,
    captionHighlightColor: "#FFB347",
    captionBackgroundColor: "rgba(10, 10, 26, 0.8)",
  },
};

// Default theme when none is specified — uses the existing dark style for backwards compatibility
export const DEFAULT_THEME = THEMES["flat-motion-graphics"];

export function resolveTheme(props: Record<string, unknown>): ThemeConfig {
  const themeName = (props.theme as string) || (props.playbook as string);
  if (themeName && THEMES[themeName]) {
    return THEMES[themeName];
  }
  // Allow custom theme passed as full object
  if (props.themeConfig && typeof props.themeConfig === "object") {
    return { ...DEFAULT_THEME, ...(props.themeConfig as Partial<ThemeConfig>) };
  }
  return DEFAULT_THEME;
}

const calculateMetadata: CalculateMetadataFunction<ExplainerProps> = async ({
  props,
}) => {
  const cuts = props.cuts || [];
  if (cuts.length === 0) {
    return { durationInFrames: 30 * 60 };
  }
  const lastEnd = Math.max(...cuts.map((c) => c.out_seconds || 0));
  // Add 1 second padding for final fade
  return { durationInFrames: Math.ceil((lastEnd + 1) * 30) };
};

const SFX_DIR = "the-500m-claude-bill/audio/sfx-v2";
const claude500MDefault: Claude500MReelProps = {
  narration: "the-500m-claude-bill/audio/narration.mp3",
  music: "the-500m-claude-bill/audio/music.mp3",
  scenes: [
    { id: "s1_hook", comp: "s1_hook", start: 0.0, dur: 4.627 },
    { id: "s2_reported", comp: "s2_reported", start: 4.627, dur: 7.51 },
    { id: "s3_electricity", comp: "s3_electricity", start: 12.137, dur: 6.092 },
    { id: "s4_tokens", comp: "s4_tokens", start: 18.229, dur: 9.839 },
    { id: "s5_leaderboard", comp: "s5_leaderboard", start: 28.068, dur: 7.896 },
    { id: "s6_split", comp: "s6_split", start: 35.964, dur: 11.365 },
    { id: "s7_router", comp: "s7_router", start: 47.329, dur: 10.538 },
    { id: "s8_payoff", comp: "s8_payoff", start: 57.867, dur: 7.646 },
  ],
  sfx: [
    { src: `${SFX_DIR}/impact-deep.mp3`, at: 0.18, vol: 0.7 },
    { src: `${SFX_DIR}/cash-chime.mp3`, at: 0.34, vol: 0.7 },
    { src: `${SFX_DIR}/swoosh.mp3`, at: 4.75, vol: 0.45 },
    { src: `${SFX_DIR}/marker.mp3`, at: 6.0, vol: 0.6 },
    { src: `${SFX_DIR}/marker.mp3`, at: 6.7, vol: 0.55 },
    { src: `${SFX_DIR}/swoosh.mp3`, at: 12.2, vol: 0.5 },
    { src: `${SFX_DIR}/power-morph.mp3`, at: 15.4, vol: 0.5 },
    { src: `${SFX_DIR}/riser.mp3`, at: 18.0, vol: 0.5 },
    { src: `${SFX_DIR}/swoosh.mp3`, at: 28.1, vol: 0.5 },
    { src: `${SFX_DIR}/data-tick.mp3`, at: 29.2, vol: 0.55 },
    { src: `${SFX_DIR}/deflate.mp3`, at: 31.6, vol: 0.5 },
    { src: `${SFX_DIR}/swoosh.mp3`, at: 35.96, vol: 0.55 },
    { src: `${SFX_DIR}/marker.mp3`, at: 37.2, vol: 0.6 },
    { src: `${SFX_DIR}/marker.mp3`, at: 38.3, vol: 0.55 },
    { src: `${SFX_DIR}/swoosh.mp3`, at: 47.4, vol: 0.45 },
    { src: `${SFX_DIR}/click.mp3`, at: 49.3, vol: 0.55 },
    { src: `${SFX_DIR}/click.mp3`, at: 50.4, vol: 0.55 },
    { src: `${SFX_DIR}/confirm.mp3`, at: 51.5, vol: 0.55 },
    { src: `${SFX_DIR}/boom-low.mp3`, at: 57.9, vol: 0.6 },
    { src: `${SFX_DIR}/resolve.mp3`, at: 59.5, vol: 0.55 },
    { src: `${SFX_DIR}/outro-swell.mp3`, at: 61.6, vol: 0.5 },
  ],
};
const calc500M: CalculateMetadataFunction<Claude500MReelProps> = async ({ props }) => {
  const last = Math.max(...props.scenes.map((s) => s.start + s.dur));
  return { durationInFrames: Math.ceil(last * 30), width: 1080, height: 1920, fps: 30 };
};

export const Root: React.FC = () => {
  return (
    <>
      <Composition
        id="CheapInferenceReel"
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        component={CheapInferenceReel as any}
        durationInFrames={2020}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={cheapInferenceReelDefault}
        calculateMetadata={calcCheapInferenceMetadata}
      />
      <Composition
        id="OpenAIAdsMgrReel"
        component={OpenAIAdsMgrReel}
        durationInFrames={1815}
        fps={30}
        width={1080}
        height={1920}
      />
      <Composition
        id="AgentSleepReel0531"
        component={AgentSleepReel0531}
        durationInFrames={1977}
        fps={30}
        width={1080}
        height={1920}
      />
      <Composition
        id="Claude500MReel"
        component={Claude500MReel}
        durationInFrames={1966}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={claude500MDefault}
        calculateMetadata={calc500M}
      />
      <Composition
        id="Explainer"
        component={Explainer}
        durationInFrames={30 * 60}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{
          cuts: [],
          overlays: [],
          captions: [],
          audio: {},
        }}
        calculateMetadata={calculateMetadata}
      />
      <Composition
        id="CinematicRenderer"
        component={CinematicRenderer}
        durationInFrames={30 * 30}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{
          scenes: [],
          titleFontSize: 78,
          titleWidth: 1320,
          signalLineCount: 18,
        }}
        calculateMetadata={calculateCinematicMetadata}
      />
      <Composition
        id="SignalFromTomorrowWithMusic"
        component={CinematicRenderer}
        durationInFrames={30 * 30}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={signalFromTomorrowWithMusicFixture}
        calculateMetadata={calculateCinematicMetadata}
      />
      <Composition
        id="TalkingHead"
        component={TalkingHead}
        durationInFrames={30 * 300}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{
          videoSrc: "",
          captions: [],
          overlays: [],
          wordsPerPage: 4,
          fontSize: 52,
          highlightColor: "#22D3EE",
        }}
      />
      <Composition
        id="TitledVideo"
        component={TitledVideo}
        durationInFrames={30 * 60}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{
          videoSrc: "",
          tagline: "home is a verb.",
          taglineInSeconds: 53.5,
          taglineOutSeconds: undefined,
          topPx: 150,
          fontSize: 148,
          accentColor: "#F5C470",
        }}
        calculateMetadata={calculateTitledVideoMetadata}
      />
      <Composition
        id="HeroTitle"
        component={HeroTitle}
        durationInFrames={30 * 17}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{
          title: "THE CALIBRATORS",
          subtitle: "The People Who Define Reality",
        }}
      />
      <Composition
        id="ProductReveal"
        component={ProductReveal}
        durationInFrames={30 * 8}
        fps={30}
        width={1280}
        height={720}
        defaultProps={{
          productImage: "airnothing/product.png",
          productName: "AirNothing Pro Max Ultra",
          price: "Starting at $999",
          tagline: "Nothing included.",
          closer: "Less is nothing.",
          accentColor: "#00D4FF",
        } as ProductRevealProps}
      />
      <Composition
        id="ProductRevealVertical"
        component={ProductReveal}
        durationInFrames={30 * 8}
        fps={30}
        width={720}
        height={1280}
        defaultProps={{
          productImage: "airnothing/product.png",
          productName: "AirNothing Pro Max Ultra",
          price: "Starting at $999",
          tagline: "Nothing included.",
          closer: "Less is nothing.",
          accentColor: "#00D4FF",
        } as ProductRevealProps}
      />
      <Composition
        id="CaptionOverlayOnly"
        component={CaptionOverlay}
        durationInFrames={30 * 300}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{
          words: [] as WordCaption[],
          wordsPerPage: 3,
          fontSize: 58,
          highlightColor: "#FACC15",
          backgroundColor: "rgba(15, 23, 42, 0.75)",
        }}
      />
      <Composition
        id="CollageBurst"
        component={CollageBurst}
        durationInFrames={30 * 30}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{
          backgroundSrc: "",
          backgroundInSeconds: 0,
          curtainStartSeconds: 1.5,
          curtainEndSeconds: 3.0,
          clips: [],
        } as CollageBurstProps}
      />
      <Composition
        id="LyricOverlay"
        component={LyricOverlay}
        durationInFrames={30 * 28}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={{
          videoSrc: "",
          lyrics: [],
          bottomY: 0.88,
        } as LyricOverlayProps}
      />
      <Composition
        id="EndTag"
        component={EndTag}
        // 5.5s at 30fps = 165 frames. Render CLI can override via --props.
        durationInFrames={165}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{
          text: "THE CITY KEEPS ITS OWN VIGIL.",
          palette: "cool_offwhite_on_black",
          fadeInSeconds: 0.6,
          holdSeconds: 4.3,
          fadeOutSeconds: 0.6,
        } as EndTagProps}
      />
      <Composition
        id="EndTagOverlay"
        component={EndTag}
        // 8.19s at 30fps = 246 frames. Render CLI can override via --props.
        // Intended to be composited on top of body footage, not concat'd.
        durationInFrames={246}
        fps={30}
        width={1920}
        height={1080}
        defaultProps={{
          text: "EARN THE LIGHT.",
          palette: "cool_offwhite_on_black",
          fadeInSeconds: 1.0,
          holdSeconds: 5.69,
          fadeOutSeconds: 1.5,
          overlay: true,
        } as EndTagProps}
      />
    </>
  );
};
