/* Compiler checks on SPEED; no payment request, LLM call, or DB operation. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const parser = require('@babel/parser');
const repo = process.argv[2];
if (!repo) throw new Error('Pass the existing repo path');
const root = path.resolve(__dirname,'..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(),'df-compiler-'));
const original = path.join(repo,'src','paymentservice','charge.js');
const before = fs.readFileSync(original);
try {
  const child = spawnSync(process.execPath,[path.join(root,'service','instrument.cjs'),original,temporary],{encoding:'utf8'});
  assert.equal(child.status,0,child.stderr);
  const generated = fs.readFileSync(path.join(temporary,'charge.instrumented.cjs'),'utf8');
  parser.parse(generated,{sourceType:'script'});
  const catalog = JSON.parse(fs.readFileSync(path.join(temporary,'service-catalog.json'),'utf8'));
  assert(catalog.nodes.some(n => n.definitions.includes('cardType')),'Destructured cardType definition missing');
  assert(catalog.nodes.some(n => n.definitions.includes('valid')),'Destructured valid definition missing');
  assert(catalog.nodes.some(n => n.kind === 'true' && n.uses.includes('valid')),'True p-use missing');
  assert(catalog.nodes.some(n => n.kind === 'false' && n.uses.includes('valid')),'False p-use missing');
  const locals = new Set(catalog.nodes.flatMap(n=>n.definitions));
  assert(!locals.has('logger'),'Imported logger must not enter local-binding denominator');
  assert.deepEqual(fs.readFileSync(original),before,'Original source was changed');
  const ids = new Set(catalog.nodes.map(n=>n.id));
  assert(catalog.edges.every(([a,b])=>ids.has(a)&&ids.has(b)),'CFG edge references missing node');
  const html=fs.readFileSync(path.join(root,'dashboard','index.html'),'utf8');
  parser.parse(html.split('<script>')[1].split('</script>')[0],{sourceType:'script'});
  console.log('JavaScript compiler/catalog/dashboard checks passed; no payment test executed.');
} finally {
  const resolved = fs.realpathSync(temporary);
  if (path.dirname(resolved) !== fs.realpathSync(os.tmpdir()) || !path.basename(resolved).startsWith('df-compiler-'))
    throw new Error('Unexpected compiler temporary directory; refusing cleanup');
  fs.rmSync(temporary,{recursive:true,force:true});
}
