// Inline stroke icons — aesthetic, dependency-free, currentColor, 16px by default. Used across the
// dashboard (BYOK), the studio toolbar (undo/redo), etc. No emoji (RULES.md: use aesthetic icons).

export function Svg({ size = 16, style, ...p }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
      style={{ verticalAlign: '-0.125em', ...style }} {...p} />
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
export const IconFolder = (p) => <Svg {...p}><path d="M3 7a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.6.8l1 1.2H19a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></Svg>
export const IconRefresh = (p) => <Svg {...p}><path d="M21 12a9 9 0 1 1-2.64-6.36" /><path d="M21 3v5h-5" /></Svg>
export const IconAlert = (p) => <Svg {...p}><path d="M12 9v4" /><path d="M12 17h.01" /><path d="M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" /></Svg>
export const IconMessage = (p) => <Svg {...p}><path d="M21 11.5a8.38 8.38 0 0 1-8.5 8.5 9 9 0 0 1-4-.9L3 20l1.9-5.5a8.38 8.38 0 0 1-.9-4A8.5 8.5 0 0 1 12.5 3 8.38 8.38 0 0 1 21 11.5Z" /></Svg>

// Emoji replacements (RULES.md: aesthetic icons, no emoji). Same stroke language as above.
export const IconMovie = (p) => <Svg {...p}><rect x="4" y="4" width="16" height="16" rx="2" /><path d="M8 4v16M16 4v16M4 8h4M4 16h4M16 8h4M16 16h4M4 12h16" /></Svg>
export const IconMic = (p) => <Svg {...p}><rect x="9" y="2" width="6" height="11" rx="3" /><path d="M5 10a7 7 0 0 0 14 0" /><path d="M12 17v4" /><path d="M8 21h8" /></Svg>
export const IconStar = (p) => <Svg {...p}><path d="M12 3l2.7 5.5 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z" /></Svg>
export const IconBrain = (p) => <Svg {...p}><path d="M12 5a2.5 2.5 0 0 0-4.9-.6A2.5 2.5 0 0 0 4 6.8a2.5 2.5 0 0 0-1 4.4 2.6 2.6 0 0 0 1 4.5 2.5 2.5 0 0 0 3.2 2.7A2.5 2.5 0 0 0 12 19z" /><path d="M12 5a2.5 2.5 0 0 1 4.9-.6A2.5 2.5 0 0 1 20 6.8a2.5 2.5 0 0 1 1 4.4 2.6 2.6 0 0 1-1 4.5 2.5 2.5 0 0 1-3.2 2.7A2.5 2.5 0 0 1 12 19z" /><path d="M12 5v14" /></Svg>
export const IconTool = (p) => <Svg {...p}><path d="M15.5 4.5a3.5 3.5 0 0 0-4.7 4.6l-6 6a1.7 1.7 0 0 0 2.4 2.4l6-6a3.5 3.5 0 0 0 4.6-4.7l-2.3 2.3-2-.5-.5-2z" /></Svg>
export const IconTools = (p) => <Svg {...p}><path d="M13.5 4.5a3.4 3.4 0 0 0-4.5 4.5l-6 6 2.5 2.5 6-6a3.4 3.4 0 0 0 4.5-4.5l-2.2 2.2-1.8-.5-.5-1.8z" /><path d="M15.5 15.5 20 20" /><path d="M18 11.5 21 14.5 18.5 17 15.5 14" /></Svg>
export const IconFileText = (p) => <Svg {...p}><path d="M17 21H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2z" /><path d="M14 3v4a1 1 0 0 0 1 1h4" /><path d="M9 13h6M9 17h6M9 9h1" /></Svg>
export const IconPencil = (p) => <Svg {...p}><path d="M4 20h4L18.5 9.5a2.1 2.1 0 0 0-3-3L5 17z" /><path d="m13.5 6.5 3 3" /></Svg>
export const IconTerminal = (p) => <Svg {...p}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="m8 9 3 3-3 3M13 15h3" /></Svg>
export const IconSearch = (p) => <Svg {...p}><circle cx="10" cy="10" r="6" /><path d="M21 21l-6.5-6.5" /></Svg>
export const IconWorld = (p) => <Svg {...p}><circle cx="12" cy="12" r="9" /><path d="M3.6 9h16.8M3.6 15h16.8" /><path d="M12 3a15 15 0 0 1 0 18M12 3a15 15 0 0 0 0 18" /></Svg>
export const IconListCheck = (p) => <Svg {...p}><path d="m3.5 6 1.5 1.5L8 4.5" /><path d="m3.5 13 1.5 1.5L8 11.5" /><path d="M12 6h9M12 13h9M12 20h9" /></Svg>
export const IconListDetails = (p) => <Svg {...p}><rect x="3" y="4" width="6" height="6" rx="1" /><rect x="3" y="14" width="6" height="6" rx="1" /><path d="M13 5h8M13 9h5M13 15h8M13 19h5" /></Svg>

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
