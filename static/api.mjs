export async function apiRequest(path, { method = 'GET', data, csrf = '' } = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 25000);
  try {
    const response = await fetch(path, {
      method,
      credentials: 'same-origin',
      cache: 'no-store',
      headers: {
        Accept: 'application/json',
        ...(data === undefined ? {} : { 'Content-Type': 'application/json' }),
        ...(method === 'GET' ? {} : { 'X-Brain-CSRF': csrf }),
      },
      ...(data === undefined ? {} : { body: JSON.stringify(data) }),
      signal: controller.signal,
    });
    let result;
    try { result = await response.json(); }
    catch { throw new Error('Приложение получило неожиданный ответ. Попробуйте ещё раз.'); }
    if (!response.ok || !result.success) {
      throw new Error(result.error?.message || 'Не удалось выполнить запрос. Попробуйте ещё раз.');
    }
    return result.data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('Сервер не ответил вовремя. Попробуйте ещё раз.');
    if (error instanceof TypeError) throw new Error('Нет связи с локальным приложением. Проверьте, что оно запущено.');
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}
