import Foundation
import SecondBrainCore

private struct FactCheckFailure: Error { let message: String }
private func factRequire(_ condition: @autoclosure () -> Bool, _ message: String) throws {
    if !condition() { throw FactCheckFailure(message: message) }
}
private func factProof(_ source: String = "source-one", active: Bool = true) -> [String: Any] {
    ["citation_id": "sb_\(source)", "record_id": "record-one", "revision": 1, "active": active, "support_status": "source_supported",
     "source_id": source, "source_kind": "folder", "source_object_id": "guide.md",
     "title": "Synthetic decision", "source_uri": "file:///synthetic/guide.md#L1-L2",
     "start": 0, "end": 11, "quote": "Use SQLite.",
     "dates": ["message_at": NSNull(), "source_created_at": NSNull(), "source_modified_at": NSNull(), "imported_at": "2026-11-06T08:00:00Z"]]
}
private func factPage(query: String = "", empty: Bool = false, includeHistory: Bool = false, historicalOnly: Bool = false) throws -> MemoryFactsResponse {
    let fact: [String: Any] = [
        "id": "fact_one", "kind": "decision", "text": "Use SQLite.", "status": historicalOnly ? "candidate" : "source_supported", "historical_only": historicalOnly,
        "evidence": [factProof(active: !historicalOnly), factProof("source-two", active: !historicalOnly), factProof("source-three", active: !historicalOnly)],
        "relations": [["kind": "possible_conflict", "target_id": "fact_two", "target_text": "Use PostgreSQL.", "suggestion": true, "evidence": factProof()]]
    ]
    let body: [String: Any] = ["schema_version": 1, "query": query, "include_history": includeHistory, "facts": empty ? [] : [fact], "history_since": "2026-11-06T07:00:00Z", "untrusted_content": true, "truncated": false]
    return try APIClient.decoder.decode(MemoryFactsResponse.self, from: JSONSerialization.data(withJSONObject: body))
}
private func historyPage(event: String, next: String? = nil, active: Bool = false) throws -> MemoryFactHistoryResponse {
    let item = factProof().merging(["event_id": event, "observed_at": "2026-11-06T08:05:00Z", "active": active, "status": "source_supported", "model": "synthetic-card-model", "prompt_version": "3", "relations": [["kind": "explicit_supersedes", "target_id": "fact_previous", "target_text": "Use an older design.", "suggestion": false, "historical": true, "evidence": factProof(active: false)]]]) { _, value in value }
    let body: [String: Any] = ["schema_version": 1, "fact": ["id": "fact_one", "kind": "decision", "text": "Use SQLite."], "events": [item], "history_since": "2026-11-06T07:00:00Z", "untrusted_content": true, "truncated": next != nil, "next_cursor": next as Any? ?? NSNull()]
    return try APIClient.decoder.decode(MemoryFactHistoryResponse.self, from: JSONSerialization.data(withJSONObject: body))
}

