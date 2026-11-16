import Foundation
import SwiftUI
import AppKit
import SecondBrainCore

private struct StateFailure: Error { let message: String }
private func requireState(_ condition: @autoclosure () -> Bool, _ message: String) throws {
    if !condition() { throw StateFailure(message: message) }
}

private final class RestoreProtocol: URLProtocol {
    static let lock = NSLock()
    static var pending: [String: [RestoreProtocol]] = [:]
    static var automatic = false
    static func reset() { lock.lock(); defer { lock.unlock() }; pending = [:]; automatic = false }
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let path = request.url!.path
        Self.lock.lock()
        if Self.automatic {
            Self.lock.unlock()
            let payload: [String: Any]
            switch path {
            case "/api/backups": payload = [
                "settings": ["enabled": false, "interval_hours": 24, "max_copies": 10, "max_total_bytes": 1_073_741_824],
                "backups": [], "limits": ["max_archive_bytes": 134_217_728]
            ]
            case "/api/sources": payload = ["sources": []]
            case "/api/tasks": payload = taskBoardBody(xp: 0, empty: true)
            case "/api/memory/status": payload = statusBody(records: 0, paused: true)
            case "/api/memory/browse": payload = ["results": [], "has_more": false]
            case "/api/memory/mcp": payload = ["command": "synthetic", "args": [], "claude_json": "", "codex_toml": "", "instructions": ""]
            default: send(["success": false, "error": ["code": "synthetic", "message": "Unexpected test route"]], status: 503); return
            }
            send(["success": true, "data": payload]); return
        }
        Self.pending[path, default: []].append(self)
        Self.lock.unlock()
    }
    override func stopLoading() {}
    func send(_ body: [String: Any], status: Int = 200) {
        let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: try! JSONSerialization.data(withJSONObject: body))
        client?.urlProtocolDidFinishLoading(self)
    }
    static func count(_ path: String) -> Int {
        lock.lock(); defer { lock.unlock() }; return pending[path]?.count ?? 0
    }
    static func allowFreshResponses() {
        lock.lock(); defer { lock.unlock() }; automatic = true
    }
    static func respond(_ path: String, data: [String: Any], failed: Bool = false, index: Int = 0) throws {
        lock.lock()
        guard var requests = pending[path], !requests.isEmpty else { lock.unlock(); throw StateFailure(message: "Missing held response \(path)") }
        let next = requests.remove(at: index); pending[path] = requests; lock.unlock()
        next.send(failed ? ["success": false, "error": ["code": "synthetic", "message": "Synthetic bootstrap failure"]] : ["success": true, "data": data], status: failed ? 503 : 200)
    }
    static func timeout(_ path: String) throws {
        lock.lock()
        guard var requests = pending[path], !requests.isEmpty else { lock.unlock(); throw StateFailure(message: "Missing held timeout \(path)") }
        let next = requests.removeFirst(); pending[path] = requests; lock.unlock()
        next.client?.urlProtocol(next, didFailWithError: URLError(.timedOut))
    }
}

private func statusBody(records: Int = 99, paused: Bool = false) -> [String: Any] {
    ["records": records, "cards_pending": 0, "cards_ready": 0, "cards_error": 0,
     "jobs": [], "scheduler_running": true, "scheduler": ["enabled": true,
        "interval_minutes": 15, "process_cards": !paused, "batch_limit": 10, "auto_import_enabled": !paused, "paused": paused]]
}
private func sourceBody(id: String = "source", kind: String = "folder") -> [String: Any] {
    ["id": id, "name": "Old source", "kind": kind, "enabled": true,
     "credential_saved": false, "status": "ok", "mail_import_enabled": true]
}
private func taskBoardBody(xp: Int = 125, empty: Bool = false, day: String = TaskDay.string(from: Date()), status: String = "todo") -> [String: Any] {
    ["day": day, "tasks": empty ? [] : [
        ["id": "synthetic-task", "title": "Synthetic task", "kind": "daily", "stat": "vitality",
         "difficulty": 2, "status": status, "xp": 25]
    ], "player": ["xp": xp, "level": xp == 0 ? 1 : 2, "rank": "E", "level_xp": xp == 0 ? 0 : 50,
                  "next_level_xp": xp == 0 ? 50 : 200,
                  "stats": ["strength": 0, "agility": 0, "intelligence": 0, "vitality": 0, "perception": 0]]]
}

