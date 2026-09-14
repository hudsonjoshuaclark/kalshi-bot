'use strict';
// Local control API, same idea as the phone harness on 8760.
//
// The tracker window is an Electron app, and Electron 33 bundles Node 20, which
// has no `node:sqlite`. Rather than pin the UI to whatever Node version
// Electron ships, the bot runs in real Node and the window talks to it over
// HTTP. The useful side effect is that closing the window no longer kills the
// bot, and the CLI and the UI are the same client.
//
// Bound to 127.0.0.1 only. There is no auth because there is no remote access.

const http = require('node:http');

const DEFAULT_PORT = 8770;
const HOST = '127.0.0.1';

function createServer({ store, controls }) {
  return http.createServer(async (req, res) => {
    const url = new URL(req.url, `http://${HOST}`);
    const send = (code, body) => {
      const payload = JSON.stringify(body);
      res.writeHead(code, {
        'content-type': 'application/json',
        'content-length': Buffer.byteLength(payload),
        'access-control-allow-origin': '*',
        'access-control-allow-headers': 'content-type',
        'access-control-allow-methods': 'GET,POST,OPTIONS',
      });
      res.end(payload);
    };

    if (req.method === 'OPTIONS') return send(200, { ok: true });

    const body = async () => {
      const chunks = [];
      for await (const c of req) chunks.push(c);
      if (!chunks.length) return {};
      try { return JSON.parse(Buffer.concat(chunks).toString()); } catch { return {}; }
    };

    try {
      if (req.method === 'GET') {
        switch (url.pathname) {
          case '/status': return send(200, controls.snapshot());
          case '/equity': return send(200, store.equityCurve(controls.mode(), 1000));
          case '/signals': return send(200, store.recentSignals(60));
          case '/orders': return send(200, store.recentOrders(60));
          case '/events': return send(200, store.recentEvents(120));
          case '/config': return send(200, controls.config());
          case '/health': return send(200, { ok: true, running: !!controls.snapshot().engineRunning });
        }
      }
      if (req.method === 'POST') {
        const b = await body();
        switch (url.pathname) {
          case '/start': return send(200, await controls.start());
          case '/stop': return send(200, await controls.stop());
          case '/kill': return send(200, controls.kill(b.reason));
          case '/revive': return send(200, controls.revive());
          case '/mode': return send(200, await controls.setMode(b.mode));
        }
      }
      send(404, { error: 'not found' });
    } catch (err) {
      send(500, { error: err.message });
    }
  });
}

function listen(server, port = DEFAULT_PORT) {
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, HOST, () => resolve(port));
  });
}

module.exports = { createServer, listen, DEFAULT_PORT, HOST };
