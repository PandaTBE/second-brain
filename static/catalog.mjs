import { formatPrice, sortModels, filterModels, contextLabel } from './model-utils.mjs';

const make = (tag, className, text = '') => {
  const element = document.createElement(tag);
  element.className = className;
  element.textContent = text;
  return element;
};

export function createCatalog({ onSelect, onRefresh }) {
  const list = document.querySelector('#model-list');
  const search = document.querySelector('#model-search');
  const sort = document.querySelector('#model-sort');
  const more = document.querySelector('#show-more-models');
  const refresh = document.querySelector('#refresh-models');
  let state = { models: [], selectedId: '', currency: 'USD', visible: 24 };

  function message(title, description, error = false) {
    const empty = make('div', `catalog-empty${error ? ' error' : ''}`);
    empty.append(make('span', 'empty-symbol', error ? '↻' : '✦'));
    empty.append(make('strong', '', title), make('p', '', description));
    list.replaceChildren(empty);
    more.hidden = true;
  }

  function render(keepScroll = false) {
    const top = keepScroll ? list.scrollTop : 0;
    const filtered = sortModels(filterModels(state.models, search.value), sort.value);
    document.querySelector('#catalog-count').textContent = search.value.trim()
      ? `Найдено ${filtered.length} из ${state.models.length}`
      : `${state.models.length} моделей в каталоге`;
    if (!filtered.length) {
      message(state.models.length ? 'Ничего не нашлось' : 'В каталоге нет моделей',
        state.models.length ? 'Попробуйте другое название или укажите ID вручную.' : 'Можно указать точный ID модели вручную.');
      return;
    }
    const fragment = document.createDocumentFragment();
    for (const model of filtered.slice(0, state.visible)) {
      const row = make('button', 'model-row');
      row.type = 'button';
      row.dataset.modelId = model.id;
      row.setAttribute('aria-pressed', String(state.selectedId === model.id));
      row.setAttribute('aria-label', `${model.name || model.id}. Вход: ${formatPrice(model.input_price, state.currency)}, выход: ${formatPrice(model.output_price, state.currency)} за миллион токенов.`);
      const name = make('div', 'model-row-name');
      name.append(make('strong', '', model.name || model.id), make('code', '', model.id));
      const prices = make('div', 'model-prices', `${formatPrice(model.input_price, state.currency)} / ${formatPrice(model.output_price, state.currency)}`);
      if (model.input_price === 0 && model.output_price === 0) prices.classList.add('free-price');
      prices.append(make('small', '', contextLabel(model.context_length)));
      row.append(name, prices);
      fragment.append(row);
    }
    list.replaceChildren(fragment);
    list.scrollTop = top;
    more.hidden = filtered.length <= state.visible;
    more.textContent = `Показать ещё ${Math.min(24, filtered.length - state.visible)}`;
  }

  list.addEventListener('click', (event) => {
    const button = event.target.closest('[data-model-id]');
    const model = button && state.models.find((item) => item.id === button.dataset.modelId);
    if (model) onSelect(model);
  });
  search.addEventListener('input', () => { state = { ...state, visible: 24 }; render(); });
  sort.addEventListener('change', () => { state = { ...state, visible: 24 }; render(); });
  more.addEventListener('click', () => { state = { ...state, visible: state.visible + 24 }; render(true); });
  refresh.addEventListener('click', onRefresh);

  return {
    clear() {
      state = { ...state, models: [], selectedId: '', visible: 24 };
      search.value = '';
      document.querySelector('#catalog-count').textContent = 'Каталог моделей';
      document.querySelector('#catalog-updated').textContent = 'Из каталога провайдера';
      message('Загрузите каталог моделей', 'Введите токен и нажмите «Обновить» или укажите ID вручную.');
    },
    loading(value) {
      list.setAttribute('aria-busy', String(value));
      refresh.disabled = value;
      document.querySelector('#refresh-icon').classList.toggle('spin', value);
      if (value && !state.models.length) message('Загружаем каталог…', 'Получаем доступные модели и цены у провайдера.');
    },
    setModels(data) {
      state = { ...state, models: data.models, currency: data.currency || 'USD', visible: 24 };
      const date = new Date(data.fetched_at);
      document.querySelector('#catalog-updated').textContent = Number.isNaN(date.getTime()) ? 'Каталог обновлён' : `Обновлено в ${date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}`;
      document.querySelector('.pricing-note>span:first-child').textContent = `Цены в ${state.currency} за 1 млн токенов.`;
      render();
    },
    setError(error) {
      message('Не удалось загрузить каталог', error.message, true);
      document.querySelector('#catalog-count').textContent = 'Каталог недоступен';
    },
    setSelection(modelId) {
      state = { ...state, selectedId: modelId };
      list.querySelectorAll('[data-model-id]').forEach((button) => {
        button.setAttribute('aria-pressed', String(button.dataset.modelId === modelId));
      });
    },
  };
}
