import {
  AbsoluteFill,
  Sequence,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

// Word-level caption for TikTok-style highlight display
export interface WordCaption {
  word: string;
  startMs: number;
  endMs: number;
  // Marked words render bigger + in emphasisColor (remotion_caption_burn
  // sets this from its emphasis_words input). Absent = normal word.
  emphasis?: boolean;
}

// Style bundle for caption presets. Passed through verbatim from
// remotion_caption_burn's style_preset/override inputs as `captionStyle`.
// Every field is optional — the defaults reproduce the original look exactly.
export interface CaptionStyle {
  fontSize?: number;
  color?: string;
  highlightColor?: string;
  backgroundColor?: string;
  fontFamily?: string;
  fontWeight?: number;
  // false removes the rounded background box (minimal_lower, bold_outline)
  boxed?: boolean;
  borderRadius?: number;
  // distance in px from the bottom edge of the frame to the caption block
  bottomOffset?: number;
  outlineColor?: string;
  // 0 disables; >0 draws a text outline of roughly this px width
  outlineWidth?: number;
  emphasisColor?: string;
  emphasisScale?: number;
}

interface CaptionOverlayProps extends CaptionStyle {
  words: WordCaption[];
  // How many words to show at once in a "page"
  wordsPerPage?: number;
}

type ResolvedStyle = Required<CaptionStyle>;

interface CaptionPage {
  words: WordCaption[];
  startMs: number;
  endMs: number;
}

function buildPages(words: WordCaption[], wordsPerPage: number): CaptionPage[] {
  const pages: CaptionPage[] = [];
  for (let i = 0; i < words.length; i += wordsPerPage) {
    const pageWords = words.slice(i, i + wordsPerPage);
    if (pageWords.length === 0) continue;
    pages.push({
      words: pageWords,
      startMs: pageWords[0].startMs,
      endMs: pageWords[pageWords.length - 1].endMs,
    });
  }
  return pages;
}

// Text outline via stacked hard shadows. WebkitTextStroke needs paint-order
// support to not eat the glyph fill, which is inconsistent in headless
// renderers — shadow stacking works everywhere Remotion renders.
function outlineShadow(width: number, color: string): string {
  const shadows: string[] = [];
  const radii = width > 2 ? [width, Math.ceil(width / 2)] : [width];
  for (const r of radii) {
    for (let i = 0; i < 16; i++) {
      const angle = (Math.PI * 2 * i) / 16;
      const dx = (Math.cos(angle) * r).toFixed(2);
      const dy = (Math.sin(angle) * r).toFixed(2);
      shadows.push(`${dx}px ${dy}px 0 ${color}`);
    }
  }
  return shadows.join(", ");
}

const PageRenderer: React.FC<{
  page: CaptionPage;
  style: ResolvedStyle;
}> = ({ page, style }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const currentMs = page.startMs + (frame / fps) * 1000;

  // Spring entrance
  const entrance = spring({
    frame,
    fps,
    config: { damping: 18, stiffness: 120 },
  });

  const dropShadow = "0 2px 4px rgba(0,0,0,0.5)";
  const outline =
    style.outlineWidth > 0
      ? outlineShadow(style.outlineWidth, style.outlineColor)
      : "";

  return (
    <AbsoluteFill
      style={{
        justifyContent: "flex-end",
        alignItems: "center",
        paddingBottom: style.bottomOffset,
      }}
    >
      <div
        style={{
          opacity: entrance,
          transform: `translateY(${interpolate(entrance, [0, 1], [20, 0])}px)`,
          backgroundColor: style.boxed ? style.backgroundColor : "transparent",
          borderRadius: style.borderRadius,
          padding: style.boxed ? "14px 28px" : "0px 28px",
          maxWidth: "80%",
          textAlign: "center",
        }}
      >
        <span
          style={{
            fontSize: style.fontSize,
            fontWeight: style.fontWeight,
            fontFamily: style.fontFamily,
            lineHeight: 1.4,
            whiteSpace: "pre-wrap",
          }}
        >
          {page.words.map((w, i) => {
            const isActive = w.startMs <= currentMs && w.endMs > currentMs;
            const isPast = w.endMs <= currentMs;
            // Emphasized words keep their accent color through the karaoke
            // sweep (dimmed-with-alpha until reached, like normal words).
            const wordColor = w.emphasis
              ? isActive || isPast
                ? style.emphasisColor
                : `${style.emphasisColor}99`
              : isActive
                ? style.highlightColor
                : isPast
                  ? style.color
                  : `${style.color}99`;
            const glow = isActive
              ? `0 0 20px ${style.highlightColor}66`
              : "";
            return (
              <span
                key={`${w.startMs}-${i}`}
                style={{
                  color: wordColor,
                  fontSize: w.emphasis
                    ? Math.round(style.fontSize * style.emphasisScale)
                    : undefined,
                  transition: "none", // CSS transitions forbidden in Remotion
                  textShadow: [glow, outline, dropShadow]
                    .filter(Boolean)
                    .join(", "),
                }}
              >
                {w.word}{i < page.words.length - 1 ? " " : ""}
              </span>
            );
          })}
        </span>
      </div>
    </AbsoluteFill>
  );
};

export const CaptionOverlay: React.FC<CaptionOverlayProps> = ({
  words,
  wordsPerPage = 6,
  fontSize = 42,
  color = "#F8FAFC",
  highlightColor = "#22D3EE",
  backgroundColor = "rgba(15, 23, 42, 0.75)",
  fontFamily = "Space Grotesk, Inter, system-ui, sans-serif",
  fontWeight = 700,
  boxed = true,
  borderRadius = 12,
  bottomOffset = 80,
  outlineColor = "#000000",
  outlineWidth = 0,
  emphasisColor = "#FFD60A",
  emphasisScale = 1.25,
}) => {
  const { fps } = useVideoConfig();
  const pages = buildPages(words, wordsPerPage);

  const style: ResolvedStyle = {
    fontSize,
    color,
    highlightColor,
    backgroundColor,
    fontFamily,
    fontWeight,
    boxed,
    borderRadius,
    bottomOffset,
    outlineColor,
    outlineWidth,
    emphasisColor,
    emphasisScale,
  };

  return (
    <AbsoluteFill>
      {pages.map((page, i) => {
        const fromFrame = Math.round((page.startMs / 1000) * fps);
        const nextStart = pages[i + 1]?.startMs ?? page.endMs + 500;
        const duration = Math.max(
          1,
          Math.round(((nextStart - page.startMs) / 1000) * fps)
        );

        return (
          <Sequence key={i} from={fromFrame} durationInFrames={duration}>
            <PageRenderer page={page} style={style} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
