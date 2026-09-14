'use strict';
// Desktop tracker.
//
// A viewer, not a host. The bot itself runs in a child `node bin/bot.js run`
// process and serves its state over HTTP on 127.0.0.1:8770; this window only
// polls and issues commands. Two reasons:
//
//   * Electron 33 bundles Node 20, which has no `node:sqlite`. Running the
//     engine in here would pin the whole bot to whatever Node version Electron
//     happens to ship.
//   * Closing the window should not silently close a position-holding bot. This
//     way it does not - the child keeps running unless you stop it, and if a
//     bot is already running (started from the CLI) this window just attaches
//     to it.

const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const { spawn } = require('node:child_process');

const ROOT = path.join(__dirname, '..');
const API = 'http://127.0.0.1:8770';

let win = null;
let child = null;
let weStartedIt = false;

function createWindow() {
  win = new BrowserWindow({
    width: 1180, height: 880, minWidth: 900, minHeight: 620,
    backgroundColor: '#0e1116',
    title: 'Kalshi Bot',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));

  // Renderer faults are otherwise silent; surface them where the logs are.
  win.webContents.on('render-process-gone', (_e, d) => {
    process.stderr.write('renderer gone: ' + JSON.stringify(d) + '\n');
  });
  win.webContents.on('preload-error', (_e, file, err) => {
    process.stderr.write(`preload error in ${file}: ${err.message}\n`);
  });
  win.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: 'deny' }; });
  win.on('closed', () => { win = null; });
}

// -- talking to the bot -------------------------------------------------------

