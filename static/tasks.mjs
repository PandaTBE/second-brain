import { apiRequest } from './api.mjs';
import { localDay, nextStatus, xpProgress } from './tasks-model.mjs';

const $ = selector => document.querySelector(selector);
const statusNames = { todo: 'К выполнению', doing: 'В работе', done: 'Готово' };
const statNames = { strength: 'Сила', agility: 'Ловкость', intelligence: 'Интеллект', vitality: 'Выносливость', perception: 'Восприятие' };
const difficultyNames = { 1: 'Лёгкая', 2: 'Средняя', 3: 'Сложная' };
const emptyMessages = {
  todo: 'С чего начнёте сегодня? Добавьте задачу выше.',
  doing: 'Выберите дело и перенесите его сюда.',
  done: 'Здесь будут ваши выполненные дела. Всё начинается с первого шага.',
};
let board = null, csrf = '', pending = false, refreshQueued = false, draggedId = null;

function element(tag, className = '', text = '') {
  const item = document.createElement(tag);
  item.className = className;
  item.textContent = text;
  return item;
}

function showError(message = '') {
  $('#tasks-error-text').textContent = message;
  $('#tasks-error').hidden = !message;
}

function setControls() {
  const ready = !pending && Boolean(csrf) && board?.day === localDay();
  $('#task-fields').disabled = !ready;
  $('#refresh-tasks').disabled = pending;
  $('#retry-tasks').disabled = pending;
  $('#task-board').setAttribute('aria-busy', String(pending));
  for (const card of document.querySelectorAll('.task-card')) {
    card.draggable = ready;
    for (const button of card.querySelectorAll('button')) button.disabled = !ready;
  }
}

function renderPlayer() {
  const player = board.player, progress = xpProgress(player);
  $('#player-level').textContent = player.level;
  $('#player-rank').textContent = player.rank;
  $('#player-xp').textContent = `${player.xp} XP всего`;
  $('#player-progress').max = progress.max;
  $('#player-progress').value = progress.value;
  $('#xp-caption').textContent = `${progress.value} / ${progress.max} XP уровня`;
  $('#xp-percent').textContent = `${progress.percent}%`;
  $('#xp-remaining').textContent = `До следующего уровня — ${progress.remaining} XP`;
  for (const stat of Object.keys(statNames)) $(`#stat-${stat}`).textContent = player.stats[stat];
}

function cardButton(text, label, action, className = '') {
  const button = element('button', `card-action ${className}`, text);
  button.type = 'button';
  button.setAttribute('aria-label', label);
  button.title = label;
  button.addEventListener('click', action);
  return button;
}

function taskCard(task) {
  const card = element('li', 'task-card');
  card.dataset.taskId = task.id;
  const topline = element('div', 'card-topline');
  topline.append(element('span', `task-kind${task.kind === 'daily' ? ' daily' : ''}`, task.kind === 'daily' ? '↻ Ежедневно' : 'Задача'),
    element('span', 'task-xp', task.status === 'done' ? `${task.xp} XP получено` : `+${task.xp} XP`));
  const title = element('h4', 'task-title', task.title);
  title.tabIndex = -1;
  const meta = element('div', 'card-meta');
  meta.append(element('span', 'stat-chip', statNames[task.stat]), element('span', '', difficultyNames[task.difficulty]));
  const actions = element('div', 'card-actions');
  for (const direction of [-1, 1]) {
    const destination = nextStatus(task.status, direction);
    if (destination) actions.append(cardButton(direction === -1 ? '←' : '→',
      `Переместить «${task.title}» в «${statusNames[destination]}»`, () => moveTask(task.id, destination)));
  }
  actions.append(cardButton('Удалить', `Удалить «${task.title}»`, () => deleteTask(task), 'delete-task'));
  card.append(topline, title, meta, actions);
  card.addEventListener('dragstart', event => {
    if (pending || board?.day !== localDay() || event.target.closest('button')) { event.preventDefault(); return; }
    draggedId = task.id;
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', task.id);
    card.classList.add('dragging');
  });
  card.addEventListener('dragend', clearDrag);
  return card;
}

function renderBoard(focusId = null) {
  const focusedId = focusId ?? document.activeElement?.closest('.task-card')?.dataset.taskId;
  const current = board?.day === localDay();
  $('#board-date').textContent = new Intl.DateTimeFormat('ru', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }).format(new Date());
  $('#board-summary').textContent = current ? `${board.tasks.filter(task => task.status === 'done').length} из ${board.tasks.length} выполнено` : 'Доска на сегодня';
  for (const status of Object.keys(statusNames)) {
    const tasks = current ? board.tasks.filter(task => task.status === status) : [];
    $(`#count-${status}`).textContent = current ? tasks.length : '—';
    const content = tasks.length ? tasks.map(taskCard) : [element('li', 'column-empty', current ? emptyMessages[status] : pending ? 'Загружаем задачи…' : 'Не удалось загрузить доску. Нажмите «Обновить».')];
    $(`#list-${status}`).replaceChildren(...content);
  }
  setControls();
  if (focusedId) [...document.querySelectorAll('.task-card')].find(card => card.dataset.taskId === focusedId)?.querySelector('.task-title').focus();
}

