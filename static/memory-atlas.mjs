import { buildGraph, createRecordFilter, graphSignature, LatestRequest, mergePage, statusLabels, recordChanged } from './memory-atlas-model.mjs';
import { MemoryGraph } from './memory-atlas-graph.mjs';

const $ = selector => document.querySelector(selector);
const atlasGate = new LatestRequest(), detailGate = new LatestRequest(), historyGate = new LatestRequest();
let snapshot = { records: [], sources: [], total_records: 0 };
let searchSnapshot = createRecordFilter([]), currentGraph = null, currentGraphKey = null;
let sourceNames = new Map(), recordsById = new Map(), sourceOptionsKey = null;
let filter = { status: 'all', source: 'all', query: '' };
let tab = 'graph', selected = null, detailController = null, atlasController = null, historyController = null;
let history = { records: [], cursor: null, hasMore: true, loading: false, started: false };
let queryTimer;
let atlasLoaded = false;
const graph = new MemoryGraph($('#memory-graph'), selectNode);

function element(name, className = '', text = '') {
  const item = document.createElement(name);
  if (className) item.className = className;
  if (text) item.textContent = text;
  return item;
}

async function getJSON(path, signal) {
  const response = await fetch(path, { signal, cache: 'no-store', credentials: 'same-origin' });
  const envelope = await response.json();
  if (!response.ok || !envelope.success || !envelope.data) {
    const error = new Error(envelope.error?.message || 'Не удалось загрузить память. Убедитесь, что Second Brain запущен.');
    error.code = envelope.error?.code; throw error;
  }
  return envelope.data;
}

function date(value) {
  if (!value) return 'Дата не указана';
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? 'Дата не указана' : new Intl.DateTimeFormat('ru', { dateStyle: 'medium', timeStyle: 'short' }).format(parsed);
}

function sourceName(id, kind = '') {
  return sourceNames.get(id) || ({ codex: 'Codex', claude: 'Claude Code', folder: 'Заметки' }[kind] || 'Источник');
}

function clearDetail() {
  detailGate.invalidate(); detailController?.abort(); selected = null; graph.select(null);
  const content = element('div', 'detail-placeholder');
  const symbol = element('span', 'placeholder-symbol', '◎'); symbol.setAttribute('aria-hidden', 'true');
  content.append(symbol, element('h2', '', 'Каждая точка — часть памяти'), element('p', '', 'Выберите материал на графе или в истории. Нажмите на источник или тему, чтобы увидеть связанные записи.'));
  $('#detail-panel').replaceChildren(content);
}

async function loadAtlas(manual = false) {
  const request = atlasGate.begin();
  atlasController?.abort(); atlasController = new AbortController();
  if (manual) $('#refresh').disabled = true;
  try {
    const data = await getJSON('/api/memory/atlas', atlasController.signal);
    if (!atlasGate.isCurrent(request)) return;
    if (!Array.isArray(data.records) || !Array.isArray(data.sources)) throw new Error('Не удалось прочитать данные графа. Обновите страницу.');
    snapshot = data; atlasLoaded = true;
    searchSnapshot = createRecordFilter(snapshot.records);
    sourceNames = new Map(snapshot.sources.map(source => [source.id, source.name]));
    recordsById = new Map(snapshot.records.map(record => [record.id, record]));
    $('#notice').hidden = true;
    $('#updated-at').textContent = `Обновлено ${date(data.generated_at)}`;
    const allowed = new Set(snapshot.sources.map(source => source.id));
    if (filter.source !== 'all' && !allowed.has(filter.source)) filter = { ...filter, source: 'all' };
    history.records = history.records.filter(record => allowed.has(record.source_id));
    if (selected?.sourceId && !allowed.has(selected.sourceId)) clearDetail();
    if (selected?.kind === 'record' && !snapshot.truncated && !snapshot.records.some(record => record.id === selected.recordId)) clearDetail();
    renderSources(); renderCounts();
    const graphChanged = renderGraph();
    if (tab === 'history') renderHistory();
    if (selected?.kind === 'record') {
      const current = snapshot.records.find(record => record.id === selected.recordId);
      if (manual || recordChanged(current, selected)) await selectRecord(selected.recordId);
    }
    else if (selected) {
      const current = graph.nodes.get(selected.id);
      if (!current) clearDetail();
      else if (graphChanged) selectNode(current);
    }
  } catch (error) {
    if (error.name === 'AbortError' || !atlasGate.isCurrent(request)) return;
    $('#notice-text').textContent = error instanceof TypeError ? 'Нет связи с локальным сервисом. Откройте Second Brain и повторите.' : error.message;
    $('#notice').hidden = false;
    if (!snapshot.records.length) $('#graph-empty').hidden = false;
  } finally {
    if (atlasGate.isCurrent(request)) $('#refresh').disabled = false;
  }
}

