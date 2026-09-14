#!/usr/bin/env node
'use strict';

const path = require('node:path');
const ROOT = path.join(__dirname, '..');
process.env.KALSHI_BOT_CONFIG = path.join(ROOT, 'config.robust-rv.paper.json');
const { Store } = require('../src/store');
const { promotionAudit } = require('../src/robustRvSupervisor');

const store = new Store(path.join(ROOT, 'data', 'robust-rv-paper.db'));
const rows = store.all("SELECT * FROM positions WHERE mode='paper' AND settled_at IS NOT NULL ORDER BY settled_at");
const audit = promotionAudit(rows);
console.log(JSON.stringify(audit, null, 2));
console.log(audit.mechanicalGatePassed
  ? '\nMechanical paper gate passed. Live remains NOT approved until the manual drift audit passes.'
  : '\nPaper gate not passed. Keep collecting; do not scale or switch to live.');
store.close();