async function api(pathname, { method = 'GET', body = null, timeout = 8000 } = {}) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeout);
  try {
    const r = await fetch(API + pathname, {
      method, signal: ctl.signal,
      headers: body ? { 'content-type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!r.ok) throw new Error(`${r.status}`);
    return await r.json();
  } finally { clearTimeout(t); }
}

async function botAlive() {
  try { await api('/health', { timeout: 1500 }); return true; } catch { return false; }
}

/** Start `node bin/bot.js run` as a child, and wait for its API to answer. */
async function spawnBot() {
  if (await botAlive()) return { ok: true, attached: true };
  if (child) return { ok: true };

  const nodeBin = process.env.KBOT_NODE || 'node';
  child = spawn(nodeBin, [path.join(ROOT, 'bin', 'bot.js'), 'run'], {
    cwd: ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  weStartedIt = true;

  // Forward only what the API will NOT also report. Engine log lines arrive in
  // /status and get rendered from there; relaying the child's stdout as well
  // printed every line twice, once per clock format. What is left is the
  // startup banner and any crash output - exactly the things worth seeing when
  // the bot dies before its API ever answers.
  const isEngineLine = (l) => /^\[\d{4}-\d\d-\d\dT[\d:.]+Z\]\s+\w+:/.test(l);
  const relay = (buf) => {
    const text = buf.toString();
    process.stdout.write(text);
    const keep = text.split('\n').filter((l) => l.trim() && !isEngineLine(l)).join('\n');
    if (keep && win && !win.isDestroyed()) win.webContents.send('childlog', keep);
  };
  child.stdout.on('data', relay);
  child.stderr.on('data', relay);
  child.on('exit', (code) => {
    if (win && !win.isDestroyed()) win.webContents.send('childexit', { code });
    child = null;
  });

  // The engine warms its volatility history before the first tick, so give the
  // API a few seconds to come up rather than declaring failure immediately.
  for (let i = 0; i < 30; i++) {
    await new Promise((r) => setTimeout(r, 500));
    if (await botAlive()) return { ok: true };
    if (!child) return { ok: false, reason: 'bot process exited during startup' };
  }
  return { ok: false, reason: 'bot did not answer on 127.0.0.1:8770' };
}

async function stopBot() {
  try { await api('/stop', { method: 'POST' }); } catch {}
  if (child && weStartedIt) {
    child.kill();
    child = null;
  }
  return { ok: true };
}

// -- lifecycle ----------------------------------------------------------------

app.whenReady().then(() => {
  createWindow();

  // KBOT_SELFTEST=out.png renders the window to a file and exits - how the UI
  // gets verified without a human watching. Capture has to wait for a real
  // paint: firing on a timer alone yields a zero-byte image.
  if (process.env.KBOT_SELFTEST) {
    win.webContents.once('did-finish-load', () => {
      win.show();
      win.focus();
      // KBOT_AUTOSTART also exercises the spawn path, which is the one the
      // Start button uses and the one most likely to break (PATH, cwd).
      if (process.env.KBOT_AUTOSTART) {
        spawnBot().then((r) => process.stdout.write(`autostart: ${JSON.stringify(r)}
`));
      }
      setTimeout(async () => {
        try {
          const img = await win.webContents.capturePage();
          const png = img.toPNG();
          if (!png.length) throw new Error('capturePage returned an empty image');
          fs.writeFileSync(process.env.KBOT_SELFTEST, png);
          process.stdout.write(`selftest wrote ${process.env.KBOT_SELFTEST} (${png.length} bytes)\n`);
        } catch (err) {
          process.stderr.write(`selftest failed: ${err.message}\n`);
        }
        app.exit(0);
      }, 5000);
    });
  }

  app.on('activate', () => { if (!BrowserWindow.getAllWindows().length) createWindow(); });
});

app.on('window-all-closed', async () => {
  // Only tear down a bot this window started. One attached from the CLI keeps
  // running - closing a viewer should not close positions.
  if (child && weStartedIt) { child.kill(); child = null; }
  app.quit();
});

// -- IPC ----------------------------------------------------------------------

const offline = (reason) => ({ offline: true, reason, engineRunning: false, running: false });

ipcMain.handle('snapshot', async () => {
  try { return { ...(await api('/status')), offline: false }; }
  catch (err) { return offline(err.message); }
});
ipcMain.handle('equity', async () => { try { return await api('/equity'); } catch { return []; } });
ipcMain.handle('config', async () => { try { return await api('/config'); } catch { return null; } });

ipcMain.handle('start', () => spawnBot());
ipcMain.handle('stop', () => stopBot());
ipcMain.handle('kill', async (_e, reason) => {
  try { return await api('/kill', { method: 'POST', body: { reason } }); }
  catch (err) { return { ok: false, reason: err.message }; }
});
ipcMain.handle('revive', async () => {
  try { return await api('/revive', { method: 'POST' }); }
  catch (err) { return { ok: false, reason: err.message }; }
});

/**
 * Going live is the only control here that spends money, so it asks first - and
 * says what the research actually found rather than a generic "are you sure".
 */
ipcMain.handle('setMode', async (_e, mode) => {
  if (!['paper', 'live'].includes(mode)) return { ok: false, reason: 'bad mode' };
  if (mode === 'live') {
    let cfg = null;
    try { cfg = await api('/config'); } catch {}
    const bankroll = cfg ? cfg.bankroll : 19;
    const perTrade = cfg ? cfg.risk.maxStakePerTrade : 2;
    const maxLoss = cfg ? cfg.maxTotalLoss : 19;
    const { response } = await dialog.showMessageBox(win, {
      type: 'warning',
      buttons: ['Cancel', 'Go live'],
      defaultId: 0,
      cancelId: 0,
      title: 'Switch to live trading?',
      message: 'This places real orders by tapping the Kalshi app on your phone.',
      detail:
        `Bankroll $${bankroll}, max $${perTrade} per trade, stops at $${maxLoss} of losses.\n\n` +
        'What the backtest measured on 2,000 real 15-minute windows:\n' +
        '  - crossing the spread lost money at every horizon tested\n' +
        '  - the model scored WORSE than the market price on Brier\n' +
        '  - losses grew as the model disagreed more strongly\n\n' +
        'No edge has been demonstrated. Expect to lose this money.',
    });
    if (response !== 1) return { ok: false, reason: 'cancelled' };
  }
  try { return await api('/mode', { method: 'POST', body: { mode } }); }
  catch (err) { return { ok: false, reason: err.message }; }
});
