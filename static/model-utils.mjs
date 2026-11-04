export function formatPrice(value, currency = 'USD') {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return 'Нет данных';
  if (value === 0) return 'Бесплатно';
  if (currency === 'RUB') return `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 4 }).format(value)} ₽`;
  return `$${new Intl.NumberFormat('en-US', { maximumFractionDigits: 4 }).format(value)}`;
}

export function sortModels(models, order = 'price') {
  const price = (value) => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : Infinity;
  return [...models].sort((a, b) => {
    if (order === 'name') return (a.name || a.id).localeCompare(b.name || b.id, 'ru');
    return price(a.input_price) - price(b.input_price)
      || price(a.output_price) - price(b.output_price)
      || (a.name || a.id).localeCompare(b.name || b.id, 'ru');
  });
}

export function filterModels(models, query) {
  const needle = query.trim().toLocaleLowerCase('ru');
  return models.filter((model) => `${model.id} ${model.name || ''}`.toLocaleLowerCase('ru').includes(needle));
}

export function contextLabel(value) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return 'Контекст не указан';
  const divisor = value >= 1000000 ? 1000000 : 1000;
  const unit = value >= 1000000 ? 'млн' : 'тыс.';
  return `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(value / divisor)} ${unit} токенов`;
}
