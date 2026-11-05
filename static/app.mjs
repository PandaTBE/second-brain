import { apiRequest } from './api.mjs';
import { createCatalog } from './catalog.mjs';
import { endpointScope, changeEndpoint, changePrivateNetwork, sameEndpoint } from './endpoint-policy.mjs';

const $ = (selector) => document.querySelector(selector);
const tokenInput = $('#api-token');
const symbols = { openrouter: '↗', simpleclaw: 'S', deepseek: '≈', openai: '✳', custom: '⌘' };
let state = {
  csrf: '', providers: [], draft: { provider: 'openrouter', base_url: '', model_id: '', model_name: '', allow_private_network: false },
  tokenSaved: false, storageAvailable: true, ready: false, busy: '', scopeVersion: 0,
  draftVersion: 0, catalogRequest: 0, credentialRequest: 0, connection: null, savedSettings: null,
  mutationScope: null,
};
const catalog = createCatalog({ onSelect: selectModel, onRefresh: loadModels });

function provider() { return state.providers.find((item) => item.id === state.draft.provider); }
function scope() { return endpointScope(state.draft); }
function payload() { return { ...scope(), token: tokenInput.value || null }; }
function tokenExists() { return Boolean(tokenInput.value || state.tokenSaved); }

function actionMessage(message, kind = '') {
  const element = $('#action-message');
  element.textContent = message;
  element.className = `notice${kind ? ` ${kind}` : ''}`;
  element.hidden = !message;
  element.setAttribute('role', kind === 'error' ? 'alert' : 'status');
}

function changed() {
  state = { ...state, draftVersion: state.draftVersion + 1, connection: null };
  actionMessage('');
  updateSummary();
}

function setBusy(value) {
  const mutating = value === 'save' || value === 'delete';
  state = {
    ...state, busy: value,
    mutationScope: mutating ? JSON.stringify(scope()) : null,
    credentialRequest: state.credentialRequest + (mutating ? 1 : 0),
  };
  $('#save-settings').disabled = !state.ready || Boolean(value);
  $('#test-connection').disabled = !state.ready || Boolean(value);
  $('#confirm-delete').disabled = Boolean(value);
  $('#delete-token').disabled = Boolean(value);
  $('#save-settings span:first-child').textContent = value === 'save' ? 'Сохраняем…' : 'Сохранить настройки';
  $('#test-connection').textContent = value === 'test' ? 'Проверяем…' : 'Проверить подключение';
}

function resetTokenField() {
  tokenInput.value = '';
  tokenInput.type = 'password';
  $('#toggle-token').setAttribute('aria-pressed', 'false');
  $('#toggle-token').setAttribute('aria-label', 'Показать введённый токен');
}

function updateSummary() {
  $('#summary-provider').textContent = provider()?.label || '—';
  $('#summary-model').textContent = state.draft.model_name || state.draft.model_id || 'Не выбрана';
  $('#summary-model').title = state.draft.model_id;
  $('#summary-token').textContent = tokenInput.value ? 'Введён, не сохранён' : state.tokenSaved ? 'Сохранён в Keychain' : 'Не добавлен';
  $('#summary-connection').textContent = state.connection ? state.connection.status === 'ok' ? 'API отвечает' : 'Есть замечание' : 'Не проверено';
  $('#summary-connection').dataset.state = state.connection ? state.connection.status === 'ok' ? 'ok' : 'error' : '';
  $('#selected-model-name').textContent = state.draft.model_name || state.draft.model_id || 'Модель пока не выбрана';
  $('#selected-model-id').textContent = state.draft.model_id || 'Выберите из каталога или укажите ID';
  $('#selected-model-check').hidden = !state.draft.model_id;
  $('#delete-token').hidden = !state.tokenSaved;
  $('#token-hint').textContent = state.tokenSaved
    ? 'Ключ уже сохранён. Оставьте поле пустым, чтобы использовать его, или вставьте новый.'
    : 'Токен не сохраняется в браузере и не появляется в настройках.';
  tokenInput.placeholder = state.tokenSaved ? 'Токен сохранён в Связке ключей' : 'Вставьте ваш API-токен';
  const saved = state.savedSettings;
  const complete = state.tokenSaved && !tokenInput.value && saved
    && sameEndpoint(saved, state.draft)
    && saved.model_id === state.draft.model_id && Boolean(state.draft.model_id);
  $('#setup-badge').classList.toggle('complete', complete);
  $('#setup-badge').lastChild.textContent = complete ? ' Настройки сохранены' : saved?.model_id ? ' Есть изменения' : ' Первый запуск';
  const message = state.connection ? 'Генерация не запускалась' : tokenExists() && state.draft.model_id ? 'Можно проверить и сохранить' : 'Завершите три шага слева';
  $('#summary-state span:last-child').textContent = message;
  $('#summary-state .status-dot').classList.toggle('neutral', !state.connection);
  catalog.setSelection(state.draft.model_id);
}

