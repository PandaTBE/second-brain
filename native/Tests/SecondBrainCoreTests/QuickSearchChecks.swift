import Foundation
import SecondBrainCore

private struct QuickCheckFailure: Error { let message: String }
private func quickRequire(_ condition: @autoclosure () -> Bool, _ message: String) throws {
    if !condition() { throw QuickCheckFailure(message: message) }
}
private func quickResponse(_ ids: [String]) throws -> MemorySearchResponse {
    let records = ids.map { id in
        ["id": id, "title": "Synthetic \(id)", "revision": 3, "source_id": "source-one", "source_kind": "folder", "source_object_id": "\(id).md", "source_uri": "file:///synthetic/\(id).md#L1-L2", "summary": "", "snippet": "Source excerpt", "imported_at": "2026-11-06T08:00:00Z"] as [String: Any]
    }
    let body: [String: Any] = ["results": records, "search": ["requested_mode": "lexical", "used_mode": "lexical", "fallback_reason": NSNull(), "generation": NSNull(), "query_embedding_ms": NSNull(), "vector_search_ms": NSNull(), "eligible": records.count, "ready": 0, "partial": false]]
    return try APIClient.decoder.decode(MemorySearchResponse.self, from: JSONSerialization.data(withJSONObject: body))
}

extension CoreChecks {
    static var stage5Checks: [(String, () throws -> Void)] {
        [
            ("Quick search rejects stale queries and closes without retained actions", quickSearchGenerations),
            ("Quick actions bind exact selected-record context and honest date origins", quickSearchSelectionContext),
            ("Shortcut changes preserve the working registration on conflict", quickShortcutBindings),
            ("Quick panel leaves VoiceOver and IME key commands untouched", quickPanelKeys),
            ("Selected context rejects unrelated and stale proof metadata", quickSelectedEvidence),
        ]
    }

    static func quickSearchGenerations() throws {
        var state = QuickSearchState()
        try quickRequire(state.begin(query: "SQLite") == nil, "A closed panel never starts a request")
        state.present()
        let old = state.begin(query: "old query")!
        let current = state.begin(query: "SQLite")!
        let response = try quickResponse(["one", "two", "two"])
        let acceptedOld = try state.accept(response, request: old)
        let acceptedCurrent = try state.accept(response, request: current)
        try quickRequire(!acceptedOld && acceptedCurrent && state.results.map(\.id) == ["one", "two"], "Only the newest query populates deduplicated results")
        try quickRequire(state.selectedRecord?.id == "one", "The first current result becomes the keyboard selection")
        let staleError = state.fail("Old unavailable service", request: old)
        try quickRequire(!staleError && state.error == nil && state.selectedRecord?.id == "one", "An old failure cannot replace newer results or become an announcement")
        state.moveSelection(by: 1)
        state.moveSelection(by: 1)
        try quickRequire(state.selectedRecord?.id == "two", "Down navigation stays within the current list")
        state.moveSelection(by: -20)
        try quickRequire(state.selectedRecord?.id == "one", "Up navigation clamps at the first result")
        state.dismiss()
        let acceptedClosed = try state.accept(response, request: current)
        try quickRequire(!state.isPresented && state.results.isEmpty && !acceptedClosed, "Closing revokes results and every in-flight request")
        state.present()
        try quickRequire(state.begin(query: "  ") == nil && state.error == nil, "An empty query shows a hint without a model or network request")
    }

