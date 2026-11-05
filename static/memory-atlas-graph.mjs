import { focusNodeId, fitGraph } from './memory-atlas-model.mjs';

const svgNS = 'http://www.w3.org/2000/svg';

function svgElement(name, attributes = {}, text = '') {
  const element = document.createElementNS(svgNS, name);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, String(value));
  if (text) element.textContent = text;
  return element;
}

export class MemoryGraph {
  constructor(svg, onSelect) {
    this.svg = svg; this.onSelect = onSelect;
    this.camera = { x: 0, y: 0, zoom: 1 };
    this.fitted = false; this.nodes = new Map(); this.elements = new Map();
    this.parts = new Map(); this.links = []; this.adjacency = new Map();
    this.incident = new Map(); this.highlighted = new Set(); this.neighbors = new Set();
    this.selected = null; this.entryId = null; this.drag = null; this.frame = null;
    this.nodeLayer = document.querySelector('#graph-nodes');
    this.cameraLayer = document.querySelector('#graph-camera');
    this.zoomValue = document.querySelector('#zoom-value');
    this.paths = new Map(['source', 'keyword', 'active'].map(kind => {
      const path = svgElement('path', { class: kind === 'active' ? 'graph-link is-neighbor' : `graph-link ${kind}`,
        fill: 'none', 'aria-hidden': 'true', d: '' });
      return [kind, path];
    }));
    document.querySelector('#graph-links').replaceChildren(...this.paths.values());
    svg.addEventListener('wheel', event => {
      event.preventDefault(); this.zoom(Math.exp(-event.deltaY * 0.0014), this.point(event));
    }, { passive: false });
    svg.addEventListener('pointerdown', event => this.startPan(event));
    svg.addEventListener('pointermove', event => this.movePan(event));
    svg.addEventListener('pointerup', event => this.endPan(event));
    svg.addEventListener('pointercancel', event => this.endPan(event));
    document.querySelector('#zoom-in').addEventListener('click', () => this.zoom(1.25));
    document.querySelector('#zoom-out').addEventListener('click', () => this.zoom(0.8));
    document.querySelector('#zoom-reset').addEventListener('click', () => {
      this.camera = fitGraph([...this.nodes.values()]);
      this.fitted = this.nodes.size > 0; this.updateCamera();
    });
  }

  attribute(element, name, value) {
    if (element.getAttribute(name) !== String(value)) element.setAttribute(name, value);
  }

  point(event) {
    const matrix = this.svg.getScreenCTM();
    if (!matrix) return { x: 500, y: 340 };
    const point = this.svg.createSVGPoint();
    point.x = event.clientX; point.y = event.clientY;
    return point.matrixTransform(matrix.inverse());
  }

  zoom(factor, anchor = { x: 500, y: 340 }) {
    const zoom = Math.max(0.25, Math.min(5, this.camera.zoom * factor));
    const ratio = zoom / this.camera.zoom;
    this.camera = { x: anchor.x - (anchor.x - this.camera.x) * ratio,
      y: anchor.y - (anchor.y - this.camera.y) * ratio, zoom };
    this.updateCamera();
  }

