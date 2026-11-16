const statuses = ['todo', 'doing', 'done'];

export function localDay(date = new Date()) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

export function nextStatus(status, direction) {
  const index = statuses.indexOf(status);
  return index < 0 || (direction !== -1 && direction !== 1) ? null : statuses[index + direction] ?? null;
}

export function xpProgress(player) {
  const max = Math.max(1, player.next_level_xp - player.level_xp);
  const value = Math.min(max, Math.max(0, player.xp - player.level_xp));
  return { value, max, remaining: max - value, percent: Math.round(value / max * 100) };
}