function renderCounts() {
  $('#count-all').textContent = String(snapshot.total_records ?? snapshot.records.length);
  for (const status of ['ready', 'pending', 'error']) $('#count-' + status).textContent = String(snapshot.records.filter(record => record.card_status === status).length) + (snapshot.truncated ? '+' : '');
  $('#truncation').hidden = !snapshot.truncated;
  $('#truncation').textContent = `На графе показаны последние ${snapshot.records.length} из ${snapshot.total_records} материалов. Вся память доступна во вкладке «История».`;
}

function renderSources() {
  const select = $('#source-filter');
  const key = JSON.stringify(snapshot.sources.map(source => [source.id, source.name]));
  if (sourceOptionsKey === key) { select.value = filter.source; return; }
  sourceOptionsKey = key;
  const options = [element('option', '', 'Все источники')]; options[0].value = 'all';
  for (const source of snapshot.sources) {
    const option = element('option', '', source.name); option.value = source.id; options.push(option);
  }
  select.replaceChildren(...options); select.value = filter.source;
}

function renderGraph() {
  const records = searchSnapshot(filter);
  const key = graphSignature(records, snapshot.sources);
  const changed = key !== currentGraphKey;
  if (changed) {
    currentGraph = buildGraph(records, snapshot.sources);
    currentGraphKey = key;
    graph.render(currentGraph);
  }
  $('#graph-count').textContent = `${records.length} материалов · ${currentGraph.sharedKeywordCount} общих тем`;
  $('#graph-empty').hidden = records.length > 0;
  return changed;
}

function selectNode(node) {
  graph.select(node.id);
  if (node.kind === 'record') { selectRecord(node.recordId); return; }
  detailGate.invalidate(); detailController?.abort(); selected = node;
  const content = document.createDocumentFragment();
  content.append(element('p', 'detail-label', node.kind === 'source' ? 'ИСТОЧНИК' : 'ОБЩАЯ ТЕМА'), element('h2', '', node.label),
    element('p', 'detail-meta', `${node.members.length} материалов в текущем представлении`));
  if (node.kind === 'keyword') content.append(element('p', '', 'Эти материалы имеют общее ключевое слово в готовых карточках. Связь не означает, что все их утверждения совпадают.'));
  for (const id of node.members) {
    const record = recordsById.get(id);
    if (!record) continue;
    const button = element('button', 'related-button', record.title); button.type = 'button';
    button.append(element('small', '', `${sourceName(record.source_id, record.source_kind)} · ${statusLabels[record.card_status] || 'Материал'}`));
    button.addEventListener('click', () => selectRecord(record.id)); content.append(button);
  }
  $('#detail-panel').replaceChildren(content);
}

async function selectRecord(id) {
  const request = detailGate.begin();
  detailController?.abort(); detailController = new AbortController();
  const known = snapshot.records.find(record => record.id === id) || history.records.find(record => record.id === id);
  selected = { kind: 'record', id: `record:${id}`, recordId: id, sourceId: known?.source_id };
  graph.select(selected.id); renderHistory();
  $('#detail-panel').replaceChildren(element('p', 'detail-meta', 'Открываем материал…'));
  try {
    const data = await getJSON('/api/memory/records/' + encodeURIComponent(id), detailController.signal);
    if (!detailGate.isCurrent(request)) return;
    if (atlasLoaded && !snapshot.sources.some(source => source.id === data.record.source_id)) {
      throw new Error('Источник материала больше не доступен.');
    }
    selected = { ...selected, sourceId: data.record.source_id, revision: data.record.revision,
      cardStatus: data.record.card_status, updatedAt: data.record.updated_at };
    renderDetail(data.record);
  } catch (error) {
    if (error.name === 'AbortError' || !detailGate.isCurrent(request)) return;
    $('#detail-panel').replaceChildren(element('h2', '', 'Материал недоступен'), element('p', '', error instanceof TypeError ? 'Не удалось связаться с Second Brain. Попробуйте открыть материал снова.' : error.message));
  }
}