function renderProviders() {
  const fragment = document.createDocumentFragment();
  for (const item of state.providers) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'provider-card';
    button.dataset.provider = item.id;
    button.setAttribute('aria-pressed', String(item.id === state.draft.provider));
    button.title = item.description || item.label;
    const symbol = document.createElement('span');
    symbol.className = 'provider-symbol';
    symbol.textContent = symbols[item.id] || '◇';
    symbol.setAttribute('aria-hidden', 'true');
    const label = document.createElement('span');
    label.className = 'provider-label';
    label.textContent = item.label;
    const tick = document.createElement('span');
    tick.className = 'provider-tick';
    tick.textContent = '✓';
    tick.setAttribute('aria-hidden', 'true');
    button.append(symbol, label, tick);
    fragment.append(button);
  }
  $('#provider-list').replaceChildren(fragment);
}

function updateProviderFields() {
  $('#provider-list').querySelectorAll('[data-provider]').forEach((button) => {
    button.setAttribute('aria-pressed', String(button.dataset.provider === state.draft.provider));
  });
  const custom = state.draft.provider === 'custom';
  $('#custom-endpoint').hidden = !custom;
  $('#custom-private-network').hidden = !custom;
  $('#allow-private-network').checked = scope().allow_private_network;
  $('#preset-endpoint').hidden = custom;
  $('#base-url').value = state.draft.base_url;
  $('#preset-endpoint-text').textContent = state.draft.base_url;
  const keyLink = provider()?.key_url;
  const validLink = keyLink && /^https:\/\//i.test(keyLink);
  $('#get-key').hidden = !validLink;
  if (validLink) $('#get-key').href = keyLink;
  $('#delete-confirm').hidden = true;
  $('#model-id').value = state.draft.model_id;
  updateSummary();
}

async function checkCredential() {
  if (!state.ready || !scope().base_url) return;
  if (state.mutationScope === JSON.stringify(scope())) return;
  const version = state.scopeVersion;
  const request = state.credentialRequest + 1;
  state = { ...state, credentialRequest: request };
  try {
    const result = await apiRequest('/api/credential-status', { method: 'POST', data: scope(), csrf: state.csrf });
    if (state.scopeVersion !== version || state.credentialRequest !== request) return;
    state = { ...state, tokenSaved: result.token_saved };
    updateSummary();
  } catch (error) {
    if (state.scopeVersion === version && state.credentialRequest === request) actionMessage(error.message, 'error');
  }
}

async function chooseProvider(id) {
  if (id === state.draft.provider) return;
  const item = state.providers.find((entry) => entry.id === id);
  if (!item) return;
  state = { ...state, draft: { ...changeEndpoint(state.draft, id, item.base_url || ''), model_id: '', model_name: '' }, tokenSaved: false, scopeVersion: state.scopeVersion + 1, catalogRequest: state.catalogRequest + 1 };
  resetTokenField();
  changed();
  catalog.clear();
  catalog.loading(false);
  updateProviderFields();
  await checkCredential();
  if (state.draft.provider === id && (id === 'openrouter' || id === 'simpleclaw' || state.tokenSaved)) loadModels();
}

function selectModel(model) {
  state = { ...state, draft: { ...state.draft, model_id: model.id, model_name: model.name || model.id } };
  $('#model-id').value = model.id;
  changed();
}

async function loadModels() {
  if (!state.ready) return;
  const request = state.catalogRequest + 1;
  const version = state.scopeVersion;
  state = { ...state, catalogRequest: request };
  if (!scope().base_url) { catalog.setError(new Error('Сначала укажите адрес API.')); return; }
  catalog.loading(true);
  try {
    const result = await apiRequest('/api/models', { method: 'POST', data: payload(), csrf: state.csrf });
    if (state.scopeVersion !== version || state.catalogRequest !== request) return;
    catalog.setModels(result);
  } catch (error) {
    if (state.scopeVersion === version && state.catalogRequest === request) catalog.setError(error);
  } finally {
    if (state.scopeVersion === version && state.catalogRequest === request) catalog.loading(false);
  }
}

function validateDraft() {
  if (!scope().base_url) { actionMessage('Укажите адрес API.', 'error'); $('#base-url').focus(); return false; }
  if (!tokenExists()) { actionMessage('Вставьте API-токен выбранного провайдера.', 'error'); tokenInput.focus(); return false; }
  if (!state.draft.model_id.trim()) { actionMessage('Выберите модель из каталога или укажите её ID вручную.', 'error'); $('#manual-model').open = true; $('#model-id').focus(); return false; }
  return true;
}

async function saveSettings(event) {
  event.preventDefault();
  if (state.busy || !validateDraft()) return;
  const version = state.draftVersion;
  const data = { ...payload(), model_id: state.draft.model_id.trim(), model_name: state.draft.model_name.trim() };
  setBusy('save');
  try {
    const result = await apiRequest('/api/settings', { method: 'POST', data, csrf: state.csrf });
    state = { ...state, savedSettings: { ...result.settings } };
    if (version !== state.draftVersion) {
      updateSummary();
      actionMessage('Предыдущие настройки сохранены. Текущие изменения ещё не сохранены.', 'warning');
      return;
    }
    state = { ...state, draft: { ...result.settings }, tokenSaved: result.token_saved };
    resetTokenField();
    updateProviderFields();
    actionMessage('Настройки сохранены. Токен находится в Связке ключей macOS.');
  } catch (error) { if (version === state.draftVersion) actionMessage(error.message, 'error'); }
  finally { setBusy(''); }
}

