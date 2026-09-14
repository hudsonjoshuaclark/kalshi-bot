'use strict';
// The operations the CLI, the HTTP API and the tracker all share. Keeping them
// in one place is what stops "stop the engine, change mode, restart" from
// existing in three slightly different versions.

const fs = require('node:fs');
const path = require('node:path');

const { Engine } = require('./engine');
const { Risk } = require('./risk');
const { PaperExecutor } = require('./executor/paper');
const { PhoneExecutor } = require('./executor/phone');
const { ApiExecutor } = require('./executor/api');

const ROOT = path.join(__dirname, '..');
const CONFIG = process.env.KALSHI_BOT_CONFIG
  ? path.resolve(process.env.KALSHI_BOT_CONFIG)
  : path.join(ROOT, 'config.json');

function loadConfig() {
  const cfg = JSON.parse(fs.readFileSync(CONFIG, 'utf8'));
  cfg.phone.screenshotDir = path.isAbsolute(cfg.phone.screenshotDir)
    ? cfg.phone.screenshotDir : path.join(ROOT, cfg.phone.screenshotDir);
  return cfg;
}

function saveConfig(cfg) {
  const out = { ...cfg, phone: { ...cfg.phone, screenshotDir: 'data/shots' } };
  fs.writeFileSync(CONFIG, JSON.stringify(out, null, 2) + '\n');
}

class Controls {
  constructor(store, { label = 'cli', onLog = () => {}, onSignal = () => {} } = {}) {
    this.store = store;
    this.label = label;
    this.engine = null;
    this.logBuffer = [];
    this.onLog = onLog;
    this.onSignal = onSignal;
  }

  config() { return loadConfig(); }
  mode() { return loadConfig().mode; }

  async start() {
    if (this.engine && this.engine.running) return { ok: true, already: true };
    const cfg = loadConfig();
    const pending = [];
    const engineLog = (src, msg) => { if (this.engine) this.engine.log(src, msg); else pending.push([src, msg]); };
    // paper -> simulated | api / api-live -> laptop via Kalshi API | live -> phone
    const executor =
      cfg.mode === 'live' ? new PhoneExecutor(cfg, { log: () => {} })
      : (cfg.mode === 'api' || cfg.mode === 'api-live')
        ? new ApiExecutor(cfg, { log: (s, m) => engineLog(s, m) })
        : new PaperExecutor({ log: () => {} });
    const engine = new Engine({ cfg, store: this.store, executor });
    engine.on('log', (e) => {
      this.logBuffer.push(e);
      if (this.logBuffer.length > 500) this.logBuffer.shift();
      this.onLog(e);
    });
    engine.on('signal', (s) => this.onSignal(s));
    this.engine = engine;
    for (const [src, msg] of pending) engine.log(src, msg);
    try {
      await engine.start({ label: this.label });
      if (!engine.running) { this.engine = null; return { ok: false, reason: 'engine refused to start' }; }
      return { ok: true };
    } catch (err) {
      this.engine = null;
      return { ok: false, reason: err.message };
    }
  }

  async stop() {
    if (this.engine) { await this.engine.stop(); this.engine = null; }
    return { ok: true };
  }

  kill(reason) {
    this.store.setState('killSwitch', true);
    this.store.setState('killReason', reason || 'manual');
    this.store.logEvent('warn', 'controls', `kill switch engaged: ${reason || 'manual'}`);
    return { ok: true };
  }

  revive() {
    this.store.setState('killSwitch', false);
    this.store.setState('killReason', null);
    return { ok: true };
  }

  async setMode(mode) {
    if (!['paper', 'live', 'api', 'api-live'].includes(mode)) return { ok: false, reason: 'bad mode' };
    const cfg = loadConfig();
    const wasRunning = !!(this.engine && this.engine.running);
    await this.stop();
    cfg.mode = mode;
    saveConfig(cfg);
    if (wasRunning) await this.start();
    return { ok: true, mode };
  }

  snapshot() {
    const cfg = loadConfig();
    const logs = this.logBuffer.slice(-150);
    if (this.engine && this.engine.running) {
      return { ...this.engine.snapshot(), engineRunning: true, logs };
    }
    // Engine stopped: the database still knows everything that happened.
    const risk = new Risk(cfg, this.store, () => {});
    const holder = this.store.getState('engineHolder', null);
    return {
      mode: cfg.mode,
      running: false,
      engineRunning: false,
      otherHolder: holder && holder.pid !== process.pid && Date.now() - holder.ts < 20000
        ? holder : null,
      killed: !!this.store.getState('killSwitch', false),
      killReason: this.store.getState('killReason', null),
      bankroll: cfg.bankroll,
      equity: cfg.bankroll + risk.lifetimeRealised(),
      realised: risk.lifetimeRealised(),
      today: risk.todayRealised(),
      exposure: risk.openExposure(),
      summary: this.store.summary(cfg.mode),
      forecastScore: this.store.forecastScore(),
      quotes: {},
      markets: {},
      logs,
    };
  }
}

module.exports = { Controls, loadConfig, saveConfig, CONFIG, ROOT };
