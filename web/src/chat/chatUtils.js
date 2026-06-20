// Shared chat helpers used by both the agent hook (useAgentChat) and the chat view
// (ChatPanel). Kept tiny and pure so both the pipeline window and the editor can import them.

export const TOOL_ICON = {
  Read: '📄',
  Write: '✏️',
  Edit: '✏️',
  MultiEdit: '✏️',
  Bash: '⌨️',
  Glob: '🔍',
  Grep: '🔍',
  WebSearch: '🌐',
  WebFetch: '🌐',
  Skill: '🛠',
  TodoWrite: '📋',
}

// Detect render-in-progress from a tool_use event (drives the inline render progress bar).
export function isRenderCommand(item) {
  if (item.kind !== 'tool_use' || item.name !== 'Bash') return false
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
