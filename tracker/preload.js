'use strict';
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('bot', {
  snapshot: () => ipcRenderer.invoke('snapshot'),
  equity: () => ipcRenderer.invoke('equity'),
  config: () => ipcRenderer.invoke('config'),
  start: () => ipcRenderer.invoke('start'),
  stop: () => ipcRenderer.invoke('stop'),
  kill: (reason) => ipcRenderer.invoke('kill', reason),
  revive: () => ipcRenderer.invoke('revive'),
  setMode: (mode) => ipcRenderer.invoke('setMode', mode),
  onChildLog: (fn) => ipcRenderer.on('childlog', (_e, p) => fn(p)),
  onChildExit: (fn) => ipcRenderer.on('childexit', (_e, p) => fn(p)),
});
