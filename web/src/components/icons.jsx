// Inline stroke icons — aesthetic, dependency-free, currentColor, 16px by default. Used across the
// dashboard (BYOK), the studio toolbar (undo/redo), etc. No emoji (RULES.md: use aesthetic icons).

export function Svg({ size = 16, ...p }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...p} />
  )
}

export const IconKey = (p) => <Svg {...p}><circle cx="7.5" cy="15.5" r="4.5" /><path d="M11 12 20 3" /><path d="m16 7 3 3" /><path d="m13.5 9.5 2.5 2.5" /></Svg>
export const IconEye = (p) => <Svg {...p}><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" /><circle cx="12" cy="12" r="3" /></Svg>
export const IconEyeOff = (p) => <Svg {...p}><path d="M9.9 4.24A9.1 9.1 0 0 1 12 4c6.5 0 10 8 10 8a18 18 0 0 1-2.16 3.19" /><path d="M6.6 6.6A18 18 0 0 0 2 12s3.5 8 10 8a9.3 9.3 0 0 0 5.4-1.6" /><path d="M9.88 9.88a3 3 0 1 0 4.24 4.24" /><path d="M2 2l20 20" /></Svg>
export const IconCheck = (p) => <Svg {...p}><path d="M20 6 9 17l-5-5" /></Svg>
export const IconX = (p) => <Svg {...p}><path d="M18 6 6 18" /><path d="m6 6 12 12" /></Svg>
// Classic undo/redo: a horizontal arrow that curves back (Lucide undo-2 / redo-2).
export const IconUndo = (p) => <Svg {...p}><path d="M9 14 4 9l5-5" /><path d="M4 9h10.5a5.5 5.5 0 0 1 5.5 5.5 5.5 5.5 0 0 1-5.5 5.5H11" /></Svg>
export const IconRedo = (p) => <Svg {...p}><path d="m15 14 5-5-5-5" /><path d="M20 9H9.5A5.5 5.5 0 0 0 4 14.5 5.5 5.5 0 0 0 9.5 20H11" /></Svg>
export const IconPlay = (p) => <Svg {...p}><path d="M6 4v16l13-8z" fill="currentColor" stroke="none" /></Svg>
export const IconMusic = (p) => <Svg {...p}><path d="M9 18V5l11-2v13" /><circle cx="6" cy="18" r="3" /><circle cx="17" cy="16" r="3" /></Svg>
export const IconRefresh = (p) => <Svg {...p}><path d="M21 12a9 9 0 1 1-2.64-6.36" /><path d="M21 3v5h-5" /></Svg>
export const IconAlert = (p) => <Svg {...p}><path d="M12 9v4" /><path d="M12 17h.01" /><path d="M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" /></Svg>

// Claude sunburst mark — a radiating burst that evokes Claude's identity, in its terracotta.
// Rendered as filled rays so it reads at button size; `color` defaults to the Claude accent.
export function ClaudeLogo({ size = 18, color = 'currentColor', ...p }) {
  const rays = []
  for (let i = 0; i < 8; i++) {
    rays.push(
      <line key={i} x1="12" y1="12" x2="12" y2="3.2"
        transform={`rotate(${i * 45} 12 12)`} />
    )
  }
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke={color} strokeWidth="2.4" strokeLinecap="round" aria-hidden="true" {...p}>
      {rays}
    </svg>
  )
}
