import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { localDay, nextStatus, xpProgress } from '../static/tasks-model.mjs';

test('Local day uses calendar components at year and leap-day boundaries', () => {
  assert.equal(localDay(new Date(2026, 0, 1, 0, 0, 0)), '2026-01-01');
  assert.equal(localDay(new Date(2024, 1, 29, 23, 59, 59)), '2024-02-29');
  assert.equal(localDay(new Date(2026, 11, 31, 23, 59, 59)), '2026-12-31');
});

test('Near midnight, local day differs from UTC in both timezone directions', () => {
  for (const [timezone, instant, expected] of [
    ['Europe/Moscow', '2026-11-15T21:00:00Z', '2026-11-16'],
    ['America/Los_Angeles', '2026-11-17T07:59:59Z', '2026-11-16'],
    ['Pacific/Kiritimati', '2026-12-31T10:00:00Z', '2027-01-01'],
    ['America/New_York', '2026-03-08T06:59:59Z', '2026-03-08'],
    ['America/New_York', '2026-03-08T07:00:00Z', '2026-03-08'],
  ]) {
    const script = `import { localDay } from ${JSON.stringify(new URL('../static/tasks-model.mjs', import.meta.url).href)};
      process.stdout.write(localDay(new Date(${JSON.stringify(instant)})));`;
    const result = spawnSync(process.execPath, ['--input-type=module', '-e', script], {
      env: { ...process.env, TZ: timezone }, encoding: 'utf8',
    });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout, expected, timezone);
  }
});

test('Movement follows kanban order and never wraps or accepts unknown directions', () => {
  assert.equal(nextStatus('todo', 1), 'doing');
  assert.equal(nextStatus('doing', 1), 'done');
  assert.equal(nextStatus('done', -1), 'doing');
  assert.equal(nextStatus('doing', -1), 'todo');
  assert.equal(nextStatus('todo', -1), null);
  assert.equal(nextStatus('done', 1), null);
  assert.equal(nextStatus('missing', 1), null);
  for (const direction of [0, 2, -2, '1']) assert.equal(nextStatus('doing', direction), null);
});

test('XP progress uses server thresholds without deriving levels or modifying player', () => {
  const player = Object.freeze({ xp: 125, level: 2, level_xp: 50, next_level_xp: 200 });
  assert.deepEqual(xpProgress(player), { value: 75, max: 150, remaining: 75, percent: 50 });
  assert.deepEqual(xpProgress({ xp: 450, level_xp: 450, next_level_xp: 800 }),
    { value: 0, max: 350, remaining: 350, percent: 0 });
  assert.deepEqual(xpProgress({ xp: 0, level_xp: 0, next_level_xp: 50 }),
    { value: 0, max: 50, remaining: 50, percent: 0 });
});

test('XP display stays within the progress element range at threshold boundaries', () => {
  assert.deepEqual(xpProgress({ xp: 201, level_xp: 50, next_level_xp: 200 }),
    { value: 150, max: 150, remaining: 0, percent: 100 });
  assert.deepEqual(xpProgress({ xp: 49, level_xp: 50, next_level_xp: 200 }),
    { value: 0, max: 150, remaining: 150, percent: 0 });
  assert.deepEqual(xpProgress({ xp: 50, level_xp: 50, next_level_xp: 50 }),
    { value: 0, max: 1, remaining: 1, percent: 0 });
});
