// DOM-free rendering regressions. Run: node --test tests/test_frontend.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../static/js/app.js'), 'utf8');
function app() {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, { innerHTML: '', style: {}, addEventListener() {}, classList: { toggle() {} } });
    return elements.get(id);
  };
  const context = vm.createContext({
    window: { addEventListener() {} },
    document: { addEventListener() {}, getElementById: get, querySelectorAll() { return []; } },
    localStorage: { getItem() { return null; } },
    requestAnimationFrame() {},
    setTimeout() { throw new Error('Unexpected delayed dismissal'); },
    t: (key, ...args) => [key, ...args].join(' '),
    event: { stopPropagation() {} },
  });
  vm.runInContext(source, context);
  return { context, get, run: code => vm.runInContext(code, context) };
}

function decodedHandlers(html) {
  const entities = { amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'" };
  return [...html.matchAll(/on(?:click|dblclick)="([^"]*)"/g)].map(match =>
    match[1].replace(/&(amp|lt|gt|quot|#39);/g, (_, key) => entities[key]));
}

const hostile = `O'Brien\\");globalThis.injected=1;("<img src=x onerror=alert(1)>&quot;`;
const header = '<img src=x onerror=alert(1)>';
const sheet = { headers: [header], rows: [{ _row: 1, cells: { A: header } },
  { _row: 2, cells: { A: 'value' } }], row_count: 2 };
const sheetDiff = { old_headers: [], new_headers: [header], status: 'modified',
  added_rows: [{ _row: 2, cells: { A: 'value' } }], removed_rows: [], modified_cells: [] };

test('diff and browse headers render as text', () => {
  const a = app();
  a.context.input = sheetDiff;
  const diff = a.run('renderDiffTable(input)');
  assert.ok(diff.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert.ok(!diff.includes('<img src=x'));
  a.context.parsed = { sheets: { Items: sheet } };
  a.run("state.diff={browse:true,parsed};state.activeSheet='Items';renderBrowseView(document.getElementById('content'));");
  assert.ok(a.get('content').innerHTML.includes('&lt;img'));
  assert.ok(!a.get('content').innerHTML.includes('<img src=x'));
});

test('filenames cannot escape inline event handlers', () => {
  const a = app();
  a.context.filename = hostile + '.xml';
  a.run("state.files=[{name:filename,size:1}];renderFileList();selectFile=(name)=>{globalThis.chosen=name;};");
  const html = a.get('fileList').innerHTML;
  assert.ok(!html.includes('<img src=x'));
  for (const handler of decodedHandlers(html)) a.run(handler);
  assert.equal(a.context.chosen, hostile + '.xml');
  assert.equal(a.context.injected, undefined);
});

test('sheet names remain inert in all tab renderers', () => {
  const a = app();
  a.context.sheetName = hostile;
  a.context.diff = { sheets: { [hostile]: sheetDiff, Other: sheetDiff }, summary: { has_changes: false } };
  a.context.parsed = { sheets: { [hostile]: sheet } };
  a.context.merge = { sheets: { [hostile]: { rows: [], headers: [], conflict_count: 0 } }, summary: {} };
  a.run('setActiveSheet=(name)=>{globalThis.chosen=name;};setOverviewSheet=(file,name)=>{globalThis.chosen=name;};');
  const renderers = [
    "state.diff=diff;state.activeSheet=sheetName;renderDiffView(document.getElementById('content'));",
    "state.diff={parsed};renderBrowseView(document.getElementById('content'));",
    "state.mergeData=merge;renderMergeView(document.getElementById('content'));",
    "document.getElementById('content').innerHTML=renderOverviewFileDetail({file:'items.xml',diff});",
  ];
  for (const renderer of renderers) {
    a.run(renderer);
    const html = a.get('content').innerHTML;
    assert.ok(!html.includes('<img src=x'));
    a.run(decodedHandlers(html).find(code => code.includes('setActiveSheet(') || code.includes('setOverviewSheet(')));
    assert.equal(a.context.chosen, hostile);
    assert.equal(a.context.injected, undefined);
  }
});

test('merge choices preserve arbitrary sheet names and row keys', () => {
  const a = app();
  a.context.sheetName = hostile;
  a.context.row = { row_key: hostile, status: 'added_both_diff', row_decision: null };
  a.context.cell = { header, base: 'old', mine: 'mine', theirs: 'theirs', status: 'conflict', resolved: null };
  a.run('setRowChoice=(sheet,key)=>{globalThis.chosen=[sheet,key];};setCellChoice=setRowChoice;');
  for (const render of ['renderRowDecisionButtonsInner(sheetName,row)', "renderMergeCell(sheetName,row,'B',cell)"]) {
    const html = a.run(render);
    assert.ok(!html.includes('<img src=x'));
    for (const handler of decodedHandlers(html)) {
      a.run(handler);
      assert.equal(a.context.chosen[0], hostile);
      assert.equal(a.context.chosen[1], hostile);
      assert.equal(a.context.injected, undefined);
    }
  }
});

test('deletion and shifted modification sharing a row number are both visible', () => {
  const a = app();
  a.context.input = { old_headers: ['ID', 'Value'], new_headers: ['ID', 'Value'],
    removed_rows: [{ _row: 2, cells: { A: '1', B: 'removed' } }], added_rows: [],
    modified_rows: [{ _row: 2, cells: { A: '2', B: 'new' }, old_cells: { A: '2', B: 'old' },
      changes: { B: { old: 'old', new: 'new' } } }],
  };
  const html = a.run('renderDiffTable(input)');
  assert.ok(html.includes('row-removed'));
  assert.ok(html.includes('cell-modified'));
  const body = html.match(/<tbody[^>]*>([\s\S]*?)<\/tbody>/)[1];
  assert.equal((body.match(/<tr[ >]/g) || []).length, 2);
});

test('deleted trailing columns remain visible', () => {
  const a = app();
  a.context.input = { old_headers: ['ID', 'Name'], new_headers: ['ID'],
    removed_rows: [{ _row: 2, cells: { A: '1', B: 'removed-name' } }] };
  const html = a.run('renderDiffTable(input)');
  assert.ok(html.includes('<th title="B">Name</th>'));
  assert.ok(html.includes('removed-name'));
});

test('merge error messages are escaped', () => {
  const a = app();
  a.context.message = header;
  a.run("state.mode='merge';state.selectedFile='items.xml';state.mergeData={error:message};renderContent();");
  assert.ok(a.get('content').innerHTML.includes('&lt;img'));
  assert.ok(!a.get('content').innerHTML.includes('<img src=x'));
});

test('SVN errors stay visible instead of becoming a success notification', async () => {
  const a = app();
  a.context.result = { errors: [header], backups: ['recovery.bak'], updated: 0 };
  a.run('api=async()=>result;reloadAfterUpdate=async()=>{};renderToolbar=()=>{};');
  await a.run('_runUpdateAndReport({})');
  const html = a.get('updateBanner').innerHTML;
  assert.ok(html.includes('update.failed'));
  assert.ok(html.includes('recovery.bak'));
  assert.ok(!html.includes('update.doneDetail'));
  assert.ok(!html.includes('<img src=x'));
});

test('a refreshed merge preview can be retried after a stale apply', async () => {
  const a = app();
  a.context.alert = () => {};
  a.run(`
    state.mode='merge';state.selectedFile='items.xml';
    state.mergeData={sheets:{Items:{rows:[]}},summary:{conflicts:0,auto_resolved:0}};
    api=async()=>{const error=new Error('stale');error.status=409;error.body={stale:true};throw error;};
    doMergePreview=async()=>{renderToolbar();};
  `);
  await a.run('applyMerge()');
  const button = a.get('toolbar').innerHTML.match(/<button[^>]*id="applyMergeBtn"[^>]*>/)[0];
  assert.ok(!button.includes('disabled'), button);
});
