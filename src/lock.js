'use strict';
// Single-engine guard.
//
// The tracker window can run the engine, and so can the CLI. If both do, they
// each see the other's positions in the shared database, both decide the same
// window is worth trading, and the bankroll gets spent twice. This is the same
// class of bug as two Claude sessions editing one repo - cheap to prevent,
// expensive to notice.
//
// A heartbeat in the state table rather than a lockfile: a lockfile left behind
// by a crash blocks every future run until someone deletes it, whereas a stale
// heartbeat simply ages out.

const HEARTBEAT_MS = 5000;
const STALE_MS = 20000;

class EngineLock {
  constructor(store, label) {
    this.store = store;
    this.label = label;
    this.timer = null;
  }

  /** Current holder, or null if none / stale. */
  holder() {
    const h = this.store.getState('engineHolder', null);
    if (!h) return null;
    if (Date.now() - h.ts > STALE_MS) return null;
    if (h.pid === process.pid) return null;      // ours
    return h;
  }

  acquire() {
    const other = this.holder();
    if (other) {
      return {
        ok: false,
        reason: `another engine is already running (${other.label}, pid ${other.pid}, ` +
          `last seen ${Math.round((Date.now() - other.ts) / 1000)}s ago)`,
      };
    }
    const beat = () => this.store.setState('engineHolder', {
      pid: process.pid, label: this.label, ts: Date.now(),
    });
    beat();
    this.timer = setInterval(beat, HEARTBEAT_MS);
    if (this.timer.unref) this.timer.unref();
    return { ok: true };
  }

  release() {
    clearInterval(this.timer);
    this.timer = null;
    const h = this.store.getState('engineHolder', null);
    if (h && h.pid === process.pid) this.store.setState('engineHolder', null);
  }
}

module.exports = { EngineLock };
