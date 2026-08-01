// Shared chat helpers used by both the agent hook (useAgentChat) and the chat view
// (ChatPanel). Kept tiny and pure so both the pipeline window and the editor can import them.

// Agent models offered in the chat-header dropdown. First entry = default/recommended.
// Ids MUST match server/agent_runner.py AGENT_MODELS (the backend validates against it).
export const AGENT_MODELS = [
  { id: 'claude-opus-4-8', label: 'Opus 4.8', recommended: true },
  { id: 'claude-sonnet-5', label: 'Sonnet 5' },
  { id: 'claude-haiku-4-5-20251001', label: 'Haiku 4.5' },
]
export const DEFAULT_MODEL = AGENT_MODELS[0].id

import {
  IconFileText, IconPencil, IconTerminal, IconSearch, IconWorld,
  IconTools, IconListCheck, IconMovie,
} from '../components/icons.jsx'

// Tool name → icon component (rendered by ChatPanel's ActivityChip).
export const TOOL_ICON = {
  Read: IconFileText,
  Write: IconPencil,
  Edit: IconPencil,
  MultiEdit: IconPencil,
  Bash: IconTerminal,
  Glob: IconSearch,
  Grep: IconSearch,
  WebSearch: IconWorld,
  WebFetch: IconWorld,
  Skill: IconTools,
  TodoWrite: IconListCheck,
  render: IconMovie,
  mcp__mc__render: IconMovie,
}

// Detect render-in-progress from a tool_use event (drives the inline render progress bar).
export function isRenderCommand(item) {
  if (item.kind !== 'tool_use') return false
  // The in-process render tool: progress bar appears on tool_use, clears on tool_result.
  if (item.name === 'mcp__mc__render' || item.name === 'render') return true
  // Other render steps still shell out to ffmpeg/remotion.
  if (item.name !== 'Bash') return false
  const d = (item.detail || '').toLowerCase()
  return d.includes('npx remotion') || d.includes('ffmpeg') || d.includes('npm run render') || d.includes('hyperframes render')
}

// Pretty-print a tool_use input for the expandable activity chip.
export function formatToolInput(item) {
  const inp = item.input || {}
  if (item.name === 'Bash') return inp.command || ''
  if (['Write', 'Edit', 'MultiEdit', 'NotebookEdit'].includes(item.name)) {
    const path = inp.file_path || inp.path || ''
    if (inp.content != null) return `${path}\n\n${inp.content}`
    if (inp.old_string != null || inp.new_string != null) {
      return `${path}\n\n- - - old - - -\n${inp.old_string || ''}\n\n+ + + new + + +\n${inp.new_string || ''}`
    }
    return path
  }
  return JSON.stringify(inp, null, 2)
}
