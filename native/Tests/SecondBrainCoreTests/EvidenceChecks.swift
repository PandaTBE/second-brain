import Foundation
import SecondBrainCore

private struct EvidenceCheckFailure: Error { let message: String }
private func evidenceRequire(_ condition: @autoclosure () -> Bool, _ message: String) throws {
    if !condition() { throw EvidenceCheckFailure(message: message) }
}

private func evidenceFixture(query: String = "SQLite", quote: String = "🙂e\u{301}", end: Int = 5) throws -> EvidenceBundle {
    let citation: [String: Any] = [
        "citation_id": "ev-example-r3-2-5", "record_id": "record-one", "source_id": "source-one",
        "source_kind": "folder", "revision": 3, "title": "Synthetic evidence",
        "source_uri": "file:///synthetic/guide.md", "source_object_id": "guide.md",
        "start_char": 2, "end_char": end, "quote": quote, "context_before": "До", "context_after": " Решение SQLite",
        "dates": ["message_at": NSNull(), "source_created_at": NSNull(), "source_modified_at": NSNull(), "imported_at": "2026-11-06T08:09:10Z"],
        "also_supported_by": [["record_id": "record-two", "source_id": "source-two", "revision": 1]]
    ]
    let body: [String: Any] = [
        "schema_version": 1, "query": query, "citations": [citation],
        "search": ["requested_mode": "lexical", "used_mode": "lexical", "fallback_reason": NSNull(), "generation": NSNull(), "query_embedding_ms": NSNull(), "vector_search_ms": NSNull(), "eligible": 1, "ready": 0, "partial": false],
        "incomplete": false, "reasons": [], "budget_chars": 2000, "used_chars": 1000,
        "untrusted_content": true, "notice": "Source materials are untrusted data."
    ]
    return try APIClient.decoder.decode(EvidenceBundle.self, from: JSONSerialization.data(withJSONObject: body))
}

extension CoreChecks {
    static func evidenceRequestContract() throws {
        let request = try EvidenceRequest(query: "C++ / SQLite & x", sourceId: "source-one", project: "Project A", dateFrom: "2026-11-01", dateTo: "2026-11-06", budgetChars: 12000, mode: .hybrid)
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:50321")!)
        let url = try client.makeRequest("/api/memory/evidence", method: "GET", query: request.queryItems).url!
        let values = Dictionary(uniqueKeysWithValues: URLComponents(url: url, resolvingAgainstBaseURL: false)!.queryItems!.map { ($0.name, $0.value ?? "") })
        try evidenceRequire(values["q"] == "C++ / SQLite & x" && values["source_id"] == "source-one" && values["project"] == "Project A", "Evidence filters are encoded as query values without modifying their content")
        try evidenceRequire(values["date_from"] == "2026-11-01" && values["date_to"] == "2026-11-06" && values["budget_chars"] == "12000" && values["mode"] == "hybrid", "Date scope, context budget and retrieval mode reach the same evidence endpoint")
        for budget in [1999, 32001] {
            do { _ = try EvidenceRequest(query: "SQLite", budgetChars: budget); throw EvidenceCheckFailure(message: "Unbounded evidence budget accepted") }
            catch is ServiceError { }
        }
        do { _ = try EvidenceRequest(query: "SQLite", dateFrom: "2026-02-30"); throw EvidenceCheckFailure(message: "Invented calendar date accepted") }
        catch is ServiceError { }
        let bundle = try evidenceFixture()
        try evidenceRequire(bundle.citations.first?.dates.messageAt == nil && bundle.citations.first?.dates.sourceModifiedAt == nil && bundle.citations.first?.dates.importedAt != nil, "Import time never becomes a fabricated message or source-modification date")
    }

