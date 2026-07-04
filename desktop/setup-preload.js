'use strict';

// Preload for the first-run SETUP window only (separate from the main preload so the main
// window's bridge stays minimal). Exposes a one-way progress feed the setup page renders.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('openNolanSetup', {
  onProgress: (cb) => ipcRenderer.on('setup:progress', (_e, line) => cb(line)),
  // Determinate progress: {pct, end, label} on the GLOBAL 0-100 setup scale (main.js maps each
  // provision run's own scale into its weighted slice). `end` = where this step finishes, so the
  // page can creep the bar toward it while a long install is silent.
  onStep: (cb) => ipcRenderer.on('setup:step', (_e, s) => cb(s)),
  onDone: (cb) => ipcRenderer.on('setup:done', () => cb()),
  onError: (cb) => ipcRenderer.on('setup:error', (_e, msg) => cb(msg)),
});
