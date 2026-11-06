import Foundation
import SecondBrainCore

private struct FactCheckFailure: Error { let message: String }
private func factRequire(_ condition: @autoclosure () -> Bool, _ message: String) throws {
    if !condition() { throw FactCheckFailure(message: message) }
}
private func factProof(_ source: String = "source-one") -> [String: Any] {
    ["citation_id": "sb_\(source)", "record_id": "record-one", "revision": 1,
     "source_id": source, "source_kind": "folder", "source_object_id": "guide.md",
     "title": "Synthetic decision", "source_uri": "file:///synthetic/guide.md#L1-L2",
     "start": 0, "end": 11, "quote": "Use SQLite.",
     "dates": ["message_at": NSNull(), "source_created_at": NSNull(), "source_modified_at": NSNull(), "imported_at": "2026-11-06T08:00:00Z"]]
}
private func factPage(query: String = "", empty: Bool = false) throws -> MemoryFactsResponse {
    let fact: [String: Any] = [
        "id": "fact_one", "kind": "decision", "text": "Use SQLite.", "status": "source_supported",
        "evidence": [factProof(), factProof("source-two"), factProof("source-three")],
        "relations": [["kind": "possible_conflict", "target_id": "fact_two", "target_text": "Use PostgreSQL.", "suggestion": true, "evidence": factProof()]]
    ]
    let body: [String: Any] = ["schema_version": 1, "query": query, "facts": empty ? [] : [fact], "history_since": "2026-11-06T07:00:00Z", "untrusted_content": true, "truncated": false]
    return try APIClient.decoder.decode(MemoryFactsResponse.self, from: JSONSerialization.data(withJSONObject: body))
}
private func historyPage(event: String, next: String? = nil, active: Bool = false) throws -> MemoryFactHistoryResponse {
    let item = factProof().merging(["event_id": event, "observed_at": "2026-11-06T08:05:00Z", "active": active, "status": "source_supported", "model": "synthetic-card-model", "prompt_version": "3"]) { _, value in value }
    let body: [String: Any] = ["schema_version": 1, "fact": ["id": "fact_one", "kind": "decision", "text": "Use SQLite."], "events": [item], "history_since": "2026-11-06T07:00:00Z", "untrusted_content": true, "truncated": next != nil, "next_cursor": next as Any? ?? NSNull()]
    return try APIClient.decoder.decode(MemoryFactHistoryResponse.self, from: JSONSerialization.data(withJSONObject: body))
}

extension CoreChecks {
    static func memoryFactsContract() throws {
        let page = try factPage()
        try factRequire(page.facts.count == 1 && page.facts[0].evidence.count == 3, "One claim retains independent proof from three sources without duplicating the claim")
        let fact = page.facts[0]
        try factRequire(fact.status.title == "Подтверждается источниками" && fact.relations[0].suggestion && fact.relations[0].title == "Возможный конфликт", "Source support and proposed conflicts remain conservative labels rather than probabilities")
        try factRequire(fact.evidence[0].start == 0 && fact.evidence[0].end == 11 && fact.evidence[0].dates.messageAt == nil, "Fact offsets use the start/end wire keys and unknown message dates remain unknown")
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
