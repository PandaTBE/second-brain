import test from 'node:test';
import assert from 'node:assert/strict';
import { buildGraph, filterRecords, normalizeKeyword, LatestRequest, mergePage, focusNodeId, recordChanged, fitGraph, createRecordFilter, graphSignature } from '../static/memory-atlas-model.mjs';

const records = [
  { id: 'a', title: 'SQLite решение', source_id: 'codex', card_status: 'ready', keywords: [' SQLite ', 'FTS5'] },
  { id: 'b', title: 'Поиск', source_id: 'claude', card_status: 'pending', keywords: ['ＳＱＬｉｔｅ', 'sqlite'] },
  { id: 'c', title: '<img src=x onerror=alert(1)>', source_id: 'codex', card_status: 'error', keywords: ['unique'] },
];
const sources = [{ id: 'codex', name: 'Codex', kind: 'codex' }, { id: 'claude', name: 'Claude', kind: 'claude' }];

test('Graph connects actual source membership and shared normalized keywords only', () => {
  const graph = buildGraph(records, sources);
  assert.equal(normalizeKeyword('  ＳＱＬｉｔｅ  '), 'sqlite');
  assert.equal(graph.nodes.filter(n => n.kind === 'record').length, 3);
  assert.equal(graph.nodes.filter(n => n.kind === 'source').length, 2);
  assert.equal(graph.nodes.filter(n => n.kind === 'keyword').length, 1);
  assert.equal(graph.links.filter(n => n.kind === 'source').length, 3);
  assert.equal(graph.links.filter(n => n.kind === 'keyword').length, 2);
  assert.equal(graph.nodes.find(n => n.id === 'record:c').label, records[2].title);
  assert.equal(new Set(graph.nodes.map(n => n.id)).size, graph.nodes.length);
});

test('Filtering intersects status, source, and text without changing input', () => {
  assert.deepEqual(filterRecords(records, { status: 'ready', source: 'codex', query: 'sqlite' }).map(n => n.id), ['a']);
  assert.deepEqual(filterRecords(records, { status: 'pending', source: 'codex' }), []);
  assert.equal(records.length, 3);
  assert.equal(buildGraph([], []).nodes.length, 0);
  assert.equal(buildGraph([records[0]], sources).nodes.some(n => n.kind === 'keyword'), false);
});

test('A search index normalizes each snapshot once and a refreshed snapshot sees changed text', () => {
  let reads = 0;
  const first = { ...records[0], get summary() { reads += 1; return 'Первое решение'; } };
  const search = createRecordFilter([first, records[1]]);
  const indexedReads = reads;
  assert.deepEqual(search({ query: 'первое' }).map(row => row.id), ['a']);
  assert.deepEqual(search({ query: 'решение', status: 'ready' }).map(row => row.id), ['a']);
  assert.deepEqual(search({ query: 'unmatched' }), []);
  assert.equal(reads, indexedReads);
  const updated = createRecordFilter([{ ...records[0], summary: 'Новое содержание' }]);
  assert.equal(updated({ query: 'новое' }).length, 1);
});

test('Graph refresh keys ignore poll timestamps but include labels, state, membership and keywords', () => {
  const key = graphSignature(records, sources);
  assert.equal(graphSignature(records.map(row => ({ ...row })), sources.map(row => ({ ...row }))), key);
  for (const change of [{ title: 'Changed' }, { card_status: 'pending' }, { keywords: ['new'] }, { source_id: 'new' }]) {
    assert.notEqual(graphSignature([{ ...records[0], ...change }, ...records.slice(1)], sources), key);
  }
  assert.notEqual(graphSignature(records, [{ ...sources[0], name: 'Changed' }, sources[1]]), key);
});

test('Only the newest asynchronous response is current, including late errors', () => {
  const gate = new LatestRequest();
  const old = gate.begin();
  const fresh = gate.begin();
  assert.equal(gate.isCurrent(old), false);
  assert.equal(gate.isCurrent(fresh), true);
  gate.invalidate();
  assert.equal(gate.isCurrent(fresh), false);
});

test('Keyboard entry falls back when a focused node is removed by refresh', () => {
  const nodes = [{ id: 'a' }, { id: 'b' }];
  assert.equal(focusNodeId(nodes, 'b'), 'b');
  assert.equal(focusNodeId(nodes, 'removed'), 'a');
  assert.equal(focusNodeId([], 'removed'), null);
});

test('Unchanged snapshots preserve an opened detail while new revisions or status refresh it', () => {
  const selected = { revision: 1, cardStatus: 'pending', updatedAt: '2026-11-04' };
  assert.equal(recordChanged({ revision: 1, card_status: 'pending', updated_at: '2026-11-04' }, selected), false);
  assert.equal(recordChanged({ revision: 2, card_status: 'pending', updated_at: '2026-11-04' }, selected), true);
  assert.equal(recordChanged({ revision: 1, card_status: 'ready', updated_at: '2026-11-04' }, selected), true);
  assert.equal(recordChanged(null, selected), false);
});

test('Initial and reset camera fit a large negative-coordinate cluster with label padding', () => {
  const large = Array.from({ length: 292 }, (_, i) => ({ id: String(i), title: 'Материал', source_id: i < 280 ? 'codex' : 'claude', keywords: [] }));
  const graph = buildGraph(large, sources);
  assert.ok(Math.min(...graph.nodes.map(node => node.y)) < 0);
  const camera = fitGraph(graph.nodes);
  for (const node of graph.nodes) {
    const x = node.x * camera.zoom + camera.x, y = node.y * camera.zoom + camera.y;
    assert.ok(x >= 35 && x <= 965, 'All nodes fit horizontally');
    assert.ok(y >= 35 && y <= 645, 'All nodes fit vertically');
  }
  assert.ok(graph.nodes.filter(node => node.kind === 'source').every(node => node.y * camera.zoom + camera.y - 40 * camera.zoom >= 35));
  assert.deepEqual(fitGraph([]), { x: 0, y: 0, zoom: 1 });
});

test('History append deduplicates records while retaining order and latest data', () => {
  assert.deepEqual(mergePage([records[0], records[1]], [{ ...records[1], title: 'updated' }, records[2]]).map(n => [n.id, n.title]), [
    ['a', records[0].title], ['b', 'updated'], ['c', records[2].title],
  ]);
});
