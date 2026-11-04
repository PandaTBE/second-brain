import test from 'node:test';
import assert from 'node:assert/strict';
import { formatPrice, sortModels, filterModels, contextLabel } from '../static/model-utils.mjs';

test('prices distinguish free, unknown, and small paid amounts', () => {
  assert.equal(formatPrice(null), 'Нет данных');
  assert.equal(formatPrice(undefined), 'Нет данных');
  assert.equal(formatPrice(0), 'Бесплатно');
  assert.equal(formatPrice(0.05), '$0.05');
  assert.equal(formatPrice(1.25), '$1.25');
});

test('provider currencies stay separate without conversion', () => {
  assert.equal(formatPrice(1.25, 'RUB'), '1,25 ₽');
  assert.equal(formatPrice(0, 'RUB'), 'Бесплатно');
  assert.equal(formatPrice(null, 'RUB'), 'Нет данных');
});

test('cheap-first ordering puts unknown prices last and breaks ties on output', () => {
  const models = [
    { id: 'unknown', name: 'Unknown', input_price: null, output_price: null },
    { id: 'paid-b', name: 'Beta', input_price: 0.1, output_price: 1 },
    { id: 'free', name: 'Free', input_price: 0, output_price: 0 },
    { id: 'paid-a', name: 'Alpha', input_price: 0.1, output_price: 0.2 },
  ];
  assert.deepEqual(sortModels(models, 'price').map((m) => m.id), ['free', 'paid-a', 'paid-b', 'unknown']);
  assert.deepEqual(models.map((m) => m.id), ['unknown', 'paid-b', 'free', 'paid-a']);
});

test('search matches both model identifiers and names without changing the collection', () => {
  const models = [{ id: 'vendor/fast-mini', name: 'Быстрая модель' }, { id: 'vendor/large', name: 'Large' }];
  assert.equal(filterModels(models, 'FAST')[0].id, 'vendor/fast-mini');
  assert.equal(filterModels(models, 'быстрая')[0].id, 'vendor/fast-mini');
  assert.equal(filterModels(models, 'absent').length, 0);
  assert.equal(filterModels(models, '').length, 2);
});

test('context formatting handles missing and real context windows', () => {
  assert.equal(contextLabel(null), 'Контекст не указан');
  assert.equal(contextLabel(128000), '128 тыс. токенов');
  assert.equal(contextLabel(1000000), '1 млн токенов');
});
