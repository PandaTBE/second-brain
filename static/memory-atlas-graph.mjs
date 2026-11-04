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
    this.svg = svg;
    this.onSelect = onSelect;
    this.camera = { x: 0, y: 0, zoom: 1 };
    this.fitted = false;
    this.nodes = new Map();
    this.elements = new Map();
    this.links = [];
    this.selected = null;
    this.drag = null;
    svg.addEventListener('wheel', event => {
      event.preventDefault();
      this.zoom(Math.exp(-event.deltaY * 0.0014), this.point(event));
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

  point(event) {
    const point = this.svg.createSVGPoint();
    point.x = event.clientX; point.y = event.clientY;
    return point.matrixTransform(this.svg.getScreenCTM().inverse());
  }

  zoom(factor, anchor = { x: 500, y: 340 }) {
    const zoom = Math.max(0.25, Math.min(5, this.camera.zoom * factor));
    const ratio = zoom / this.camera.zoom;
    this.camera = { x: anchor.x - (anchor.x - this.camera.x) * ratio,
      y: anchor.y - (anchor.y - this.camera.y) * ratio, zoom };
    this.updateCamera();
  }

  updateCamera() {
    document.querySelector('#graph-camera').setAttribute('transform', `translate(${this.camera.x} ${this.camera.y}) scale(${this.camera.zoom})`);
    document.querySelector('#zoom-value').textContent = `${Math.round(this.camera.zoom * 100)}%`;
  }

  startPan(event) {
    if (event.button !== 0 || event.target.closest('.graph-node')) return;
    this.drag = { pointer: event.pointerId, point: this.point(event), camera: { ...this.camera } };
    this.svg.setPointerCapture(event.pointerId);
    this.svg.classList.add('dragging');
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

  render(graph) {
    const focused = this.svg.contains(document.activeElement) ? document.activeElement.dataset.nodeId : null;
    const entryId = focusNodeId(graph.nodes, focused);
    if (!this.fitted && graph.nodes.length) { this.camera = fitGraph(graph.nodes); this.fitted = true; }
    this.nodes = new Map(graph.nodes.map(node => [node.id, node]));
    this.elements = new Map(); this.links = [];
    const edgeFragment = document.createDocumentFragment();
    for (const link of graph.links) {
      const source = this.nodes.get(link.source), target = this.nodes.get(link.target);
      if (!source || !target) continue;
      const element = svgElement('line', { x1: source.x, y1: source.y, x2: target.x, y2: target.y, class: `graph-link ${link.kind}` });
      edgeFragment.append(element); this.links.push({ ...link, element });
    }
    document.querySelector('#graph-links').replaceChildren(edgeFragment);
    const nodeFragment = document.createDocumentFragment();
    graph.nodes.forEach(node => {
      const label = node.kind === 'record' ? `Материал: ${node.label}` : `${node.kind === 'source' ? 'Источник' : 'Общая тема'}: ${node.label}, ${node.members.length} материалов`;
      const element = svgElement('g', { transform: `translate(${node.x} ${node.y})`, class: `graph-node ${node.kind} ${node.status || ''}`,
        tabindex: node.id === entryId ? 0 : -1, role: 'button', 'aria-label': label, 'data-node-id': node.id });
      element.append(svgElement('circle', { r: node.kind === 'source' ? 18 : node.kind === 'keyword' ? 7 : 5 }));
      element.append(svgElement('title', {}, label));
      if (node.kind === 'source' || node.showLabel) element.append(svgElement('text', { y: node.kind === 'source' ? -26 : -13, 'text-anchor': 'middle' }, node.label.length > 30 ? `${node.label.slice(0, 30)}…` : node.label));
      if (node.kind === 'source') element.append(svgElement('text', { y: 4, 'text-anchor': 'middle' }, String(node.members.length)));
      element.addEventListener('click', () => this.onSelect(node));
      element.addEventListener('keydown', event => this.onKey(event, node));
      this.elements.set(node.id, element); nodeFragment.append(element);
    });
    document.querySelector('#graph-nodes').replaceChildren(nodeFragment);
    this.select(this.selected);
    if (focused && entryId) this.elements.get(entryId)?.focus({ preventScroll: true });
    this.updateCamera();
  }

  onKey(event, node) {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); this.onSelect(node); return; }
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
    event.preventDefault();
    const ids = [...this.elements.keys()];
    const direction = ['ArrowLeft', 'ArrowUp'].includes(event.key) ? -1 : 1;
    const next = ids[(ids.indexOf(node.id) + direction + ids.length) % ids.length];
    for (const [id, element] of this.elements) element.setAttribute('tabindex', id === next ? '0' : '-1');
    this.elements.get(next)?.focus({ preventScroll: true });
  }

  select(id) {
    this.selected = id;
    const adjacent = new Set();
    for (const link of this.links) {
      const active = link.source === id || link.target === id;
      link.element.classList.toggle('is-neighbor', active);
      if (active) { adjacent.add(link.source); adjacent.add(link.target); }
    }
    for (const [nodeId, element] of this.elements) {
      element.classList.toggle('is-selected', nodeId === id);
      element.classList.toggle('is-neighbor', nodeId !== id && adjacent.has(nodeId));
      element.setAttribute('aria-pressed', nodeId === id ? 'true' : 'false');
    }
  }
}