async function testConnection() {
  if (state.busy || !validateDraft()) return;
  const version = state.draftVersion;
  setBusy('test');
  try {
    const result = await apiRequest('/api/test-connection', { method: 'POST', data: { ...payload(), model_id: state.draft.model_id.trim() }, csrf: state.csrf });
    if (version !== state.draftVersion) return;
    state = { ...state, connection: result };
    updateSummary();
    actionMessage(result.message, result.status === 'warning' ? 'warning' : '');
  } catch (error) {
    if (version === state.draftVersion) { state = { ...state, connection: null }; updateSummary(); actionMessage(error.message, 'error'); }
  } finally { setBusy(''); }
}

async function deleteToken() {
  if (state.busy) return;
  const version = state.scopeVersion;
  const draftVersion = state.draftVersion;
  setBusy('delete');
  try {
    await apiRequest('/api/token', { method: 'DELETE', data: scope(), csrf: state.csrf });
    if (version !== state.scopeVersion) return;
    state = { ...state, tokenSaved: false };
    if (draftVersion === state.draftVersion) resetTokenField();
    changed();
    $('#delete-confirm').hidden = true;
    updateSummary();
    actionMessage('Сохранённый токен удалён из Связки ключей.');
  } catch (error) { if (version === state.scopeVersion) actionMessage(error.message, 'error'); }
  finally { setBusy(''); }
}

async function bootstrap() {
  $('#bootstrap-error').hidden = true;
  try {
    const result = await apiRequest('/api/bootstrap');
    state = { ...state, csrf: result.csrf_token, providers: result.providers, draft: { ...result.settings }, savedSettings: { ...result.settings }, tokenSaved: result.token_saved, storageAvailable: result.storage.available, ready: true };
    $('#storage-warning').hidden = state.storageAvailable && !result.warning;
    if (result.warning) $('#storage-warning').textContent = result.warning;
    renderProviders();
    updateProviderFields();
    setBusy('');
    if (['openrouter', 'simpleclaw'].includes(state.draft.provider) || state.tokenSaved) loadModels();
    else catalog.clear();
  } catch (error) {
    $('#bootstrap-error-text').textContent = error.message;
    $('#bootstrap-error').hidden = false;
    $('#provider-list').textContent = 'Не удалось загрузить настройки';
  }
}

$('#provider-list').addEventListener('click', (event) => { const button = event.target.closest('[data-provider]'); if (button) chooseProvider(button.dataset.provider); });
$('#base-url').addEventListener('input', () => {
  state = { ...state, draft: changeEndpoint(state.draft, state.draft.provider, $('#base-url').value), tokenSaved: false, scopeVersion: state.scopeVersion + 1, catalogRequest: state.catalogRequest + 1 };
  $('#allow-private-network').checked = scope().allow_private_network;
  resetTokenField(); changed(); catalog.clear(); catalog.loading(false);
});
$('#base-url').addEventListener('blur', checkCredential);
$('#allow-private-network').addEventListener('change', () => {
  state = { ...state, draft: changePrivateNetwork(state.draft, $('#allow-private-network').checked), scopeVersion: state.scopeVersion + 1, catalogRequest: state.catalogRequest + 1 };
  changed(); catalog.clear(); catalog.loading(false);
  checkCredential();
});
tokenInput.addEventListener('input', () => {
  if (!['openrouter', 'simpleclaw'].includes(state.draft.provider)) {
    state = { ...state, catalogRequest: state.catalogRequest + 1 };
    catalog.clear();
    catalog.loading(false);
  }
  changed();
});
$('#toggle-token').addEventListener('click', () => { const visible = tokenInput.type === 'password'; tokenInput.type = visible ? 'text' : 'password'; $('#toggle-token').setAttribute('aria-pressed', String(visible)); $('#toggle-token').setAttribute('aria-label', visible ? 'Скрыть введённый токен' : 'Показать введённый токен'); });
$('#model-id').addEventListener('input', () => { state = { ...state, draft: { ...state.draft, model_id: $('#model-id').value, model_name: $('#model-id').value } }; changed(); });
$('#settings-form').addEventListener('submit', saveSettings);
$('#test-connection').addEventListener('click', testConnection);
$('#delete-token').addEventListener('click', () => { $('#delete-confirm').hidden = false; $('#cancel-delete').focus(); });
$('#cancel-delete').addEventListener('click', () => { $('#delete-confirm').hidden = true; $('#delete-token').focus(); });
$('#confirm-delete').addEventListener('click', deleteToken);
$('#retry-bootstrap').addEventListener('click', bootstrap);
bootstrap();