  updateCamera() {
    if (this.frame !== null) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = null;
      this.attribute(this.cameraLayer, 'transform', `translate(${this.camera.x} ${this.camera.y}) scale(${this.camera.zoom})`);
      const label = `${Math.round(this.camera.zoom * 100)}%`;
      if (this.zoomValue.textContent !== label) this.zoomValue.textContent = label;
    });
  }

  startPan(event) {
    if (event.button !== 0 || event.target.closest('.graph-node')) return;
    this.drag = { pointer: event.pointerId, point: this.point(event), camera: { ...this.camera } };
    this.svg.setPointerCapture(event.pointerId); this.svg.classList.add('dragging');
  }

  movePan(event) {
    if (!this.drag || this.drag.pointer !== event.pointerId) return;
    const point = this.point(event);
    this.camera = { ...this.camera, x: this.drag.camera.x + point.x - this.drag.point.x,
      y: this.drag.camera.y + point.y - this.drag.point.y };
    this.updateCamera();
  }

  endPan(event) {
    if (this.svg.hasPointerCapture(event.pointerId)) this.svg.releasePointerCapture(event.pointerId);
    this.drag = null; this.svg.classList.remove('dragging');
  }

  buildEdges(links) {
    const groups = { source: [], keyword: [] };
    // Round each node once; subpixel endpoint differences do not need long path strings.
    const positions = new Map([...this.nodes].map(([id, node]) =>
      [id, `${Number(node.x.toFixed(2))} ${Number(node.y.toFixed(2))}`]));
    this.links = []; this.adjacency = new Map(); this.incident = new Map();
    for (const link of links) {
      if (!positions.has(link.source) || !positions.has(link.target)) continue;
      const segment = `M${positions.get(link.source)}L${positions.get(link.target)}`;
      const edge = { ...link, segment };
      this.links.push(edge);
      groups[link.kind === 'source' ? 'source' : 'keyword'].push(segment);
      for (const [id, neighbor] of [[link.source, link.target], [link.target, link.source]]) {
        if (!this.adjacency.has(id)) { this.adjacency.set(id, new Set()); this.incident.set(id, []); }
        this.adjacency.get(id).add(neighbor); this.incident.get(id).push(edge);
      }
    }
    for (const kind of ['source', 'keyword']) this.attribute(this.paths.get(kind), 'd', groups[kind].join(''));
  }

  createNode(id) {
    const element = svgElement('g', { role: 'button', 'data-node-id': id });
    const parts = { circle: svgElement('circle'), title: svgElement('title'), label: null, count: null };
    element.append(parts.circle, parts.title); this.parts.set(id, parts);
    element.addEventListener('click', () => {
      const node = this.nodes.get(id);
      if (node) { this.setEntry(id); element.focus({ preventScroll: true }); this.onSelect(node); }
    });
    element.addEventListener('keydown', event => this.onKey(event, this.nodes.get(id)));
    return element;
  }

  selectionAttributes(id, element) {
    const node = this.nodes.get(id);
    const classes = ['graph-node', node.kind, node.status || '',
      id === this.selected ? 'is-selected' : '', id !== this.selected && this.neighbors.has(id) ? 'is-neighbor' : ''];
    this.attribute(element, 'class', classes.filter(Boolean).join(' '));
    this.attribute(element, 'aria-pressed', String(id === this.selected));
  }

  updateNode(node, element) {
    const parts = this.parts.get(node.id);
    const label = node.kind === 'record' ? `Материал: ${node.label}` :
      `${node.kind === 'source' ? 'Источник' : 'Общая тема'}: ${node.label}, ${node.members.length} материалов`;
    this.attribute(element, 'transform', `translate(${node.x} ${node.y})`);
    this.attribute(element, 'tabindex', node.id === this.entryId ? 0 : -1);
    this.attribute(element, 'aria-label', label);
    this.attribute(parts.circle, 'r', node.kind === 'source' ? 18 : node.kind === 'keyword' ? 7 : 5);
    if (parts.title.textContent !== label) parts.title.textContent = label;
    if (node.kind === 'source' || node.showLabel) {
      if (!parts.label) { parts.label = svgElement('text', { 'text-anchor': 'middle' }); element.append(parts.label); }
      this.attribute(parts.label, 'y', node.kind === 'source' ? -26 : -13);
      const text = node.label.length > 30 ? `${node.label.slice(0, 30)}…` : node.label;
      if (parts.label.textContent !== text) parts.label.textContent = text;
    } else if (parts.label) { parts.label.remove(); parts.label = null; }
    if (node.kind === 'source') {
      if (!parts.count) { parts.count = svgElement('text', { y: 4, 'text-anchor': 'middle' }); element.append(parts.count); }
      if (parts.count.textContent !== String(node.members.length)) parts.count.textContent = String(node.members.length);
    }
    this.selectionAttributes(node.id, element);
  }

  render(graph) {
    if (this.currentGraph === graph) return;
    const focused = this.svg.contains(document.activeElement) ? document.activeElement.dataset.nodeId : null;
    this.entryId = focusNodeId(graph.nodes, focused || this.entryId);
    if (!this.fitted && graph.nodes.length) { this.camera = fitGraph(graph.nodes); this.fitted = true; }
    this.nodes = new Map(graph.nodes.map(node => [node.id, node]));
    this.order = graph.nodes.map(node => node.id);
    this.indices = new Map(this.order.map((id, index) => [id, index]));
    for (const [id, element] of this.elements) {
      if (!this.nodes.has(id)) { element.remove(); this.elements.delete(id); this.parts.delete(id); }
    }
    graph.nodes.forEach((node, index) => {
      let element = this.elements.get(node.id);
      if (!element) { element = this.createNode(node.id); this.elements.set(node.id, element); }
      this.updateNode(node, element);
      if (this.nodeLayer.children[index] !== element) this.nodeLayer.insertBefore(element, this.nodeLayer.children[index] || null);
    });
    this.buildEdges(graph.links); this.select(this.selected, true);
    if (focused && this.entryId) this.elements.get(this.entryId)?.focus({ preventScroll: true });
    this.currentGraph = graph; this.updateCamera();
  }

  setEntry(id) {
    if (id === this.entryId || !this.elements.has(id)) return;
    if (this.entryId && this.elements.has(this.entryId)) this.attribute(this.elements.get(this.entryId), 'tabindex', -1);
    this.entryId = id; this.attribute(this.elements.get(id), 'tabindex', 0);
  }

  onKey(event, node) {
    if (!node) return;
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); this.onSelect(node); return; }
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
    event.preventDefault();
    const direction = ['ArrowLeft', 'ArrowUp'].includes(event.key) ? -1 : 1;
    const next = this.order[(this.indices.get(node.id) + direction + this.order.length) % this.order.length];
    this.setEntry(next); this.elements.get(next)?.focus({ preventScroll: true });
  }

  select(id, force = false) {
    if (id === this.selected && !force) return;
    const nextNeighbors = this.adjacency.get(id) || new Set();
    const nextHighlighted = new Set(this.nodes.has(id) ? [id, ...nextNeighbors] : []);
    const changed = new Set([...this.highlighted, ...nextHighlighted]);
    this.selected = id; this.neighbors = nextNeighbors; this.highlighted = nextHighlighted;
    for (const nodeId of changed) {
      const element = this.elements.get(nodeId);
      if (element) this.selectionAttributes(nodeId, element);
    }
    const segments = (this.incident.get(id) || []).map(link => link.segment);
    this.attribute(this.paths.get('active'), 'd', segments.join(''));
  }
}
