'use strict';
// Phone executor - places orders by driving the Kalshi iOS app through the
// phone-harness HTTP API on 127.0.0.1:8760.
//
// The flow below is CALIBRATED against the real app (2026-08-24, Kalshi
// com.kalshi.mobile, iOS 26.6). Four things about that app broke the first,
// guessed version of this file and are worth stating plainly:
//
//  1. The search result row does NOT contain the ticker. It shows the strike:
//     "BTC 15 min - $77,347.53 target". Worse, once you type into the search
//     box the box's own VALUE becomes its accessibility label, so a naive
//     "find an element containing the ticker" matches the search field itself
//     and taps in a loop. Rows are therefore matched on the formatted strike,
//     with TextField/Keyboard types excluded.
//
//  2. The Up/Down buttons have EMPTY accessibility labels. They are only
//     locatable geometrically: two unlabelled ~173x44 Buttons sitting directly
//     above the two payout-multiplier StaticTexts ("1.29x", "3.66x"). Left is
//     Up/YES, right is Down/NO.
//
//  3. The ticket takes a DOLLAR amount on a custom keypad, not a contract
//     count typed into a text field.
//
//  4. Confirmation is a SWIPE - "Slide to buy Up" - not a tap. That is a
//     safety feature and this module treats it as one: nothing else in the
//     flow can accidentally execute.
//
// Execution takes 8-12 actions at 200-450ms each, so 6-10 seconds elapse
// between deciding and being filled. In a market that reprices several times a
// second the quote that justified the trade is usually gone. That is a property
// of trading from a phone, not a bug to be tuned away.