async function requestBoard(path = '/api/tasks', { method = 'GET', data, focusId, message = '', preserveError = false } = {}) {
  if (pending) { if (method === 'GET') refreshQueued = true; return false; }
  const day = localDay(), mutation = method !== 'GET';
  if (mutation && board?.day !== day) { void requestBoard(); return false; }
  pending = true;
  clearDrag();
  if (!preserveError) showError();
  $('#action-status').textContent = mutation ? 'Сохраняем изменения…' : 'Обновляем доску…';
  if (mutation) $('#level-notice').textContent = '';
  if (board?.day !== day) renderBoard();
  setControls();
  try {
    if (!csrf) {
      const bootstrap = await apiRequest('/api/bootstrap');
      if (!bootstrap.csrf_token) throw new Error('Не удалось подготовить приложение. Обновите страницу.');
      csrf = bootstrap.csrf_token;
    }
    if (day !== localDay()) { refreshQueued = true; return false; }
    const result = await apiRequest(mutation ? path : `${path}?${new URLSearchParams({ day })}`, {
      method, csrf, ...(mutation ? { data: { ...data, day } } : {}),
    });
    if (day !== localDay()) { refreshQueued = true; return mutation; }
    if (result?.day !== day || !Array.isArray(result.tasks) || !result.player?.stats) throw new Error('Приложение вернуло неполную доску. Попробуйте обновить её.');
    const previousLevel = board?.player.level;
    board = result;
    renderPlayer();
    renderBoard(focusId);
    $('#action-status').textContent = message || 'Доска обновлена.';
    if (mutation && previousLevel != null && result.player.level > previousLevel) $('#level-notice').textContent = `Уровень повышен: ${result.player.level}! Новый шаг вперёд.`;
    return true;
  } catch (error) {
    showError(`${error.message}${mutation ? ' Проверьте обновлённую доску перед повтором действия.' : ''}`);
    $('#action-status').textContent = mutation ? 'Не удалось подтвердить сохранение.' : 'Не удалось обновить доску.';
    if (mutation) refreshQueued = true;
    if (board?.day !== localDay()) renderBoard();
    return false;
  } finally {
    pending = false;
    if (board?.day !== localDay()) renderBoard();
    setControls();
    // Serialize reads and writes so a delayed snapshot cannot overwrite a mutation.
    if (refreshQueued || day !== localDay()) {
      refreshQueued = false;
      void requestBoard('/api/tasks', { preserveError: !$('#tasks-error').hidden });
    }
  }
}

function moveTask(id, status) {
  const task = board?.tasks.find(item => item.id === id);
  if (!task || task.status === status || !Object.hasOwn(statusNames, status)) return;
  void requestBoard(`/api/tasks/${encodeURIComponent(id)}/status`, {
    method: 'POST', data: { status }, focusId: id, message: `«${task.title}» — ${statusNames[status]}.`,
  });
}

async function deleteTask(task) {
  if (pending) return;
  const daily = task.kind === 'daily' ? ' Она исчезнет и из следующих дней.' : '';
  if (!window.confirm(`Удалить «${task.title}»?${daily} Полученный XP сохранится.`)) return;
  if (await requestBoard(`/api/tasks/${encodeURIComponent(task.id)}`, { method: 'DELETE', message: 'Задача удалена. Полученный XP сохранён.' })) $('#task-title').focus();
}

function clearDrag() {
  draggedId = null;
  for (const item of document.querySelectorAll('.drop-target, .dragging')) item.classList.remove('drop-target', 'dragging');
}

for (const column of document.querySelectorAll('.task-column')) {
  column.addEventListener('dragover', event => {
    if (!draggedId || pending || board?.day !== localDay()) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    column.classList.add('drop-target');
  });
  column.addEventListener('dragleave', event => {
    if (!column.contains(event.relatedTarget)) column.classList.remove('drop-target');
  });
  column.addEventListener('drop', event => {
    if (!draggedId) return;
    event.preventDefault();
    const id = draggedId;
    clearDrag();
    moveTask(id, column.dataset.status);
  });
}

$('#task-title').addEventListener('input', () => $('#task-title').setCustomValidity(''));
$('#task-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (pending) return;
  const input = $('#task-title'), title = input.value.trim();
  input.setCustomValidity(title ? '' : 'Введите название задачи.');
  if (!$('#task-form').reportValidity()) return;
  const created = await requestBoard('/api/tasks', { method: 'POST', data: {
    title, kind: $('#task-kind').value, stat: $('#task-stat').value, difficulty: Number($('#task-difficulty').value),
  }, message: 'Задача добавлена в «К выполнению».' });
  if (created) { input.value = ''; input.focus(); }
});
$('#refresh-tasks').addEventListener('click', () => void requestBoard());
$('#retry-tasks').addEventListener('click', () => void requestBoard());
window.addEventListener('focus', () => void requestBoard());
document.addEventListener('visibilitychange', () => { if (!document.hidden) void requestBoard(); });

function scheduleDayCheck() {
  const now = new Date(), midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  setTimeout(() => {
    if (board && board.day !== localDay()) void requestBoard();
    scheduleDayCheck();
  }, Math.min(60_000, midnight - now + 50));
}

void requestBoard();
scheduleDayCheck();