function renderDetail(record) {
  const content = document.createDocumentFragment(), card = record.card;
  content.append(element('p', 'detail-label', statusLabels[record.card_status] || 'МАТЕРИАЛ'), element('h2', '', card?.title || record.title),
    element('div', 'detail-meta', `${sourceName(record.source_id, record.source_kind)} · Ревизия ${record.revision || 1}\nДобавлен: ${date(record.created_at)}\nИзменён: ${date(record.updated_at)}`));
  if (card?.summary) content.append(element('p', 'detail-summary', card.summary));
  else content.append(element('p', 'detail-summary', record.card_error || 'Карточка ожидает обработки. Исходный текст уже доступен ниже.'));
  if (Array.isArray(card?.keywords)) {
    const chips = element('div', 'keyword-chips');
    for (const keyword of card.keywords) chips.append(element('span', '', keyword));
    content.append(chips);
  }
  if (record.source_uri) {
    content.append(element('h3', '', 'Источник'), element('div', 'source-uri', record.source_uri));
    const copy = element('button', 'text-button', 'Скопировать ссылку на источник'); copy.type = 'button';
    copy.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(record.source_uri); copy.textContent = 'Ссылка скопирована'; }
      catch { copy.textContent = 'Выделите и скопируйте ссылку выше'; }
    });
    content.append(copy);
  }
  if (Array.isArray(card?.atoms) && card.atoms.length) {
    content.append(element('h3', '', 'Факты и подтверждения'));
    for (const atom of card.atoms) {
      const evidence = element('section', 'evidence');
      if (atom.text) evidence.append(element('p', '', atom.text));
      if (atom.evidence) evidence.append(element('blockquote', '', atom.evidence));
      content.append(evidence);
    }
  }
  const details = element('details'); details.append(element('summary', '', 'Исходный текст'), element('pre', '', record.text || 'Текст отсутствует.'));
  content.append(details);
  $('#detail-panel').replaceChildren(content);
}

function resetHistory() {
  historyGate.invalidate(); historyController?.abort();
  history = { records: [], cursor: null, hasMore: true, loading: false, started: false };
  $('#history-error').hidden = true; renderHistory();
  if (tab === 'history') loadHistory();
}

async function loadHistory() {
  if (history.loading || !history.hasMore) return;
  const request = historyGate.begin(), cursor = history.cursor;
  historyController = new AbortController();
  history = { ...history, loading: true, started: true };
  $('#history-error').hidden = true; renderHistory();
  const parameters = new URLSearchParams({ q: filter.query, card_status: filter.status, limit: '30' });
  if (cursor) parameters.set('cursor', cursor);
  try {
    const data = await getJSON('/api/memory/browse?' + parameters, historyController.signal);
    if (!historyGate.isCurrent(request)) return;
    if (!Array.isArray(data.results) || (data.has_more && (!data.next_cursor || data.next_cursor === cursor))) throw new Error('Не удалось продолжить список. Обновите его и попробуйте снова.');
    const allowed = new Set(snapshot.sources.map(source => source.id));
    const incoming = atlasLoaded ? data.results.filter(record => allowed.has(record.source_id)) : data.results;
    history = { records: mergePage(history.records, incoming), cursor: data.next_cursor,
      hasMore: Boolean(data.has_more), loading: false, started: true };
    renderHistory();
  } catch (error) {
    if (error.name === 'AbortError' || !historyGate.isCurrent(request)) return;
    history = { ...history, loading: false };
    $('#history-error-text').textContent = error instanceof TypeError ? 'Нет связи с Second Brain. Загруженные записи сохранены.' : error.message;
    $('#history-error').hidden = false; renderHistory();
  }
}