const fs = require('node:fs');
const path = require('node:path');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class PhoneExecutor {
  constructor(cfg, { log = () => {} } = {}) {
    this.mode = 'live';
    this.cfg = cfg.phone;
    this.full = cfg;
    this.log = log;
    this.base = this.cfg.baseUrl.replace(/\/$/, '');
    fs.mkdirSync(this.cfg.screenshotDir, { recursive: true });
  }

  // -- harness primitives ---------------------------------------------------

  async _req(pathname, { method = 'GET', body = null, raw = false, timeout = 20000 } = {}) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeout);
    try {
      const r = await fetch(this.base + pathname, {
        method,
        signal: ctl.signal,
        headers: body ? { 'content-type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!r.ok) throw new Error(`phone ${r.status} ${pathname}: ${(await r.text()).slice(0, 160)}`);
      return raw ? Buffer.from(await r.arrayBuffer()) : await r.json();
    } finally { clearTimeout(timer); }
  }

  status() { return this._req('/status'); }

  async tap(x, y) {
    await this._req('/tap', { method: 'POST', body: { x, y } });
    await sleep(this.cfg.actionDelayMs);
  }

  async swipe(fromX, fromY, toX, toY, duration = 0.45) {
    await this._req('/swipe', { method: 'POST', body: { fromX, fromY, toX, toY, duration } });
    await sleep(this.cfg.actionDelayMs);
  }

  async type(text) {
    await this._req('/type', { method: 'POST', body: { text } });
    await sleep(this.cfg.actionDelayMs);
  }

  async launch(bundleId) {
    // App control is namespaced under /app/*; a bare /launch 404s.
    await this._req('/app/launch', { method: 'POST', body: { bundleId } });
    await sleep(1800);
  }

  async terminate(bundleId) {
    try { await this._req('/app/terminate', { method: 'POST', body: { bundleId } }); } catch {}
    await sleep(600);
  }

  async screenshot(label) {
    const buf = await this._req('/screenshot', { raw: true, timeout: 20000 });
    const file = path.join(this.cfg.screenshotDir,
      `${new Date().toISOString().replace(/[:.]/g, '-')}-${label}.png`);
    fs.writeFileSync(file, buf);
    return file;
  }

  async ready() {
    try {
      const s = await this.status();
      const st = s.state || s;
      if (!st.ready) return { ok: false, reason: `harness not ready (stage ${st.stage})` };
      // `status` serves cached state after the cable is pulled, and the video
      // stream keeps working when UI control does not. Only a live control read
      // proves the agent is actually there.
      await this.elements();
      return { ok: true };
    } catch (err) {
      return { ok: false, reason: err.message };
    }
  }

  // -- accessibility tree ---------------------------------------------------

  _flatten(node, out = []) {
    if (!node || typeof node !== 'object') return out;
    const rect = node.rect || node.frame || null;
    const label = [node.label, node.name, node.value]
      .filter((v) => typeof v === 'string' && v.trim()).join(' | ');
    if (rect && Number.isFinite(rect.x) && rect.width > 0 && rect.height > 0) {
      out.push({
        type: node.type || node.elementType || '',
        label,
        enabled: node.enabled !== false,
        visible: node.visible !== false,
        x: rect.x, y: rect.y, w: rect.width, h: rect.height,
        cx: Math.round(rect.x + rect.width / 2),
        cy: Math.round(rect.y + rect.height / 2),
      });
    }
    for (const child of node.children || []) this._flatten(child, out);
    return out;
  }

  async elements() {
    const body = await this._req('/source', { timeout: 25000 });
    const root = body.value || body.tree || body.source || body;
    return this._flatten(root);
  }

  /** Spendable cash, read off the ticket: "Predictions - $16.77 available". */
  balanceFrom(els) {
    for (const e of els) {
      const m = /\$([\d,]+\.?\d*)\s*available/i.exec(e.label);
      if (m) return Number(m[1].replace(/,/g, ''));
    }
    return null;
  }

  // -- order placement ------------------------------------------------------

  /**
   * Place an order in the Kalshi app.
   *
   * `dryRun: true` walks the whole flow and stops immediately BEFORE the
   * slide-to-buy gesture, returning what it saw. Because confirmation is a
   * swipe and every other step is a tap, a dry run genuinely cannot execute.
   */
  async place({ ticker, side, contracts, limitPrice, strike, dryRun = false }) {
    const started = Date.now();
    const shots = [];
    const snap = async (tag) => { try { shots.push(await this.screenshot(tag)); } catch {} };

    try {
      const rd = await this.ready();
      if (!rd.ok) throw new Error(`phone not ready: ${rd.reason}`);
      if (!(strike > 0)) throw new Error('strike is required to identify the market row');

      // Terminate before launching. Relying on in-app navigation to get back to
      // a known screen does not work: a market detail page has no bottom tab
      // bar, so there is no Explore tab to return through, and whatever the
      // previous order left on screen leaks into this one. Two seconds for a
      // deterministic starting state is worth it.
      await this.terminate(this.cfg.bundleId);
      await this.launch(this.cfg.bundleId);
      await snap('01-launch');

      // ---- reset to a clean search -------------------------------------
      let els = await this.elements();
      const cancel = els.find((e) => e.visible && /cancel search/i.test(e.label));
      if (cancel) { await this.tap(cancel.cx, cancel.cy); els = await this.elements(); }
      const explore = els.find((e) => e.visible && /^Explore\b/i.test(e.label));
      if (explore) { await this.tap(explore.cx, explore.cy); await sleep(700); els = await this.elements(); }

      const search = els.find((e) => e.visible
        && (/search for markets/i.test(e.label) || /SearchField/i.test(e.type)));
      if (!search) throw new Error('could not find the search box');
      await this.tap(search.cx, search.cy);
      await this.type(ticker);
      await sleep(2000);
      await snap('02-search');

      // ---- pick the row by STRIKE, never by ticker ----------------------
      const strikeText = Number(strike).toLocaleString('en-US',
        { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      els = await this.elements();
      const row = els.find((e) => e.visible
        && !/TextField|SearchField|Keyboard|Key$/i.test(e.type)
        && e.label.includes(strikeText)
        && !/closed/i.test(e.label));
      if (!row) throw new Error(`no open market row showing strike ${strikeText}`);
      await this.tap(row.cx, row.cy);
      await sleep(1500);
      await snap('03-market');

      // ---- side: unlabelled buttons above the multipliers ---------------
      els = await this.elements();
      const mults = els.filter((e) => e.visible && /^[\d.]+x\b/.test(e.label))
        .sort((a, b) => a.cx - b.cx);
      if (mults.length < 2) throw new Error('could not find the Up/Down payout multipliers');
      const target = side === 'yes' ? mults[0] : mults[1];      // left = Up = YES
      const mult = parseFloat(target.label);
      const btn = els.find((e) => e.type === 'Button' && !e.label && e.visible && e.enabled
        && Math.abs(e.cx - target.cx) < 14 && e.cy < target.cy && target.cy - e.cy < 60);
      if (!btn) throw new Error(`no ${side === 'yes' ? 'Up' : 'Down'} button above its multiplier`);

      // The multiplier IS the price: 1.29x means ~77.5c. Refuse if the app
      // disagrees with the book we priced off - that gap is the phone's
      // latency showing up as a worse fill than we agreed to.
      const appPrice = 1 / mult;
      const slip = appPrice - limitPrice;
      const maxSlip = this.full.signal.maxPhoneSlippage ?? 0.03;
      if (!(mult > 1)) throw new Error(`nonsense multiplier ${mult}`);
      if (slip > maxSlip) {
        throw new Error(`app price ${appPrice.toFixed(3)} is ${slip.toFixed(3)} worse than the ` +
          `${limitPrice.toFixed(3)} we priced - refusing`);
      }
      this.log('phone', `${side} @ app ${appPrice.toFixed(3)} (${mult}x) vs intended ${limitPrice.toFixed(3)}`);
      await this.tap(btn.cx, btn.cy);
      await sleep(1600);
      await snap('04-ticket');

      // ---- amount, in dollars, on the ticket keypad ---------------------
      els = await this.elements();
      if (!els.some((e) => /slide to buy/i.test(e.label))) {
        throw new Error('order ticket did not open (no slide-to-buy control)');
      }
      const selectedSide = els.find((e) => e.visible && /^(UP|DOWN)$/i.test(e.label.split(' | ')[0]));
      const wantSide = side === 'yes' ? 'UP' : 'DOWN';
      const slideEl = els.find((e) => /slide to buy/i.test(e.label));
      if (slideEl && !new RegExp(`slide to buy ${side === 'yes' ? 'up' : 'down'}`, 'i').test(slideEl.label)) {
        throw new Error(`ticket is for the wrong side: "${slideEl.label}"`);
      }

      const available = this.balanceFrom(els);
      // Size off the APP's price, not the API quote we decided on: by the time
      // the ticket is open the book has moved, and the app buys $X worth.
      const dollars = Math.max(0.01, Math.round(contracts * appPrice * 100) / 100);
      if (available != null && dollars > available) {
        throw new Error(`order $${dollars.toFixed(2)} exceeds available $${available.toFixed(2)}`);
      }

      const typed = await this.enterAmount(els, dollars);
      await sleep(900);
      await snap('05-amount');

      // ---- verify the ticket before the only step that spends money -----
      els = await this.elements();
      const v = this.verifyTicket(els, { typed, side, strikeText, limitPrice });
      if (!v.ok) throw new Error(`ticket check failed: ${v.reason}`);
      this.log('phone', `ticket ok: $${typed} at ${v.ticketMult}x ` +
        `(${(1 / v.ticketMult).toFixed(3)}/contract, intended ${limitPrice.toFixed(3)})`);

      if (dryRun) {
        await snap('06-dryrun-stop');
        this.log('phone', 'DRY RUN - stopping before the slide-to-buy gesture');
        await this.closeTicket(els);
        return {
          status: 'aborted',
          error: 'dry run: stopped before confirming',
          appPrice, dollars, available,
          evidence: shots.join(';'),
          latencyMs: Date.now() - started,
        };
      }

      // ---- confirm: slide, do not tap -----------------------------------
      const slide = els.find((e) => /slide to buy/i.test(e.label));
      if (!slide) throw new Error('slide-to-buy control vanished before confirming');
      const y = slide.cy;
      const fromX = Math.round(slide.x + 18);
      const toX = Math.round(slide.x + slide.w - 12);
      this.log('phone', `sliding to buy: ${fromX} -> ${toX} @ y=${y}`);
      await this.swipe(fromX, y, toX, y, 0.5);
      await sleep(2500);
      await snap('07-confirmed');

      // Without API credentials this screen IS the fill record, so if we
      // cannot see a confirmation we must not book one.
      const after = await this.elements();
      const text = after.map((e) => e.label).join(' ~ ');
      const filled = /filled|order placed|confirmed|position|you (bought|own)/i.test(text);
      if (!filled) {
        return {
          status: 'pending',
          error: 'no fill confirmation on screen - reconcile in the app before restarting',
          evidence: shots.join(';'),
          latencyMs: Date.now() - started,
          screenText: text.slice(0, 2000),
        };
      }

      return {
        status: 'filled',
        fillPrice: appPrice,
        notional: dollars,
        evidence: shots.join(';'),
        latencyMs: Date.now() - started,
        screenText: text.slice(0, 2000),
      };
    } catch (err) {
      await snap('99-error');
      this.log('phone', `ORDER FAILED: ${err.message}`);
      try { await this.closeTicket(); } catch {}
      return {
        status: 'failed',
        error: err.message,
        evidence: shots.join(';'),
        latencyMs: Date.now() - started,
      };
    }
  }

  /** Type a dollar amount on the ticket's own keypad (not the iOS keyboard). */
  async enterAmount(els, dollars) {
    // The app echoes exactly what you type - "$0.4", not "$0.40" - so the
    // string typed here is what the verifier must look for on screen.
    const digits = dollars.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
    const keys = new Map();
    for (const e of els) {
      const k = e.label.split(' | ')[0].trim();
      if (e.type === 'Button' && /^[0-9.]$/.test(k) && e.visible) keys.set(k, e);
    }
    if (keys.size < 10) throw new Error(`ticket keypad not found (saw ${keys.size} keys)`);
    for (const ch of digits) {
      const key = keys.get(ch);
      if (!key) throw new Error(`no keypad key for "${ch}"`);
      await this.tap(key.cx, key.cy);
    }
    return digits;
  }

  /**
   * Cross-check the ticket against our intent. Deliberately conservative:
   * anything that cannot be positively confirmed fails the order. A missed
   * selector must stop the trade, never place a guess.
   */
  verifyTicket(els, { typed, side, strikeText, limitPrice }) {
    const text = els.map((e) => e.label).join(' ~ ');
    if (!text.includes(strikeText)) return { ok: false, reason: 'strike not visible on the ticket' };

    const slide = els.find((e) => /slide to buy/i.test(e.label));
    if (!slide) return { ok: false, reason: 'no slide-to-buy control' };
    const wants = side === 'yes' ? 'up' : 'down';
    if (!new RegExp(`slide to buy ${wants}`, 'i').test(slide.label)) {
      return { ok: false, reason: `slide control says "${slide.label}", expected ${wants}` };
    }

    // The app echoes the amount exactly as typed ("$0.4"), so match that
    // string rather than a re-formatted one.
    const esc = typed.replace('.', '\\.');
    if (!new RegExp(`\\$?\\s*${esc}(?![\\d])`).test(text)) {
      return { ok: false, reason: `amount ${typed} not visible on the ticket` };
    }

    // The ticket carries its OWN multiplier ("3.18x - $1.27 payout"), and it is
    // fresher than the one on the market page - measured moving 3.66x -> 3.18x
    // in the four seconds between the two screens. This is the price we are
    // actually about to pay, so the slippage gate is applied here, last.
    const m = /(\d+\.?\d*)x\b/.exec(text);
    if (!m) return { ok: false, reason: 'no payout multiplier on the ticket' };
    const ticketMult = Number(m[1]);
    if (!(ticketMult > 1)) return { ok: false, reason: `nonsense ticket multiplier ${ticketMult}` };
    const ticketPrice = 1 / ticketMult;
    const maxSlip = this.full.signal.maxPhoneSlippage ?? 0.03;
    if (ticketPrice - limitPrice > maxSlip) {
      return {
        ok: false,
        reason: `ticket price ${ticketPrice.toFixed(3)} is ` +
          `${(ticketPrice - limitPrice).toFixed(3)} worse than the ${limitPrice.toFixed(3)} we priced`,
      };
    }
    return { ok: true, ticketMult, ticketPrice };
  }

  async closeTicket(els = null) {
    try {
      const list = els || await this.elements();
      const close = list.find((e) => e.visible && /^Close$/i.test(e.label.split(' | ')[0]));
      if (close) await this.tap(close.cx, close.cy);
    } catch { /* best effort */ }
  }

  async close() {}
}

module.exports = { PhoneExecutor };
