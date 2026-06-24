// Unit tests for chatUtils.isRenderCommand — it drives the inline render progress bar.
// The in-process `render` tool must be recognized so the bar appears on tool_use and
// clears on its tool_result (same as the old background ffmpeg/remotion Bash renders).

import { describe, it, expect } from 'vitest'
import { isRenderCommand } from './chatUtils.js'

describe('isRenderCommand', () => {
  it('recognizes the in-process render tool (mcp__mc__render and render)', () => {
    expect(isRenderCommand({ kind: 'tool_use', name: 'mcp__mc__render' })).toBe(true)
    expect(isRenderCommand({ kind: 'tool_use', name: 'render' })).toBe(true)
  })

  it('still recognizes ffmpeg / remotion Bash renders', () => {
    expect(isRenderCommand({ kind: 'tool_use', name: 'Bash', detail: 'ffmpeg -i a.mp4 b.mp4' })).toBe(true)
    expect(isRenderCommand({ kind: 'tool_use', name: 'Bash', detail: 'npx remotion render' })).toBe(true)
  })

  it('ignores non-render tool_use and non-tool_use items', () => {
    expect(isRenderCommand({ kind: 'tool_use', name: 'Read', detail: 'x.py' })).toBe(false)
    expect(isRenderCommand({ kind: 'tool_use', name: 'Bash', detail: 'ls -la' })).toBe(false)
    expect(isRenderCommand({ kind: 'tool_result', tool_use_id: 't1' })).toBe(false)
    expect(isRenderCommand({ kind: 'text', text: 'render the video' })).toBe(false)
  })
})