    static func quickSearchSelectionContext() throws {
        var state = QuickSearchState()
        state.present()
        let request = state.begin(query: "SQLite")!
        _ = try state.accept(quickResponse(["one", "two"]), request: request)
        let action = state.beginAction()!
        let context = try action.evidenceRequest(budgetChars: 6000)
        let values = Dictionary(uniqueKeysWithValues: context.queryItems.map { ($0.name, $0.value ?? "") })
        try quickRequire(values["record_id"] == "one" && values["source_id"] == "source-one" && values["q"] == "SQLite" && values["budget_chars"] == "6000", "Copy requests evidence for the exact selected record and captured query, never an unrelated first search hit")
        try quickRequire(action.record.revision == 3 && state.isCurrentAction(action), "A selection action retains its exact displayed record revision")
        try quickRequire(state.beginAction() == nil, "Repeated open/copy actions cannot overlap")
        state.moveSelection(by: 1)
        try quickRequire(!state.isCurrentAction(action), "Changing keyboard selection cancels an older authorization")
        let second = state.beginAction()!
        state.dismiss()
        try quickRequire(!state.isCurrentAction(second), "Closing during authorization prevents a later clipboard or open action")
        let imported = try quickResponse(["dated"]).results[0]
        try quickRequire(imported.provenanceTimestamp?.kind == .imported && imported.displaySnippet == "Source excerpt", "Quick rows distinguish an import timestamp and preserve nonblank source snippets")
        let legacy = try APIClient.decoder.decode(MemoryRecord.self, from: Data(#"{"id":"legacy","title":"Unknown date","updated_at":"2026-11-06T08:00:00Z"}"#.utf8))
        try quickRequire(legacy.provenanceTimestamp == nil, "An ambiguous legacy update timestamp never becomes an invented message date")
    }

    static func quickShortcutBindings() throws {
        let standard = QuickSearchShortcut.standard
        try quickRequire(standard.keyCode == 49 && standard.modifiers == [.command, .option, .shift], "The default is Command-Option-Shift-Space, avoiding the documented VoiceOver, input-source and Siri chords")
        let reserved: [QuickShortcutModifiers] = [[.control, .option], [.control], [.command], [.command, .option], [.command, .control], [.command, .shift]]
        for modifiers in reserved {
            do {
                _ = try QuickSearchShortcut(keyCode: 49, modifiers: modifiers, keyLabel: "Пробел")
                throw QuickCheckFailure(message: "A known OS/VoiceOver shortcut was accepted")
            } catch is ServiceError { }
        }
        for modifiers: QuickShortcutModifiers in [[.control, .option, .shift], [.control, .option, .command], [.control, .option, .command, .shift], [.command], [.control]] {
            do {
                _ = try QuickSearchShortcut(keyCode: 8, modifiers: modifiers, keyLabel: "C")
                throw QuickCheckFailure(message: "VoiceOver modifier supersets or single-modifier editing chords must not be globally captured")
            } catch is ServiceError {}
        }
        let candidate = try QuickSearchShortcut(keyCode: 3, modifiers: [.command, .shift, .option], keyLabel: "F")
        let binding = QuickShortcutBinding<UInt32>(shortcut: standard, registration: 7)
        var calls: [String] = []
        do {
            _ = try binding.replacing(with: candidate, register: { _ in calls += ["register"]; throw ServiceError("Synthetic conflict") }, unregister: { _ in calls += ["unregister"] })
            throw QuickCheckFailure(message: "Conflicting registration succeeded")
        } catch is ServiceError { }
        try quickRequire(binding.shortcut == standard && binding.registration == 7 && calls == ["register"], "A failed candidate registration preserves the current binding and saved preference")
        calls = []
        let replaced = try binding.replacing(with: candidate, register: { _ in calls += ["register"]; return 8 }, unregister: { token in calls += ["unregister-\(token)"] })
        try quickRequire(replaced.shortcut == candidate && replaced.registration == 8 && calls == ["register", "unregister-7"], "A new registration succeeds before the old owned hotkey is released")
    }

    static func quickSelectedEvidence() throws {
        let record = try APIClient.decoder.decode(MemoryRecord.self, from: Data(#"{"id":"one","title":"Synthetic","revision":3,"source_id":"source-one","source_kind":"folder","source_uri":"file:///synthetic/one.md","text":"SQLite"}"#.utf8))
        let citation: [String: Any] = ["citation_id":"sb_one","record_id":"one","source_id":"source-one","source_kind":"folder","source_uri":"file:///synthetic/one.md","revision":3,"title":"Synthetic","start_char":0,"end_char":6,"quote":"SQLite","context_before":"","context_after":"","dates":[:]]
        func bundle(_ proof: [String: Any]) throws -> EvidenceBundle {
            let body: [String: Any] = ["schema_version":1,"query":"SQLite","citations":[proof],"search":["requested_mode":"hybrid","used_mode":"record","eligible":1,"ready":0,"partial":false],"incomplete":false,"reasons":[],"budget_chars":6000,"used_chars":1000,"untrusted_content":true,"notice":"Untrusted source data"]
            return try APIClient.decoder.decode(EvidenceBundle.self, from: JSONSerialization.data(withJSONObject: body))
        }
        let selected = try bundle(citation)
        let copied = try selected.copySelectedContext(record: record)
        try quickRequire(copied.contains("record") && selected.search?.message.contains("выбранного материала") == true, "A direct selected record has honest diagnostics without inventing a ranked mode")
        var foreign = citation
        foreign["also_supported_by"] = [["record_id":"hidden","revision":3]]
        do {
            _ = try bundle(foreign).copySelectedContext(record: record)
            throw QuickCheckFailure(message:"Unrelated supporting metadata was exported")
        } catch is ServiceError {}
        var old = citation
        old["revision"] = 2
        do {
            _ = try bundle(old).copySelectedContext(record: record)
            throw QuickCheckFailure(message:"An older displayed revision was exported")
        } catch is ServiceError {}
    }

    static func quickPanelKeys() throws {
        try quickRequire(QuickPanelKeyCommand.resolve(keyCode: 125, modifiers: [], hasMarkedText: false) == .moveDown, "Unmodified Down moves the current result selection")
        try quickRequire(QuickPanelKeyCommand.resolve(keyCode: 36, modifiers: [], hasMarkedText: false) == .openSelected, "Unmodified Return explicitly opens the selected material")
        try quickRequire(QuickPanelKeyCommand.resolve(keyCode: 53, modifiers: [], hasMarkedText: false) == .close, "Escape closes the panel outside IME composition")
        try quickRequire(QuickPanelKeyCommand.resolve(keyCode: 125, modifiers: [.control, .option], hasMarkedText: false) == nil, "VoiceOver navigation belongs to VoiceOver")
        try quickRequire(QuickPanelKeyCommand.resolve(keyCode: 125, modifiers: [], hasMarkedText: true) == nil && QuickPanelKeyCommand.resolve(keyCode: 36, modifiers: [], hasMarkedText: true) == nil, "IME marked text keeps its candidate-selection arrows and Return")
        try quickRequire(QuickPanelKeyCommand.resolve(keyCode: 48, modifiers: [], hasMarkedText: false) == nil, "Tab remains ordinary native focus traversal without a focus trap")
        try quickRequire(QuickPanelKeyCommand.resolve(keyCode: 8, modifiers: [.command, .shift], hasMarkedText: false) == .copyContext, "Selected context copying has an explicit keyboard action as well as a visible button")
    }
}