private let recordBody: [String: Any] = ["id": "same-record", "revision": 1, "source_id": "source",
    "source_kind": "folder", "source_object_id": "note.md", "source_uri": "file:///synthetic/note.md",
    "title": "Old material", "text": "Old source text"]

@main
enum NativeRestoreStateChecks {
    private enum Outcome { case bootstrapFailure, timeout, rejected, success }
    @MainActor static func requireCancelled<Value>(_ task: Task<Value, Error>, _ message: String) async throws {
        do { _ = try await task.value; throw StateFailure(message: message) }
        catch is CancellationError { }
    }
    @MainActor static func waitFor(_ predicate: () -> Bool) async throws {
        for _ in 0..<200 {
            if predicate() { return }
            try await Task.sleep(nanoseconds: 5_000_000)
        }
        throw StateFailure(message: "Timed out waiting for a synthetic request")
    }

    @MainActor static func main() async throws {
        for outcome in [Outcome.bootstrapFailure, .timeout, .rejected, .success] {
            try await checkRestore(outcome)
        }
        try await checkTasksRequests()
        try await checkTaskDayRollover()
        try await checkInspectionLifecycle()
        try checkPassphraseControl()
    }

    @MainActor private static func checkTasksRequests() async throws {
        RestoreProtocol.reset()
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [RestoreProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:50321")!, csrf: "synthetic", session: session)
        let tasks = TasksViewModel(client: client)
        let blank = await tasks.create(title: "  ", kind: .todo, stat: .strength, difficulty: 1)
        let tooLong = await tasks.create(title: String(repeating: "e\u{301}", count: 101), kind: .todo, stat: .strength, difficulty: 1)
        let invalidDifficulty = await tasks.create(title: "Synthetic", kind: .todo, stat: .strength, difficulty: 4)
        await tasks.move("foreign-id", to: .done); await tasks.delete("foreign-id")
        try requireState(!blank && !tooLong && !invalidDifficulty && RestoreProtocol.count("/api/tasks") == 0, "Invalid titles, difficulty and foreign cards never reach the backend")
        let oldLoad = Task { await tasks.load() }
        try await waitFor { RestoreProtocol.count("/api/tasks") == 1 }
        let newLoad = Task { await tasks.load() }
        try await waitFor { RestoreProtocol.count("/api/tasks") == 2 }
        try RestoreProtocol.respond("/api/tasks", data: taskBoardBody(xp: 200), index: 1)
        await newLoad.value
        try RestoreProtocol.respond("/api/tasks", data: taskBoardBody(xp: 25))
        await oldLoad.value
        try requireState(tasks.board?.player.xp == 200 && !tasks.isLoading, "Older GETs cannot overwrite a newer board")

        let move = Task { await tasks.move("synthetic-task", to: .done) }
        try await waitFor { RestoreProtocol.count("/api/tasks/synthetic-task/status") == 1 }
        try RestoreProtocol.respond("/api/tasks/synthetic-task/status", data: taskBoardBody(xp: 250, status: "done"))
        await move.value
        try requireState(tasks.board?.tasks.first?.status == .done && tasks.board?.player.xp == 250 && tasks.action.isEmpty, "Moves replace both tasks and player with the returned board")
        let deletion = Task { await tasks.delete("synthetic-task") }
        try await waitFor { RestoreProtocol.count("/api/tasks/synthetic-task") == 1 }
        try RestoreProtocol.respond("/api/tasks/synthetic-task", data: taskBoardBody(xp: 250, empty: true))
        await deletion.value
        try requireState(tasks.board?.tasks.isEmpty == true && tasks.board?.player.xp == 250, "Deletion renders the server's preserved XP and empty board")

        let create = Task { await tasks.create(title: "Synthetic new", kind: .todo, stat: .strength, difficulty: 1) }
        try await waitFor { RestoreProtocol.count("/api/tasks") == 1 }
        await tasks.load()
        let duplicate = await tasks.create(title: "Duplicate", kind: .todo, stat: .strength, difficulty: 1)
        try requireState(!duplicate && RestoreProtocol.count("/api/tasks") == 1, "Mutations serialize and refresh cannot race their full-board response")
        tasks.invalidateForRestore()
        try requireState(tasks.board == nil && tasks.isRestoring && !tasks.isLoading && tasks.action.isEmpty, "Restore clears cached task and player state immediately")
        await tasks.load()
        let blocked = await tasks.create(title: "Blocked", kind: .todo, stat: .strength, difficulty: 1)
        try requireState(!blocked && RestoreProtocol.count("/api/tasks") == 1, "Restore blocks all task backend access")
        let fresh = Task { await tasks.restoreDidComplete(client) }
        try await waitFor { RestoreProtocol.count("/api/tasks") == 2 }
        try RestoreProtocol.respond("/api/tasks", data: taskBoardBody(xp: 999))
        let accepted = await create.value
        try requireState(!accepted && tasks.board == nil && tasks.isLoading, "An old mutation and its defer cannot change a fresh request")
        try RestoreProtocol.respond("/api/tasks", data: taskBoardBody(xp: 0, empty: true))
        await fresh.value
        try requireState(!tasks.isRestoring && tasks.board?.player.xp == 0 && tasks.board?.tasks.isEmpty == true && !tasks.isLoading, "Only a fresh board is admitted after restore")
        print("PASS: Task request ordering, serialized writes and restore admission preserve current state")
    }

    @MainActor private static func checkTaskDayRollover() async throws {
        RestoreProtocol.reset()
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [RestoreProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:50321")!, csrf: "synthetic", session: session)
        let calendar = Calendar(identifier: .gregorian)
        var now = calendar.startOfDay(for: Date()).addingTimeInterval(3600)
        let tasks = TasksViewModel(client: client, now: { now })
        let oldDay = TaskDay.string(from: now)
        let loading = Task { await tasks.load() }
        try await waitFor { RestoreProtocol.count("/api/tasks") == 1 }
        now = calendar.date(byAdding: .day, value: 1, to: now)!
        let newDay = TaskDay.string(from: now)
        try RestoreProtocol.respond("/api/tasks", data: taskBoardBody(day: oldDay))
        try await waitFor { RestoreProtocol.count("/api/tasks") == 1 }
        try requireState(tasks.board == nil && tasks.isLoading, "A GET crossing midnight never displays the previous daily state")
        try RestoreProtocol.respond("/api/tasks", data: taskBoardBody(day: newDay))
        await loading.value
        try requireState(tasks.board?.day == newDay, "A request crossing midnight reloads with the fresh local day")

        let creating = Task { await tasks.create(title: "After midnight", kind: .daily, stat: .vitality, difficulty: 2) }
        try await waitFor { RestoreProtocol.count("/api/tasks") == 1 }
        now = calendar.date(byAdding: .day, value: 1, to: now)!
        let latestDay = TaskDay.string(from: now)
        await tasks.reloadIfDayChanged()
        try requireState(RestoreProtocol.count("/api/tasks") == 1, "Midnight refresh waits for a pending mutation")
        try RestoreProtocol.respond("/api/tasks", data: taskBoardBody(day: newDay))
        try await waitFor { RestoreProtocol.count("/api/tasks") == 1 }
        try requireState(tasks.board == nil && tasks.isLoading, "A mutation crossing midnight clears the old daily board while reloading")
        try RestoreProtocol.respond("/api/tasks", data: taskBoardBody(xp: 0, empty: true, day: latestDay))
        let created = await creating.value
        try requireState(created && tasks.board?.day == latestDay && !tasks.isLoading && tasks.action.isEmpty, "A successful mutation survives rollover and admits only today's fresh board")
        print("PASS: Task GETs and mutations crossing midnight reload the current local day")
    }

    @MainActor private static func checkPassphraseControl() throws {
        let composed = "synthetic-caf\u{e9}"
        let decomposed = "synthetic-cafe\u{301}"
        var value = BackupPassphraseValue(decomposed)
        let state = Binding(get: { value }, set: { value = $0 })
        var changes = 0
        let adapter = BackupSecureField("Synthetic archive password", value: state, changed: { _, _ in changes += 1 })
        let coordinator = adapter.makeCoordinator()
        let field = coordinator.makeField()
        func edit(_ text: String) {
            field.stringValue = text
            coordinator.controlTextDidChange(Notification(name: NSControl.textDidChangeNotification, object: field))
        }
        edit(composed)
        try requireState(value.text.utf8.elementsEqual(composed.utf8) && changes == 1, "The native secure delegate must replace decomposed with composed bytes")
        try requireState(BackupPassphrase.exportRequest(path: "/synthetic/archive.age", passphrase: BackupPassphraseValue(composed), confirmation: value) != nil, "Replacing a mismatched confirmation must enable a valid action")
        edit(decomposed)
        try requireState(value.text.utf8.elementsEqual(decomposed.utf8) && changes == 2, "The native secure delegate must replace composed with decomposed bytes")
        try requireState(BackupPassphrase.exportRequest(path: "/synthetic/archive.age", passphrase: BackupPassphraseValue(composed), confirmation: value) == nil, "Replacing an exact confirmation must revoke export admission immediately")
        value = BackupPassphraseValue(composed)
        coordinator.synchronize(field, enabled: true)
        try requireState(field.stringValue.utf8.elementsEqual(composed.utf8), "Native view updates must replace canonically equal but byte-distinct text")
        value = BackupPassphraseValue()
        coordinator.synchronize(field, enabled: true)
        try requireState(field.stringValue.isEmpty, "Programmatic clearing must immediately empty the native secure control")
        edit("")
        try requireState(changes == 2, "A programmatic empty echo must not trigger input invalidation")
        coordinator.synchronize(field, enabled: false)
        edit(composed)
        try requireState(!field.isEnabled && value.text.isEmpty && changes == 2, "A disabled secure control must not admit a delayed edit callback")
        try requireState(field.accessibilityLabel() == "Synthetic archive password", "Native secure controls must preserve their accessibility label")
        print("PASS: Native secure adapter preserves exact Unicode transitions, clearing, disabled state and action-time rejection")
    }

    @MainActor private static func checkInspectionLifecycle() async throws {
        RestoreProtocol.reset()
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RestoreProtocol.self]
        let session = URLSession(configuration: config)
        defer { session.invalidateAndCancel() }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:50321")!, csrf: "synthetic", session: session)
        let backups = BackupViewModel(); backups.configure(client)
        let preview: [String: Any] = [
            "inspection_id": "synthetic-ticket", "expires_at": ISO8601DateFormatter().string(from: Date().addingTimeInterval(300)),
            "created_at": "2026-01-01T00:00:00Z", "app_version": "0.1.0", "documents": 1, "sources": 1,
            "foreign_installation": false, "requires_relocation": [], "requires_reauthentication": [], "warnings": []
        ]
        // Replay the observed SecureField lifecycle: button clears first, then an empty binding echo arrives after begin.
        for correct in [false, true] {
            var password = correct ? "synthetic-caf\u{e9}" : "synthetic-wrong"
            let submitted = password; password = ""
            let inspection = Task { await backups.inspect(path: "/synthetic/archive.age", passphrase: submitted) }
            try await waitFor { RestoreProtocol.count("/api/backups/inspect") == 1 }
            if backups.changeInspectionPassword(from: password, to: "") { password = "" }
            try requireState(password.isEmpty, "Inspection must clear its displayed password before awaiting a reply")
            try RestoreProtocol.respond("/api/backups/inspect", data: preview, failed: !correct)
            await inspection.value
            if correct {
                try requireState(backups.inspection.restoreRequest() != nil && backups.error == nil, "The empty binding echo must not discard a successful encrypted preview")
                _ = backups.changeInspectionPassword(from: "", to: "")
                try requireState(backups.inspection.restoreRequest() != nil, "A repeated unchanged empty callback must preserve the reviewed preview")
            } else {
                try requireState(backups.error != nil && backups.inspection.inspection == nil, "The empty binding echo must not swallow a wrong-password error")
            }
        }
        try requireState(backups.changeInspectionPassword(from: "", to: "synthetic-new"), "A real password change must be reported")
        try requireState(backups.inspection.restoreRequest() == nil, "A real password edit must revoke the previous restore preview")
        let pending = Task { await backups.inspect(path: "/synthetic/archive.age", passphrase: "synthetic-input") }
        try await waitFor { RestoreProtocol.count("/api/backups/inspect") == 1 }
        try requireState(backups.changeInspectionPassword(from: "synthetic-caf\u{e9}", to: "synthetic-cafe\u{301}"), "Canonically equal but byte-distinct password edits must invalidate")
        try RestoreProtocol.respond("/api/backups/inspect", data: preview)
        await pending.value
        try requireState(backups.inspection.restoreRequest() == nil, "A reply for a genuinely edited password must remain stale")
        print("PASS: Actual inspection preserves password-clear echoes, visible failures and real-edit invalidation")
    }

    @MainActor private static func checkRestore(_ outcome: Outcome) async throws {
        RestoreProtocol.reset()
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [RestoreProtocol.self]
        let session = URLSession(configuration: config)
        defer { session.invalidateAndCancel() }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:50321")!, csrf: "synthetic", session: session)
        let store = AppStore(client: client)
        let taskLoad = Task { await store.tasks.load() }
        try await waitFor { RestoreProtocol.count("/api/tasks") == 1 }
        try RestoreProtocol.respond("/api/tasks", data: taskBoardBody())
        await taskLoad.value
        let oldTaskMove = Task { await store.tasks.move("synthetic-task", to: .done) }
        try await waitFor { RestoreProtocol.count("/api/tasks/synthetic-task/status") == 1 }
        let preview: BackupInspection = try APIClient.decoder.decode(BackupInspection.self, from: JSONSerialization.data(withJSONObject: [
            "inspection_id": "synthetic-ticket", "expires_at": ISO8601DateFormatter().string(from: Date().addingTimeInterval(300)),
            "created_at": "2026-01-01T00:00:00Z", "app_version": "0.1.0", "documents": 1, "sources": 1,
            "foreign_installation": false, "requires_relocation": [], "requires_reauthentication": [], "warnings": []
        ]))
        let inspectionRequest = store.backups.inspection.begin(path: "/synthetic/archive.zip")
        try requireState(store.backups.inspection.accept(preview, request: inspectionRequest), "Synthetic restore preview must be current")
        let model = ModelSettings(provider: "ollama", baseUrl: "http://127.0.0.1:11434", modelId: "old-model", modelName: "Old model")
        store.draft = model; store.savedSettings = model
        let source = try APIClient.decoder.decode(MemorySource.self, from: JSONSerialization.data(withJSONObject: sourceBody()))
        let mail = try APIClient.decoder.decode(MemorySource.self, from: JSONSerialization.data(withJSONObject: sourceBody(id: "mail", kind: "imap")))
        store.sources = [source, mail]
        store.memory.status = try APIClient.decoder.decode(MemoryStatus.self, from: JSONSerialization.data(withJSONObject: statusBody()))
        store.memory.record = try APIClient.decoder.decode(MemoryRecord.self, from: JSONSerialization.data(withJSONObject: recordBody))
        store.memory.selectedId = "same-record"
        store.memory.scheduleDraft = MemoryScheduleRequest(enabled: true, processCards: true)
        store.memory.scheduleEdited = true
        store.memory.query = "Old private search"
        store.quickSearch.model.present()
        try requireState(store.quickSearch.model.state.isPresented, "Synthetic panel must be admitted before restore")

        let save = Task { await store.save() }
        let check = Task { await store.checkSource(source) }
        let mailUpdate = Task { await store.setMailImport(mail, enabled: false) }
        let add = Task { await store.addSource(AddSourceRequest(kind: "folder", name: "Old addition", path: "/synthetic")) }
        let browse = Task { await store.memory.search() }
        let detail = Task { await store.memory.selectRecord("same-record") }
        let status = Task { await store.memory.refreshStatus() }
        let schedule = Task { await store.memory.applySchedule() }
        let oldFact = Task { try await store.memory.currentFactRecord("same-record") }
        let oldHistory = Task { try await store.memory.factHistory("same-fact") }
        let oldFacts = Task { try await store.memory.factPage(MemoryFactsRequest()) }
        let oldEvidence = Task { try await store.memory.evidenceBundle(EvidenceRequest(query: "Synthetic question")) }
        let displayed = store.memory.record!
        let oldOpen = Task { try await store.memory.originalTarget(displayed) }
        try await waitFor {
            RestoreProtocol.count("/api/settings") == 1 && RestoreProtocol.count("/api/sources/source/test") == 1
                && RestoreProtocol.count("/api/sources/mail/mail-import") == 1 && RestoreProtocol.count("/api/sources") == 1
                && RestoreProtocol.count("/api/memory/browse") == 1 && RestoreProtocol.count("/api/memory/records/same-record") == 2
                && RestoreProtocol.count("/api/memory/status") == 1 && RestoreProtocol.count("/api/memory/schedule") == 1
                && RestoreProtocol.count("/api/memory/facts/same-fact/history") == 1 && RestoreProtocol.count("/api/memory/facts") == 1
                && RestoreProtocol.count("/api/memory/evidence") == 1 && RestoreProtocol.count("/api/memory/records/same-record/open-target") == 1
        }

        let restore = Task { await store.backups.restore() }
        try await waitFor { RestoreProtocol.count("/api/backups/restore") == 1 }
        try requireState(store.draft.modelId.isEmpty && store.savedSettings.modelId.isEmpty, "Restore must clear model drafts before its first await")
        try requireState(store.memory.record == nil && store.memory.status == nil && store.memory.results.isEmpty, "Restore must synchronously revoke cached and pending memory state")
        try requireState(store.tasks.isRestoring && store.tasks.board == nil, "AppStore restore revokes the task board and player synchronously")
        await store.tasks.load()
        try requireState(RestoreProtocol.count("/api/tasks") == 0, "AppStore keeps task reads blocked during restore")
        try requireState(!store.memory.scheduleEdited && !store.memory.scheduleDraft.processCards, "Old paid schedule must be revoked synchronously")
        try requireState(store.memory.query.isEmpty, "Restore must clear the previous snapshot's search query")
        store.quickSearch.model.present()
        try requireState(!store.quickSearch.model.state.isPresented, "Quick panel admission must remain closed while the restore POST is pending")

        // New action sentinels share the same model instance and source IDs as old requests.
        store.action = "new-model-action"; store.busySources = [source.id, mail.id]
        store.memory.action = "new-memory-action"
        try RestoreProtocol.respond("/api/tasks/synthetic-task/status", data: taskBoardBody(xp: 999))
        await oldTaskMove.value
        try requireState(store.tasks.board == nil && store.tasks.isRestoring, "Old task mutations cannot cross the AppStore restore epoch")
        let encodedModel = try JSONSerialization.jsonObject(with: APIClient.encoder.encode(model)) as! [String: Any]
        try RestoreProtocol.respond("/api/settings", data: ["settings": encodedModel, "token_saved": true])
        try RestoreProtocol.respond("/api/sources/source/test", data: ["source": sourceBody()])
        try RestoreProtocol.respond("/api/sources/mail/mail-import", data: ["source": sourceBody(id: "mail", kind: "imap")])
        try RestoreProtocol.respond("/api/sources", data: ["source": sourceBody(id: "late-add")])
        try RestoreProtocol.respond("/api/memory/browse", data: ["results": [recordBody], "has_more": false])
        try RestoreProtocol.respond("/api/memory/records/same-record", data: ["record": recordBody])
        try RestoreProtocol.respond("/api/memory/records/same-record", data: ["record": recordBody])
        try RestoreProtocol.respond("/api/memory/status", data: statusBody())
        try RestoreProtocol.respond("/api/memory/schedule", data: ["scheduler": statusBody()["scheduler"]!])
        try RestoreProtocol.respond("/api/memory/facts/same-fact/history", data: [
            "schema_version": 1, "fact": ["id": "same-fact", "kind": "fact", "text": "Old fact"],
            "events": [], "history_since": "2026-01-01T00:00:00Z", "untrusted_content": true, "truncated": false
        ])
        try RestoreProtocol.respond("/api/memory/facts", data: [
            "schema_version": 1, "query": "", "facts": [], "history_since": "2026-01-01T00:00:00Z",
            "untrusted_content": true, "truncated": false
        ])
        try RestoreProtocol.respond("/api/memory/evidence", data: [
            "schema_version": 1, "query": "Synthetic question", "citations": [], "incomplete": false,
            "reasons": [], "budget_chars": 12000, "used_chars": 0, "untrusted_content": true, "notice": "Synthetic"
        ])
        try RestoreProtocol.respond("/api/memory/records/same-record/open-target", data: [
            "available": true, "uri": recordBody["source_uri"]!, "reference": recordBody
        ])
        await save.value; await check.value; await mailUpdate.value; _ = await add.value
        await browse.value; await detail.value; await status.value; await schedule.value
        try await requireCancelled(oldFact, "Same-ID/revision fact callback survived restore")
        try await requireCancelled(oldHistory, "Historical callback survived restore")
        try await requireCancelled(oldFacts, "Facts callback survived restore")
        try await requireCancelled(oldEvidence, "Evidence callback survived restore")
        try await requireCancelled(oldOpen, "Same-ID/revision/URI opening survived restore")
        try requireState(store.savedSettings.modelId.isEmpty && store.sources.isEmpty, "Late pre-restore model/source responses must not repopulate restored UI")
        try requireState(store.action == "new-model-action" && store.busySources == [source.id, mail.id], "Old mutation defers must not erase new actions")
        try requireState(store.memory.action == "new-memory-action" && store.memory.record == nil && store.memory.status == nil && store.memory.results.isEmpty, "Old memory responses and defers must remain revoked")
        if outcome == .timeout || outcome == .rejected {
            if outcome == .timeout { try RestoreProtocol.timeout("/api/backups/restore") }
            else { try RestoreProtocol.respond("/api/backups/restore", data: [:], failed: true) }
            await restore.value
            try requireState(store.isRestoring && store.memory.isRestoring && store.tasks.isRestoring && store.tasks.board == nil && store.memory.record == nil, "A failed or ambiguous POST must never readmit cached memory")
            try requireState(RestoreProtocol.count("/api/bootstrap") == 0, "An unknown restore result must not be treated as a successful completion")
            try requireState(store.backups.inspection.restoreRequest() == nil && store.backups.error != nil, "A failed restore must revoke the old inspection and explain recovery")
            store.quickSearch.model.present()
            try requireState(!store.quickSearch.model.state.isPresented, "Quick panel must stay closed after a failed restore")
            print("PASS: Restore \(outcome) stays fail-closed without cached actions")
            return
        }
        try RestoreProtocol.respond("/api/backups/restore", data: [
            "restored": true, "documents": 1, "sources": 1, "foreign_installation": false,
            "paused": true, "restart_recommended": false
        ])
        try await waitFor { RestoreProtocol.count("/api/bootstrap") == 1 }
        RestoreProtocol.allowFreshResponses()
        let resetModel = try JSONSerialization.jsonObject(with: APIClient.encoder.encode(ModelSettings())) as! [String: Any]
        try RestoreProtocol.respond("/api/bootstrap", data: [
            "csrf_token": "synthetic", "providers": [], "settings": resetModel, "token_saved": false,
            "storage": ["available": true, "label": "Synthetic storage"]
        ], failed: outcome == .bootstrapFailure)
        await restore.value
        try requireState(store.draft.modelId.isEmpty && store.savedSettings.modelId.isEmpty && !store.tokenSaved, "Bootstrap failure must retain cleared model state")
        if outcome == .bootstrapFailure {
            try requireState(store.isRestoring && store.memory.isRestoring && store.tasks.isRestoring && store.tasks.board == nil, "Bootstrap failure must keep uncertain memory admission fail closed")
            print("PASS: Failed restore bootstrap keeps model and paid schedule state revoked")
        } else {
            try requireState(!store.isRestoring && !store.memory.isRestoring && store.memory.status?.records == 0 && store.memory.synchronizationPaused, "Successful restore must readmit only fresh paused server state")
            try requireState(!store.tasks.isRestoring && store.tasks.board?.player.xp == 0 && store.tasks.board?.tasks.isEmpty == true, "AppStore reloads fresh restored tasks using its configured client")
            try requireState(store.backups.error == nil, "Successful resynchronization must finish without a hidden decoding failure")
            print("PASS: Actual BackupViewModel restore admission rejects old responses and readmits only fresh server state")
        }
    }
}
