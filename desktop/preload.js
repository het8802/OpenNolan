'use strict';

// Locked-down bridge. The UI reaches the backend over HTTP (same origin), so it
// needs nothing from Node today — expose only diagnostics + the auto-update channel.
// Keep this minimal: anything added here widens the renderer's privileges.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('openNolan', {
  desktop: true,
  // The session id minted in main. The renderer only RECEIVES it — minting there would split
  // a session on every ⌘R and leave pre-UI launch failures unjoinable. It rides out on the
  // X-ON-Session header (web/src/analytics/track.js). Delivered via additionalArguments
  // because a sandboxed preload has no ipcRenderer.sendSync and no module state to read.
  sessionId: (process.argv.find((a) => a.startsWith('--on-session=')) || '').slice(13) || null,
  versions: {
    electron: process.versions.electron,
    chrome: process.versions.chrome,
    node: process.versions.node,
  },
  // Auto-update channel for the in-app "update ready" banner. Handlers only exist in a packaged
  // build (initAutoUpdate registers them); in dev/browser getState() rejects harmlessly and the
  // banner stays hidden.
  update: {
    getState: () => ipcRenderer.invoke('update:get-state'),   // → {version} | null
    install: () => ipcRenderer.invoke('update:install'),       // restart + install the staged build
    onDownloaded: (cb) => {                                    // returns an unsubscribe fn
      const handler = (_e, info) => cb(info);
      ipcRenderer.on('update:downloaded', handler);
      return () => ipcRenderer.removeListener('update:downloaded', handler);
    },
  },
});
