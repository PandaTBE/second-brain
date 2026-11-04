export const statusLabels = { ready: 'Карточка готова', pending: 'Ожидает обработки', error: 'С ошибкой' };

export function normalizeKeyword(value) {
  return typeof value === 'string' ? value.normalize('NFKC').trim().replace(/\s+/g, ' ').toLocaleLowerCase('ru') : '';
}

export function filterRecords(records, { status = 'all', source = 'all', query = '' } = {}) {
  const term = normalizeKeyword(query);
  return records.filter(record => (status === 'all' || record.card_status === status)
    && (source === 'all' || record.source_id === source)
    && (!term || normalizeKeyword([record.title, record.summary, record.snippet, ...(record.keywords || [])].join(' ')).includes(term)));
}

export function buildGraph(records, sources) {
  const sourceIds = [...new Set(records.map(record => record.source_id))];
  const nodes = [], links = [];
  const sourceMap = new Map(sources.map(source => [source.id, source]));
  sourceIds.forEach((id, index) => {
    const angle = (index / Math.max(sourceIds.length, 1)) * Math.PI * 2 - Math.PI / 2;
    const anchor = sourceIds.length === 1 ? { x: 500, y: 340 } : { x: 500 + Math.cos(angle) * 235, y: 340 + Math.sin(angle) * 175 };
    const members = records.filter(record => record.source_id === id);
    nodes.push({ id: `source:${id}`, kind: 'source', label: sourceMap.get(id)?.name || 'Источник', members: members.map(record => record.id), ...anchor });
    members.forEach((record, position) => {
      const theta = position * 2.3999632297;
      const radius = 43 + Math.sqrt(position + 1) * Math.min(17, 225 / Math.sqrt(members.length));
      nodes.push({ id: `record:${record.id}`, recordId: record.id, kind: 'record', label: record.title, status: record.card_status,
        x: anchor.x + Math.cos(theta) * radius, y: anchor.y + Math.sin(theta) * radius });
      links.push({ source: `source:${id}`, target: `record:${record.id}`, kind: 'source' });
    });
  });
  const tags = new Map();
  for (const record of records) {
    for (const tag of new Set((record.keywords || []).map(normalizeKeyword).filter(Boolean))) {
      if (!tags.has(tag)) tags.set(tag, []);
      tags.get(tag).push(record.id);
    }
  }
  const shared = [...tags].filter(([, members]) => members.length > 1).sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0])).slice(0, 60);
  shared.forEach(([tag, members], index) => {
    const angle = index * Math.PI * 2 / Math.max(shared.length, 1);
    const id = `keyword:${tag}`;
    nodes.push({ id, kind: 'keyword', label: tag, members, x: 500 + Math.cos(angle) * 350, y: 340 + Math.sin(angle) * 275, showLabel: index < 12 });
    members.forEach(recordId => links.push({ source: id, target: `record:${recordId}`, kind: 'keyword' }));
  });
  return { nodes, links, sharedKeywordCount: shared.length };
}

export function fitGraph(nodes, width = 1000, height = 680, padding = 45) {
  if (!nodes.length) return { x: 0, y: 0, zoom: 1 };
  const bounds = nodes.map(node => ({
    left: node.x - (node.kind === 'source' || node.showLabel ? 100 : 8),
    right: node.x + (node.kind === 'source' || node.showLabel ? 100 : 8),
    top: node.y - (node.kind === 'source' ? 42 : node.showLabel ? 28 : 8),
    bottom: node.y + (node.kind === 'source' ? 22 : 8),
  }));
  const left = Math.min(...bounds.map(node => node.left)), right = Math.max(...bounds.map(node => node.right));
  const top = Math.min(...bounds.map(node => node.top)), bottom = Math.max(...bounds.map(node => node.bottom));
  const zoom = Math.min(1.25, (width - padding * 2) / Math.max(1, right - left), (height - padding * 2) / Math.max(1, bottom - top));
  return { x: width / 2 - (left + right) / 2 * zoom, y: height / 2 - (top + bottom) / 2 * zoom, zoom };
}

export function recordChanged(record, selected) {
  return Boolean(record && (record.revision !== selected.revision || record.card_status !== selected.cardStatus || record.updated_at !== selected.updatedAt));
}

export function focusNodeId(nodes, currentId) {
  return nodes.some(node => node.id === currentId) ? currentId : nodes[0]?.id ?? null;
}

export class LatestRequest {
  #version = 0;
  begin() { this.#version += 1; return this.#version; }
  invalidate() { this.#version += 1; }
  isCurrent(version) { return version === this.#version; }
}

export function mergePage(previous, incoming) {
  const latest = new Map([...previous, ...incoming].map(record => [record.id, record]));
  return [...latest.values()];
}
