import test from 'node:test';
import assert from 'node:assert/strict';
import { MemoryGraph } from '../static/memory-atlas-graph.mjs';
import { buildGraph } from '../static/memory-atlas-model.mjs';

class Element {
  constructor(tag, ownerDocument) {
    this.tagName = tag; this.ownerDocument = ownerDocument;
    this.attributes = new Map(); this.children = []; this.dataset = {}; this.listeners = new Map();
    this.writes = [];
    this.classList = { toggle: (name, enabled) => {
      const values = new Set((this.attributes.get('class') || '').split(' ').filter(Boolean));
      if (enabled) values.add(name); else values.delete(name);
      this.setAttribute('class', [...values].join(' '));
    }, add: () => {}, remove: () => {} };
  }
  setAttribute(name, value) {
    this.attributes.set(name, String(value)); this.writes.push(name);
    if (name === 'data-node-id') this.dataset.nodeId = String(value);
  }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  append(...nodes) { for (const node of nodes) {
    if (node.tagName === 'fragment') { this.append(...[...node.children]); continue; }
    node.remove(); this.children.push(node); node.parentNode = this;
  } }
  insertBefore(node, anchor) {
    if (node === anchor) return;
    node.remove();
    const index = anchor ? this.children.indexOf(anchor) : this.children.length;
    this.children.splice(index, 0, node); node.parentNode = this;
  }
  replaceChildren(...nodes) { for (const child of [...this.children]) child.remove(); this.append(...nodes); }
  remove() {
    if (this.parentNode) this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1);
    this.parentNode = null;
  }
  contains(element) { return this === element || this.children.some(child => child.contains(element)); }
  focus() { this.ownerDocument.activeElement = this; }
}

function setup() {
  const document = { activeElement: null, created: 0 };
  const ids = new Map();
  document.createElementNS = (_namespace, name) => { document.created += 1; return new Element(name, document); };
  document.createDocumentFragment = () => new Element('fragment', document);
  document.querySelector = selector => ids.get(selector);
  for (const id of ['memory-graph', 'graph-camera', 'graph-links', 'graph-nodes', 'zoom-in', 'zoom-out', 'zoom-reset', 'zoom-value']) {
    ids.set('#' + id, new Element(id === 'memory-graph' ? 'svg' : 'g', document));
  }
  ids.get('#memory-graph').append(ids.get('#graph-camera'));
  ids.get('#graph-camera').append(ids.get('#graph-links'), ids.get('#graph-nodes'));
  const frames = new Map(); let frame = 0;
  globalThis.document = document;
  globalThis.requestAnimationFrame = callback => { frames.set(++frame, callback); return frame; };
  globalThis.cancelAnimationFrame = id => frames.delete(id);
  const selected = [];
  const graph = new MemoryGraph(ids.get('#memory-graph'), node => selected.push(node));
  return { graph, document, selected, ids, frames, flush: () => {
    const pending = [...frames.values()]; frames.clear(); for (const callback of pending) callback();
  } };
}

const source = [{ id: 'notes', name: 'Notes' }];
const record = (id, extra = {}) => ({ id, source_id: 'notes', title: 'Note ' + id,
  card_status: 'ready', keywords: ['shared'], ...extra });

test('Dense 2000-record graph keeps every relation with a constant number of edge DOM nodes', () => {
  const { graph, ids, flush } = setup();
  const records = Array.from({ length: 2000 }, (_, index) => record(String(index), {
    keywords: Array.from({ length: 20 }, (_value, keyword) => 'topic ' + keyword),
  }));
  const model = buildGraph(records, source);
  graph.render(model); flush();
  assert.equal(model.links.length, 42000);
  assert.ok(ids.get('#graph-links').children.length <= 3);
  assert.equal(graph.elements.size, 2021);
  const segments = ids.get('#graph-links').children.reduce((sum, path) =>
    sum + (path.getAttribute('d')?.match(/M/g) || []).length, 0);
  assert.equal(segments, 42000);
});

test('Refresh reuses node elements, updates their content, and click sees the latest node', () => {
  const { graph, document, selected, flush } = setup();
  graph.render(buildGraph([record('a'), record('b')], source)); flush();
  const original = graph.elements.get('record:a');
  const before = document.created;
  graph.render(buildGraph([record('a', { title: 'Changed title', card_status: 'error' }), record('b')], source)); flush();
  assert.equal(graph.elements.get('record:a'), original);
  assert.equal(document.created, before);
  assert.match(original.getAttribute('aria-label'), /Changed title/);
  assert.match(original.getAttribute('class'), /error/);
  original.listeners.get('click')({});
  assert.equal(selected.at(-1).label, 'Changed title');
});

test('Camera changes coalesce into one animation-frame transform with the latest camera', () => {
  const { graph, ids, frames, flush } = setup();
  graph.render(buildGraph([record('a')], source)); flush();
  const camera = ids.get('#graph-camera'); camera.writes = [];
  for (let index = 0; index < 20; index += 1) graph.zoom(1.01);
  assert.equal(camera.writes.length, 0);
  assert.equal(frames.size, 1);
  flush();
  assert.equal(camera.writes.filter(name => name === 'transform').length, 1);
  assert.match(camera.getAttribute('transform'), new RegExp(`scale\\(${graph.camera.zoom}\\)`));
});

test('Roving keyboard focus updates only the old and new entries and survives refresh/removal', () => {
  const { graph, document, flush } = setup();
  const rows = Array.from({ length: 2000 }, (_, index) => record(String(index)));
  graph.render(buildGraph(rows, source)); flush();
  for (const element of graph.elements.values()) element.writes = [];
  const first = graph.nodes.values().next().value;
  graph.onKey({ key: 'ArrowRight', preventDefault() {} }, first);
  assert.equal([...graph.elements.values()].reduce((sum, element) =>
    sum + element.writes.filter(name => name === 'tabindex').length, 0), 2);
  assert.equal(document.activeElement, graph.elements.get('record:0'));
  graph.render(buildGraph(rows, source)); flush();
  assert.equal(document.activeElement, graph.elements.get('record:0'));
  graph.render(buildGraph(rows.slice(1), source)); flush();
  assert.equal(document.activeElement, graph.elements.get('source:notes'));
  assert.equal([...graph.elements.values()].filter(element => element.getAttribute('tabindex') === '0').length, 1);
});

test('Selection highlights exact neighbours without rewriting unrelated nodes', () => {
  const { graph, ids, flush } = setup();
  graph.render(buildGraph([record('a'), record('b'), record('c', { keywords: [] })], source)); flush();
  const unrelated = graph.elements.get('record:c'); unrelated.writes = [];
  graph.select('record:a');
  assert.equal(graph.elements.get('record:a').getAttribute('aria-pressed'), 'true');
  assert.match(graph.elements.get('keyword:shared').getAttribute('class'), /is-neighbor/);
  assert.equal(unrelated.writes.length, 0);
  assert.equal(ids.get('#graph-links').children.at(-1).getAttribute('d').match(/M/g).length, 2);
  graph.select(null);
  assert.equal(graph.elements.get('record:a').getAttribute('aria-pressed'), 'false');
  assert.equal(ids.get('#graph-links').children.at(-1).getAttribute('d'), '');
});
