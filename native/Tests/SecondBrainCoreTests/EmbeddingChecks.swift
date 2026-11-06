#if SECOND_BRAIN_STAGE2_CHECKS
import Foundation
import SecondBrainCore

private struct EmbeddingCheckFailure: Error { let message: String }
private func embeddingRequire(_ condition: @autoclosure () -> Bool, _ message: String) throws {
    if !condition() { throw EmbeddingCheckFailure(message: message) }
}

extension CoreChecks {
    static func embeddingSettingsContract() throws {
        let disabled = try APIClient.decoder.decode(EmbeddingSettings.self, from: Data(#"{"enabled":false,"model_id":null,"digest":null,"dimension":null,"generation":null,"prep_version":1,"ready":0,"pending":0,"errors":0,"message":null}"#.utf8))
        try embeddingRequire(!disabled.enabled && disabled.modelId == nil && disabled.dimension == nil && disabled.generation == nil && disabled.ready == 0, "Embeddings start disabled without borrowing the card model")
        let active = try APIClient.decoder.decode(EmbeddingSettings.self, from: Data(#"{"enabled":true,"model_id":"embed-test:latest","digest":"sha256:synthetic","dimension":768,"generation":"generation-two","prep_version":1,"ready":7,"pending":3,"errors":1,"message":"Local model"}"#.utf8))
        try embeddingRequire(active.modelId == "embed-test:latest" && active.digest == "sha256:synthetic" && active.dimension == 768 && active.ready == 7 && active.pending == 3 && active.errors == 1, "The native UI retains index identity, dimension and separate progress counts")
        let request = EmbeddingSettingsRequest(enabled: true, modelId: "embed-test:latest")
        let body = try JSONSerialization.jsonObject(with: APIClient.encoder.encode(request)) as! [String: Any]
        try embeddingRequire(body.count == 2 && body["enabled"] as? Bool == true && body["model_id"] as? String == "embed-test:latest", "Explicit embedding settings never send card-provider settings, credentials, digest or an invented dimension")
        let disable = try JSONSerialization.jsonObject(with: APIClient.encoder.encode(EmbeddingSettingsRequest(enabled: false, modelId: nil))) as! [String: Any]
        try embeddingRequire(disable.count == 1 && disable["enabled"] as? Bool == false, "Disabling does not need a model or a provider request")
        let response = try APIClient.decoder.decode(EmbeddingModelsResponse.self, from: Data(#"{"models":[{"id":"embed-test:latest","name":"embed-test:latest","digest":"sha256:synthetic"}],"message":null}"#.utf8))
        var selection = EmbeddingModelSelection()
        try embeddingRequire(!selection.canEnable && selection.selectedId == nil, "An embedding model is never selected automatically")
        let first = selection.begin()
        let second = selection.begin()
        try embeddingRequire(!selection.accept(response, request: first), "Late model catalog results are ignored")
        try embeddingRequire(selection.accept(response, request: second) && !selection.canEnable, "Discovering installed models does not enable or select one")
        try embeddingRequire(!selection.select("not-installed") && selection.select("embed-test:latest") && selection.canEnable, "Only a digest-qualified installed catalog entry can be enabled")
        selection.invalidate()
        try embeddingRequire(!selection.canEnable && !selection.accept(response, request: second), "Closing settings invalidates pending model discovery")
    }

    static func embeddingSearchContract() throws {
        let legacy = try APIClient.decoder.decode(MemorySearchResponse.self, from: Data(#"{"results":[]}"#.utf8))
        try embeddingRequire(legacy.search == nil, "The existing search result contract remains compatible")
        let fallback = try APIClient.decoder.decode(MemorySearchResponse.self, from: Data(#"{"results":[],"search":{"requested_mode":"hybrid","used_mode":"lexical","fallback_reason":"embeddings_disabled","generation":null,"query_embedding_ms":null,"vector_search_ms":null,"eligible":4,"ready":0,"partial":true}}"#.utf8))
        try embeddingRequire(fallback.search?.requestedMode == .hybrid && fallback.search?.usedMode == .lexical && fallback.search?.fallbackReason == "embeddings_disabled", "Requested and actual search modes remain distinguishable on fallback")
        try embeddingRequire(fallback.search?.queryEmbeddingMs == nil && fallback.search?.vectorSearchMs == nil && fallback.search?.partial == true, "Missing local-model timings remain unknown and partial coverage stays visible")
        let complete = try APIClient.decoder.decode(MemorySearchResponse.self, from: Data(#"{"results":[],"search":{"requested_mode":"semantic","used_mode":"semantic","fallback_reason":null,"generation":"generation-two","query_embedding_ms":12.5,"vector_search_ms":2.3,"eligible":10,"ready":10,"partial":false}}"#.utf8))
        try embeddingRequire(complete.search?.usedMode == .semantic && complete.search?.queryEmbeddingMs == 12.5 && complete.search?.vectorSearchMs == 2.3 && complete.search?.generation == "generation-two", "Native search keeps vector lookup and query embedding timing separate")
        try embeddingRequire(MemorySearchMode.allCases.map(\.rawValue) == ["lexical", "semantic", "hybrid"], "Only the three bounded search modes enter requests")
        var browsing = MemoryBrowseState()
        let old = browsing.beginFirstPage(query: "same query", mode: .semantic)
        let latest = browsing.beginFirstPage(query: "same query", mode: .hybrid)
        try embeddingRequire(old.mode == .semantic && latest.mode == .hybrid && latest.query == "same query", "Each request generation captures its query and requested mode")
        let oldAccepted = try browsing.acceptSearch(complete, for: old)
        try embeddingRequire(!oldAccepted && browsing.search == nil, "A delayed response for the old mode cannot populate results or diagnostics")
        let currentAccepted = try browsing.acceptSearch(fallback, for: latest)
        try embeddingRequire(currentAccepted && browsing.search?.usedMode == .lexical && !browsing.hasMore, "The latest bounded search result carries actual fallback diagnostics without a fictitious next page")
        let blank = browsing.beginFirstPage(query: "  ", mode: .hybrid, filter: .pending)
        try embeddingRequire(blank.mode == .lexical && blank.filter == .pending, "Blank queries preserve lexical history and status pagination")
    }
}
#endif
