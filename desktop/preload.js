'use strict';

// Locked-down bridge. The UI reaches the backend over HTTP (same origin), so it
// needs nothing from Node today — expose only diagnostics. Keep this minimal:
// anything added here widens the renderer's privileges.
const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('openNolan', {
  desktop: true,
  versions: {
    electron: process.versions.electron,
    chrome: process.versions.chrome,
    node: process.versions.node,
  },
});