function renderHistory() {
  if (tab !== 'history') return;
  const focused = document.activeElement?.closest('.history-entry')?.dataset.recordId;
  const buttons = new Map();
  const fragment = document.createDocumentFragment();
  for (const record of history.records) {
    const button = element('button', `history-entry ${record.card_status}${selected?.recordId === record.id ? ' selected' : ''}`); button.type = 'button';
    button.dataset.recordId = record.id; buttons.set(record.id, button);
    button.setAttribute('aria-pressed', selected?.recordId === record.id ? 'true' : 'false');
    button.append(element('strong', '', record.card?.title || record.title));
    if (record.summary || record.snippet) button.append(element('p', '', record.summary || record.snippet));
    button.append(element('small', '', `${sourceName(record.source_id, record.source_kind)} · ${statusLabels[record.card_status] || 'Материал'} · Ревизия ${record.revision || 1}`),
      element('small', '', `Добавлен: ${date(record.created_at)} · Изменён: ${date(record.updated_at)}`));
    button.addEventListener('click', () => selectRecord(record.id)); fragment.append(button);
  }
  const scroll = $('#history-scroll').scrollTop;
  $('#history-list').replaceChildren(fragment); $('#history-scroll').scrollTop = scroll;
  if (focused) buttons.get(focused)?.focus({ preventScroll: true });
  $('#history-count').textContent = `${history.records.length} загружено`;
  $('#history-empty').hidden = history.records.length > 0 || history.loading || !$('#history-error').hidden;
  $('#history-progress').textContent = history.loading ? 'Загружаем материалы…' : !history.hasMore ? 'Все материалы загружены' : '';
  $('#load-history').hidden = !history.hasMore || history.loading || !$('#history-error').hidden;
}

function switchTab(next) {
  tab = next;
  for (const name of ['graph', 'history']) {
    $('#tab-' + name).setAttribute('aria-selected', name === tab ? 'true' : 'false');
    $('#tab-' + name).tabIndex = name === tab ? 0 : -1;
    $('#' + name + '-panel').hidden = name !== tab;
  }
  if (tab === 'history') { renderHistory(); if (!history.started) loadHistory(); }
  else renderGraph();
}

function applyFilter() {
  clearDetail(); renderGraph(); resetHistory();
  for (const button of document.querySelectorAll('[data-status]')) {
    const active = button.dataset.status === filter.status;
    button.classList.toggle('selected', active); button.setAttribute('aria-pressed', String(active));
  }
}

for (const button of document.querySelectorAll('[data-status]')) button.addEventListener('click', () => {
  filter = { ...filter, status: button.dataset.status }; applyFilter();
});
$('#source-filter').addEventListener('change', event => { filter = { ...filter, source: event.target.value }; clearDetail(); renderGraph(); });
$('#search').addEventListener('input', event => {
  const query = event.target.value;
  historyGate.invalidate(); historyController?.abort();
  clearTimeout(queryTimer);
  queryTimer = setTimeout(() => { filter = { ...filter, query }; applyFilter(); }, 250);
});
for (const name of ['graph', 'history']) $('#tab-' + name).addEventListener('click', () => switchTab(name));
$('.tabs').addEventListener('keydown', event => {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault(); switchTab(event.key === 'Home' ? 'graph' : event.key === 'End' ? 'history' : tab === 'graph' ? 'history' : 'graph');
  $('#tab-' + tab).focus();
});
$('#refresh').addEventListener('click', () => { resetHistory(); loadAtlas(true); });
$('#retry-atlas').addEventListener('click', () => loadAtlas(true));
$('#retry-history').addEventListener('click', loadHistory);
$('#reset-history').addEventListener('click', resetHistory);
$('#load-history').addEventListener('click', loadHistory);
const observer = new IntersectionObserver(entries => {
  if (tab === 'history' && entries.some(entry => entry.isIntersecting) && $('#history-error').hidden) loadHistory();
}, { root: $('#history-scroll'), rootMargin: '160px' });
observer.observe($('#history-sentinel'));
document.addEventListener('visibilitychange', () => { if (!document.hidden) loadAtlas(); });
setInterval(() => { if (!document.hidden) loadAtlas(); }, 30_000);
loadAtlas();