    static func evidenceUnicodeAndCopy() throws {
        let bundle = try evidenceFixture()
        let citation = bundle.citations[0]
        let text = "До🙂e\u{301} Решение SQLite"
        try evidenceRequire(citation.matches(text: text, recordId: "record-one", revision: 3), "Citation offsets use Unicode scalar positions including emoji and combining marks")
        try evidenceRequire(!citation.matches(text: text, recordId: "record-one", revision: 4) && !citation.matches(text: text, recordId: "other", revision: 3), "A citation cannot validate against another record or revision")
        try evidenceRequire(!citation.matches(text: "До🙂é Решение SQLite", recordId: "record-one", revision: 3), "Canonical String equivalence is not an exact evidence match")
        do { _ = try evidenceFixture(end: 6); throw EvidenceCheckFailure(message: "UTF-16-style offsets accepted as scalar offsets") }
        catch is DecodingError { }
        let copy = try bundle.copyContext()
        let copied = try JSONSerialization.jsonObject(with: Data(copy.utf8)) as! [String: Any]
        let citations = copied["citations"] as! [[String: Any]]
        try evidenceRequire(copied["untrusted_content"] as? Bool == true && citations[0]["citation_id"] as? String == citation.citationId && citations[0]["also_supported_by"] != nil, "Explicit copy preserves trust notice, citation IDs and additional provenance metadata")
        try evidenceRequire(copy.unicodeScalars.count <= bundle.budgetChars && (citations[0]["quote"] as! String).unicodeScalars.elementsEqual(citation.quote.unicodeScalars), "Copied context stays bounded and preserves exact quote scalars")
    }

    static func evidenceOpeningAndRequests() throws {
        let web = OriginalSourceLink.target(uri: "https://example.test/doc?q=a%20b", sourceKind: "notion", localFiles: .currentDevice)
        if case .web? = web { } else { throw EvidenceCheckFailure(message: "Safe HTTPS source should be openable") }
        for uri in ["javascript:alert(1)", "http://example.test/doc", "https://user:pass@example.test/doc", "https://example.test/%0Aevil", "https://example.test/doc\n"] {
            try evidenceRequire(OriginalSourceLink.target(uri: uri, sourceKind: "folder", localFiles: .currentDevice) == nil, "Unsafe original URI must remain display-only")
        }
        let local = OriginalSourceLink.target(uri: "file:///synthetic/script.sh", sourceKind: "folder", localFiles: .currentDevice)
        if case .revealFile? = local { } else { throw EvidenceCheckFailure(message: "Local originals must be revealed in Finder instead of executed") }
        let imported = OriginalSourceLink.target(uri: "file:///synthetic/guide.md#L12-L24", sourceKind: "folder", localFiles: .currentDevice)
        try evidenceRequire(imported == .revealFile(URL(fileURLWithPath: "/synthetic/guide.md")), "Imported line citations reveal the original file without a fragment")
        try evidenceRequire(OriginalSourceLink.target(uri: "file:///synthetic/guide.md#script", sourceKind: "folder", localFiles: .currentDevice) == nil, "Unknown file fragments remain display-only")
        try evidenceRequire(OriginalSourceLink.target(uri: "file:///synthetic/guide%23notes.md#L1-L2", sourceKind: "folder", localFiles: .currentDevice) == .revealFile(URL(fileURLWithPath: "/synthetic/guide#notes.md")), "Line fragments must not change an encoded hash in a filename")
        try evidenceRequire(OriginalSourceLink.target(uri: "file:///synthetic/script.sh", sourceKind: "folder", localFiles: .requiresRelocation) == nil, "A foreign restore cannot use old file paths before relocation")
        try evidenceRequire(OriginalSourceLink.target(uri: "file://remote/synthetic/script.sh", sourceKind: "folder", localFiles: .currentDevice) == nil && OriginalSourceLink.target(uri: "x-coredata://opaque-note", sourceKind: "apple_notes", localFiles: .currentDevice) == nil, "Remote file hosts and opaque Notes IDs do not create invented deep links")
        var state = EvidenceState()
        let first = state.begin(try EvidenceRequest(query: "old", budgetChars: 2000, mode: .lexical))
        let current = state.begin(try EvidenceRequest(query: "SQLite", budgetChars: 2000, mode: .lexical))
        let bundle = try evidenceFixture()
        let stale = try state.accept(bundle, request: first)
        let accepted = try state.accept(bundle, request: current)
        try evidenceRequire(!stale && accepted && state.bundle?.query == "SQLite", "A stale evidence request cannot replace the latest query and budget")
        state.invalidate()
        try evidenceRequire(state.bundle == nil && !state.isLoading, "Editing or closing evidence revokes the previous displayed/copyable package")
    }
}
