/* Read-only JXA bridge. JSON on stdin contains data only; never executable source. */
var MAX_FOLDERS = 1000;
var MAX_NOTES = 10000;
var MAX_BODY_BYTES = 1048576;
var MAX_PAGE_BODY_BYTES = 4194304;
// Reserve space for the response envelope, cursors, warnings, commas and osascript's newline.
var MAX_PAGE_JSON_BYTES = 8388608 - 65536;

function fail(code) { var error = new Error(code); error.bridgeCode = code; throw error; }

function utf8Bytes(text) { return unescape(encodeURIComponent(text)).length; }

function cursorFor(folderIndex, offset) { return { folder_index: folderIndex, offset: offset }; }

function executeNotesRequest(request, notes, now) {
    var deadline = now() + 20000;
    var folders = [], byId = Object.create(null), seen = Object.create(null), visited = 0;
    if (!request || (request.operation !== 'folders' && request.operation !== 'notes')) fail('scope_changed');
    function check() { if (now() > deadline) fail('limit'); }
    function visit(folder, accountName, parents, depth) {
        check();
        if (depth > 32) fail('limit');
        var id = String(folder.id());
        if (seen[id]) return;
        visited += 1;
        if (visited > MAX_FOLDERS) fail('limit');
        seen[id] = true;
        var path = parents;
        if (request.operation === 'folders') {
            var name = String(folder.name());
            path = parents.concat([name]);
            var count = folder.notes().length;
            if (count > 1000000) fail('limit');
            folders.push({ id: id, name: name, account: accountName,
                           path: [accountName].concat(path).join(' / '), note_count: count });
        }
        byId[id] = folder;
        var children = folder.folders();
        if (children.length > MAX_FOLDERS) fail('limit');
        children.forEach(function (child) { visit(child, accountName, path, depth + 1); });
    }
    var accounts = notes.accounts();
    if (accounts.length > 100) fail('limit');
    accounts.forEach(function (account) {
        check();
        var accountName = request.operation === 'folders' ? String(account.name()) : '';
        var roots = account.folders();
        if (roots.length > MAX_FOLDERS) fail('limit');
        roots.forEach(function (folder) { visit(folder, accountName, [], 0); });
    });
    if (request.operation === 'folders') return { folders: folders };
    if (request.operation !== 'notes' || !Array.isArray(request.folder_ids) ||
        request.folder_ids.length < 1 || request.folder_ids.length > 50 ||
        !Number.isInteger(request.limit) || request.limit < 1 || request.limit > 50) fail('scope_changed');
    var selected = request.folder_ids;
    selected.forEach(function (id) { if (!Object.prototype.hasOwnProperty.call(byId, id)) fail('scope_changed'); });
    var current = request.cursor || cursorFor(0, 0);
    if (!Number.isInteger(current.folder_index) || !Number.isInteger(current.offset) ||
        current.folder_index < 0 || current.folder_index >= selected.length ||
        current.offset < 0 || current.offset > MAX_NOTES) fail('scope_changed');
    var folderIndex = current.folder_index, offset = current.offset;
    var results = [], warnings = [], skipped = 0, inspected = 0;
    var pageBytes = 0, bodyBytes = 0, pageFull = false;
    function warning(text) { if (warnings.indexOf(text) < 0) warnings.push(text); }
    while (folderIndex < selected.length && inspected < request.limit && !pageFull) {
        check();
        var selectedId = selected[folderIndex];
        var members = byId[selectedId].notes();
        if (members.length > MAX_NOTES) fail('limit');
        while (offset < members.length && inspected < request.limit) {
            check();
            var note = members[offset];
            offset += 1;
            inspected += 1;
            try {
                // Never request title, plaintext, attachment or other private contents of a locked note.
                if (note.passwordProtected() !== false) {
                    skipped += 1;
                    warning('Защищённые заметки пропущены.');
                    continue;
                }
                if (String(note.container().id()) !== selectedId) {
                    skipped += 1;
                    warning('Заметки вне выбранной папки пропущены.');
                    continue;
                }
                var text = note.plaintext();
                if (typeof text !== 'string') throw new Error('invalid_plaintext');
                var textBytes = utf8Bytes(text);
                if (textBytes > MAX_BODY_BYTES) {
                    skipped += 1;
                    warning('Заметки больше 1 МиБ пропущены; прежние материалы сохранены.');
                    continue;
                }
                var noteId = note.id(), title = note.name();
                if (typeof noteId !== 'string' || typeof title !== 'string' ||
                    noteId.length > 512 || utf8Bytes(title) > 2048) throw new Error('invalid_note');
                var item = { id: noteId, folder_id: selectedId,
                    title: title, text: text,
                    created_at: note.creationDate().toISOString(),
                    updated_at: note.modificationDate().toISOString() };
                var encodedBytes = utf8Bytes(JSON.stringify(item)) + 1;
                if (encodedBytes > MAX_PAGE_JSON_BYTES) throw new Error('invalid_note');
                if ((pageBytes + encodedBytes > MAX_PAGE_JSON_BYTES ||
                    bodyBytes + textBytes > MAX_PAGE_BODY_BYTES) && results.length > 0) {
                    offset -= 1;
                    inspected -= 1;
                    pageFull = true;
                    break;
                }
                pageBytes += encodedBytes;
                bodyBytes += textBytes;
                results.push(item);
            } catch (error) {
                if (error.errorNumber === -1743 || error.number === -1743) fail('permission_denied');
                skipped += 1;
                warning('Часть заметок недоступна; прежние материалы сохранены.');
            }
        }
        if (offset >= members.length) { folderIndex += 1; offset = 0; }
    }
    return { notes: results, next_cursor: folderIndex < selected.length ? cursorFor(folderIndex, offset) : null,
             warnings: warnings, skipped: skipped };
}

function run() {
    try {
        ObjC.import('Foundation');
        ObjC.import('AppKit');
        var active = $.NSRunningApplication.runningApplicationsWithBundleIdentifier('com.apple.Notes');
        if (Number(active.count) === 0) return JSON.stringify({ error: 'not_running' });
        var input = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
        var text = ObjC.unwrap($.NSString.alloc.initWithDataEncoding(input, $.NSUTF8StringEncoding));
        if (utf8Bytes(text) > 32768) fail('limit');
        var request = JSON.parse(text);
        return JSON.stringify(executeNotesRequest(request, Application('Notes'), Date.now));
    } catch (error) {
        var code = error.bridgeCode || ((error.errorNumber === -1743 || error.number === -1743) ?
            'permission_denied' : 'unavailable');
        return JSON.stringify({ error: code });
    }
}

if (typeof module !== 'undefined' && module.exports) module.exports = { executeNotesRequest: executeNotesRequest };
