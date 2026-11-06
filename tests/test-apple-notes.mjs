import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const require = createRequire(import.meta.url);
const { executeNotesRequest } = require('../brain_settings/resources/apple_notes.js');

const date = new Date('2026-11-01T10:00:00Z');
function note(id, folderId, text = 'Synthetic note') {
  return { passwordProtected: () => false, id: () => id, name: () => 'Demo',
    container: () => ({ id: () => folderId }), plaintext: () => text,
    creationDate: () => date, modificationDate: () => date };
}
function folder(id, members, children = []) {
  return { id: () => id, name: () => id, notes: () => members, folders: () => children };
}
function app(folders) { return { accounts: () => [{ name: () => 'Demo', folders: () => folders }] }; }
function read(application, folderIds, cursor = null, limit = 50) {
  return executeNotesRequest({ operation: 'notes', folder_ids: folderIds, cursor, limit }, application, () => 1);
}

test('Locked notes never expose title, identifier, plaintext, or attachment properties', () => {
  const deny = () => { throw new Error('must never read protected contents'); };
  const locked = { passwordProtected: () => true, id: deny, name: deny, plaintext: deny, attachments: deny };
  const result = read(app([folder('selected', [locked])]), ['selected']);
  assert.equal(result.skipped, 1);
  assert.deepEqual(result.notes, []);
  assert.equal(result.next_cursor, null);
});

test('Import never enumerates contents of unselected folders and rejects child-folder spill', () => {
  const outside = { ...folder('outside', []), notes: () => { throw new Error('unselected notes read'); } };
  const spill = { ...note('spill', 'child'), plaintext: () => { throw new Error('child text read'); } };
  const selected = folder('selected', [note('one', 'selected'), spill], [folder('child', [])]);
  const result = read(app([selected, outside]), ['selected']);
  assert.deepEqual(result.notes.map(item => item.id), ['one']);
  assert.equal(result.skipped, 1);
});

test('Aggregate body budget yields a continuation before consuming the next note', () => {
  const text = 'x'.repeat(900 * 1024);
  const application = app([folder('selected', Array.from({ length: 8 }, (_, i) => note(String(i), 'selected', text)))]);
  const first = read(application, ['selected']);
  assert.equal(first.notes.length, 4);
  assert.deepEqual(first.next_cursor, { folder_index: 0, offset: 4 });
  assert.equal(first.skipped, 0);
  assert.ok(Buffer.byteLength(JSON.stringify(first)) < 8 * 1024 * 1024);
  const second = read(application, ['selected'], first.next_cursor);
  assert.deepEqual(second.notes.map(item => item.id), ['4', '5', '6', '7']);
  assert.equal(second.next_cursor, null);
});

test('Escaped plaintext is budgeted as serialized JSON and resumes without losing notes', () => {
  for (const character of ['\n', '\u0001', '"', '\\']) {
    const members = Array.from({ length: 4 }, (_, i) => note(String(i), 'selected', character.repeat(1024 * 1024)));
    const application = app([folder('selected', members)]);
    const ids = [];
    let cursor = null;
    do {
      const page = read(application, ['selected'], cursor);
      assert.ok(Buffer.byteLength(JSON.stringify(page)) < 8 * 1024 * 1024);
      assert.equal(page.skipped, 0);
      assert.ok(page.notes.length > 0);
      ids.push(...page.notes.map(item => item.id));
      cursor = page.next_cursor;
    } while (cursor !== null);
    assert.deepEqual(ids, ['0', '1', '2', '3']);
  }
});

test('Folder discovery reads metadata only and does not reject an unselected large folder', () => {
  const onlyMetadata = folder('large', new Array(20_000));
  const result = executeNotesRequest({ operation: 'folders' }, app([onlyMetadata]), () => 1);
  assert.equal(result.folders[0].note_count, 20_000);
});

test('A missing selected folder, oversized note, and traversal deadline are handled explicitly', () => {
  assert.throws(() => read(app([]), ['missing']), error => error.bridgeCode === 'scope_changed');
  const result = read(app([folder('selected', [note('big', 'selected', 'x'.repeat(1024 * 1024 + 1))])]), ['selected']);
  assert.equal(result.notes.length, 0);
  assert.equal(result.skipped, 1);
  let now = 0;
  assert.throws(() => executeNotesRequest({ operation: 'folders' }, app([folder('one', [])]), () => (now += 30_000)),
    error => error.bridgeCode === 'limit');
});