private final class FactsProjectionJobProtocol: URLProtocol {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        guard request.url?.host == "127.0.0.1", request.url?.path == "/api/memory/jobs/synthetic_job", let url = request.url else {
            client?.urlProtocol(self, didFailWithError: ServiceError("Unexpected synthetic job request")); return
        }
        let response = HTTPURLResponse(url: url, statusCode: 200, httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!
        let data = Data(#"{"success":true,"data":{"job":{"id":"synthetic_job","kind":"facts","status":"completed","processed":3,"total":3,"stats":{"processed":3,"projected":2}}}}"#.utf8)
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: data)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

extension CoreChecks {
    static func memoryFactJobEnvelope() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [FactsProjectionJobProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:50321")!, session: session)
        let response: MemoryJobStatusResponse = try await client.get("/api/memory/jobs/synthetic_job")
        try factRequire(response.job.id == "synthetic_job" && response.job.status == "completed" && response.job.stats?["projected"]?.integer == 2, "Projection polling decodes the actual success/data/job HTTP envelope and completion counters")
    }
    static func memoryFactsContract() throws {
        let historical = try factPage(includeHistory: true, historicalOnly: true)
        try factRequire(historical.includeHistory == true && historical.facts[0].isHistoricalOnly && historical.facts[0].status == .candidate && historical.facts[0].evidence.allSatisfy({ $0.active == false }), "Historical-only claims are discoverable explicitly and never presented as current supported claims")
        let currentScope = try MemoryFactsRequest(query: "")
        let historyScope = try MemoryFactsRequest(query: "", includeHistory: true)
        try factRequire(currentScope != historyScope && historyScope.queryItems.contains(where: { $0.name == "include_history" && $0.value == "true" }), "Historical inclusion is part of the exact request and action scope")
        var scoped = MemoryFactsState()
        let currentRequest = scoped.begin(currentScope)
        _ = try scoped.accept(factPage(), request: currentRequest)
        try factRequire(scoped.beginRefresh(historyScope) == nil, "Toggling historical inclusion cannot reuse a cached current-only action packet")
        let historicalRequest = scoped.begin(historyScope)
        do {
            _ = try scoped.accept(factPage(), request: historicalRequest)
            throw FactCheckFailure(message: "A response from another historical scope was accepted")
        } catch is ServiceError { }
        let legacy = try APIClient.decoder.decode(MemoryFact.self, from: Data(#"{"id":"legacy","kind":"fact","text":"Legacy claim","status":"candidate","evidence":[],"relations":[]}"#.utf8))
        try factRequire(legacy.historicalOnly == nil && !legacy.isHistoricalOnly, "Legacy current fact JSON remains compatible when optional history metadata is absent")
        let page = try factPage()
        try factRequire(page.facts.count == 1 && page.facts[0].evidence.count == 3, "One claim retains independent proof from three sources without duplicating the claim")
        let fact = page.facts[0]
        try factRequire(fact.status.title == "Подтверждается источниками" && fact.relations[0].suggestion && fact.relations[0].title == "Возможный конфликт", "Source support and proposed conflicts remain conservative labels rather than probabilities")
        try factRequire(fact.evidence[0].start == 0 && fact.evidence[0].end == 11 && fact.evidence[0].dates.messageAt == nil, "Fact offsets use the start/end wire keys and unknown message dates remain unknown")
        let framed = factProof().merging(["start": 17, "end": 28, "context_before": "It is false that ", "context_after": ""]) { _, value in value }
        let framedProof = try APIClient.decoder.decode(MemoryFactProof.self, from: JSONSerialization.data(withJSONObject: framed))
        try factRequire(framedProof.contextBefore == "It is false that " && framedProof.contextAfter == "", "Source framing remains visible around a quote and is not presented as a confirmed assertion")
        let request = try MemoryFactsRequest(query: "SQLite & C++", sourceId: "source-one")
        let values = Dictionary(uniqueKeysWithValues: request.queryItems.map { ($0.name, $0.value ?? "") })
        try factRequire(values["q"] == "SQLite & C++" && values["source_id"] == "source-one" && values["limit"] == "20", "Fact source scope is sent as bounded query values")
        let body = try JSONSerialization.jsonObject(with: APIClient.encoder.encode(FactsProjectionRequest())) as! [String: Any]
        try factRequire(body.count == 1 && body["limit"] as? Int == 100, "Manual projection sends only a bounded ready-card limit and no model/provider settings")
        let job = try APIClient.decoder.decode(MemoryJob.self, from: Data(#"{"id":"facts-job","kind":"facts","status":"running","processed":3,"total":100}"#.utf8))
        try factRequire(job.title == "Обновление фактов" && job.progressLabel == "3 из 100 карточек", "Projection jobs count visited cards and are distinct from imports or model generation")
    }

    static func memoryFactHistoryContract() throws {
        let history = try historyPage(event: "event-one", next: "cursor-one")
        let event = history.events[0]
        try factRequire(event.id == "event-one" && event.observedAt > history.historySince && event.proof.dates.messageAt == nil, "Observation time and feature-history boundary do not replace original source dates")
        try factRequire(!event.active && event.snapshotLabel == "Снимок прошлой ревизии", "A historical proof is presented as a saved snapshot rather than invalidated against current text")
        try factRequire(event.relations?.first?.historical == true && event.relations?.first?.evidence.active == false, "History relations retain their authorized historical proof instead of adopting current links")
        var state = MemoryFactHistoryState()
        let first = state.begin(factId: "fact_one")
        _ = try state.accept(history, request: first)
        let next = state.beginNextPage()!
        try factRequire(next.factId == "fact_one" && next.before == "cursor-one", "History continuation captures the selected fact and opaque cursor")
        _ = try state.accept(historyPage(event: "event-two"), request: next)
        try factRequire(state.events.map(\.id) == ["event-one", "event-two"] && state.before(for: "event-two") == "cursor-one", "History pages append unique stable event IDs and retain the page scope for fresh actions")
        let old = state.begin(factId: "fact_one")
        _ = state.begin(factId: "fact_two")
        let stale = try state.accept(history, request: old)
        try factRequire(!stale && state.events.isEmpty, "A delayed history page cannot populate another selected claim")
    }

    static func memoryFactActionScopes() throws {
        let parameters = try MemoryFactsRequest(query: "")
        var state = MemoryFactsState()
        let initial = state.begin(parameters)
        _ = try state.accept(factPage(), request: initial)
        let refresh = state.beginRefresh(parameters)!
        try factRequire(state.page == nil && state.isCurrent(refresh), "Opening a source first revokes the cached fact/support packet")
        _ = try state.accept(factPage(empty: true), request: refresh)
        try factRequire(!state.canPerformAction, "A fresh scope with hidden proofs cannot authorize a source action")
        let loaded = state.begin(parameters)
        _ = try state.accept(factPage(), request: loaded)
        let pending = state.beginRefresh(parameters)!
        state.invalidate()
        try factRequire(!state.isCurrent(pending), "Closing the fact browser invalidates pending original-source work")
        let proof = try factPage().facts[0].evidence[0]
        let original: [String: Any] = ["id": "record-one", "title": "Synthetic", "revision": 1, "source_id": "source-one", "source_kind": "folder", "source_object_id": "guide.md", "source_uri": "file:///synthetic/guide.md#L1-L2", "text": "Use SQLite."]
        let record = try APIClient.decoder.decode(MemoryRecord.self, from: JSONSerialization.data(withJSONObject: original))
        let newer = original.merging(["revision": 2, "text": "Use PostgreSQL."]) { _, value in value }
        let newerRecord = try APIClient.decoder.decode(MemoryRecord.self, from: JSONSerialization.data(withJSONObject: newer))
        try factRequire(proof.matchesCurrent(record) && !proof.matchesCurrent(newerRecord), "Only current fact proofs validate against exact current revisions; historical snapshots retain their separate saved status")
    }
}
