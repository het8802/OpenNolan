// Shared folder navigation for the two Assets panels (the agent page's right column and the
// editor's properties panel). Both walk the SAME project tree via GET /api/projects/{id}/browse,
// which lists only what a user navigates to find media — sub-folders + image/video/audio files,
// never `.mc/` (the agent's chat history), non-media files or engine internals.
// Only the navigation is shared; each panel renders its own file tiles (click-to-add in the
// editor, click-to-lightbox on the agent page).

import { useEffect, useState } from 'react'
import * as api from '../api.js'
import { IconFolder } from './icons.jsx'

// Uploads still POST /assets with a kind. Inside one of the four asset folders the folder
// decides; anywhere else fall back to the dropped file's own media type.
const UPLOAD_DIRS = {
  'assets/images': 'images', 'assets/video': 'video',
  'assets/audio': 'audio', 'assets/music': 'music',
}
export const uploadKindFor = (cwd, file) =>
  UPLOAD_DIRS[cwd] ||
  (file.type.startsWith('image/') ? 'images' : file.type.startsWith('audio/') ? 'audio' : 'video')
export const uploadDirLabel = (cwd) => (UPLOAD_DIRS[cwd] ? `assets/${UPLOAD_DIRS[cwd]}` : 'assets/, by type')

// Current folder + its entries. `refreshKey` re-lists without navigating — pass whatever the
// panel already refreshes on (a poll result, an upload tick) so agent-written files show up.
export function useFolderBrowse(projectId, refreshKey) {
  const [cwd, setCwd] = useState('')
  const [entries, setEntries] = useState([])

  useEffect(() => {
    if (!projectId) { setEntries([]); return }
    let alive = true
    api.browseProject(projectId, cwd)
      .then(r => { if (alive) setEntries(r?.entries || []) })
      .catch(() => { if (alive) setEntries([]) })
    return () => { alive = false }
  }, [projectId, cwd, refreshKey])

  // A folder the user is standing in can vanish (agent cleanup) — the listing just goes empty.
  return {
    cwd, setCwd, entries,
    dirs: entries.filter(e => e.is_dir),
    files: entries.filter(e => !e.is_dir),
  }
}

// Breadcrumb + one row per sub-folder.
export function FolderNav({ cwd, dirs, onNavigate }) {
  const crumbs = cwd ? cwd.split('/') : []
  return (
    <>
      <nav className="fb-crumbs" aria-label="Folder path">
        <button className="fb-crumb" onClick={() => onNavigate('')}>Project</button>
        {crumbs.map((name, i) => (
          <span key={name + i}>
            <span className="fb-sep">/</span>
            <button className="fb-crumb" onClick={() => onNavigate(crumbs.slice(0, i + 1).join('/'))}>{name}</button>
          </span>
        ))}
      </nav>
      {dirs.length > 0 && (
        <div className="fb-dirs">
          {dirs.map(d => (
            <button key={d.path} className="fb-dir" onClick={() => onNavigate(d.path)} title={`Open ${d.path}`}>
              <IconFolder />
              <span className="fb-dir-name">{d.name}</span>
              <span className="fb-count">{d.count || 'empty'}</span>
            </button>
          ))}
        </div>
      )}
    </>
  )
}
