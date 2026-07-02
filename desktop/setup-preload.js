'use strict';

// Preload for the first-run SETUP window only (separate from the main preload so the main
// window's bridge stays minimal). Exposes a one-way progress feed the setup page renders.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('openNolanSetup', {
  onProgress: (cb) => ipcRenderer.on('setup:progress', (_e, line) => cb(line)),
  onDone: (cb) => ipcRenderer.on('setup:done', () => cb()),
  onError: (cb) => ipcRenderer.on('setup:error', (_e, msg) => cb(msg)),
});
