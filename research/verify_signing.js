'use strict';
// Validate the request-signing format WITHOUT a real Kalshi key.
//
// A throwaway RSA keypair cannot authenticate, but the SHAPE of the rejection
// tells us whether Kalshi parsed our headers. Compare:
//
//   no headers at all      -> "token_authentication_failure"
//   well-formed but bogus  -> a DIFFERENT error (key/signature specific)
//
// If a signed request produces a different error code than an unsigned one, the
// header names, timestamp units, signed-message layout and base64 encoding are
// all being accepted - which is everything that can be checked before the real
// key exists.

const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { KalshiApi, PREFIX, HOSTS } = require('../src/kalshiApi');

(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kalshi-verify-'));
  const keyPath = path.join(dir, 'test.pem');
  const { privateKey } = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 });
  fs.writeFileSync(keyPath, privateKey.export({ type: 'pkcs8', format: 'pem' }));

  const api = new KalshiApi({ env: 'demo', keyId: 'test-key-id', privateKeyPath: keyPath });

  // 1. the signature itself must verify against its own public key
  const ts = Date.now().toString();
  const sig = api.sign('GET', PREFIX + '/portfolio/balance', ts);
  const ok = crypto.verify(
    'sha256',
    Buffer.from(`${ts}GET${PREFIX}/portfolio/balance`, 'utf8'),
    { key: crypto.createPublicKey(privateKey),
      padding: crypto.constants.RSA_PKCS1_PSS_PADDING, saltLength: 32 },
    Buffer.from(sig, 'base64'));
  console.log(`1. self-verify RSA-PSS(sha256, salt=32): ${ok ? 'PASS' : 'FAIL'}`);
  console.log(`   signed message: "${ts}GET${PREFIX}/portfolio/balance"`);
  console.log(`   signature: ${sig.slice(0, 44)}... (${Buffer.from(sig, 'base64').length} bytes)`);

  // 2. unsigned request, for a baseline error
  const bare = await fetch(`${HOSTS.demo}${PREFIX}/portfolio/balance`,
    { headers: { accept: 'application/json' } });
  const bareBody = await bare.text();
  console.log(`\n2. UNSIGNED  -> ${bare.status} ${bareBody.slice(0, 120)}`);

  // 3. signed with a key Kalshi has never seen
  const res = await api.verify();
  console.log(`3. SIGNED    -> ${res.reason || JSON.stringify(res)}`);

  const bareCode = (() => { try { return JSON.parse(bareBody).error.code; } catch { return String(bare.status); } })();
  const signedCode = res.code || 'none';
  console.log(`\n   unsigned error code: ${bareCode}`);
  console.log(`   signed   error code: ${signedCode}`);
  console.log(signedCode !== bareCode
    ? '   -> headers ACCEPTED and parsed; only the key is unknown. Format is correct.'
    : '   -> same rejection as unsigned: headers were NOT parsed. Format needs work.');

  // 4. order body shape, without sending it
  console.log('\n4. order body that WOULD be sent (buy NO at 0.30 = offer YES at 0.70):');
  const spy = Object.create(api);
  spy.request = (m, e, o) => { console.log(`   ${m} ${e}\n   ${JSON.stringify(o.body)}`); return {}; };
  spy.createOrder({ ticker: 'KXBTC15M-TEST', side: 'no', contracts: 3, price: 0.30 });
  spy.createOrder({ ticker: 'KXBTC15M-TEST', side: 'yes', contracts: 3, price: 0.62, postOnly: true });

  fs.rmSync(dir, { recursive: true, force: true });
})().catch((e) => { console.error('failed:', e.message); process.exit(1); });
