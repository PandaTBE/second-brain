import Foundation
import SecondBrainCore

private struct PolicyCheckFailure: Error { let message: String }
private func policyRequire(_ condition: @autoclosure () -> Bool, _ message: String) throws {
    if !condition() { throw PolicyCheckFailure(message: message) }
}

extension CoreChecks {
    static func sourcePolicyContract() throws {
        let legacy = try APIClient.decoder.decode(SourcePolicy.self, from: Data("{}".utf8))
        try policyRequire(SourcePermission.allCases.allSatisfy { legacy.allows($0) }, "Legacy source policies preserve all existing permissions")
        let localOnly = legacy.setting(.externalModel, allowed: false)
        try policyRequire(!localOnly.externalModelEnabled && localOnly.mcpEnabled && localOnly.localModelEnabled && localOnly.localSearchEnabled && localOnly.importEnabled, "Local model processing and MCP visibility are independent")
        try policyRequire(legacy.externalModelEnabled, "Permission changes return a new policy")
        let hidden = localOnly.setting(.mcp, allowed: false)
        try policyRequire(hidden.localSearchEnabled && !hidden.mcpEnabled, "MCP privacy does not disable local search")
        let request = try hidden.excluding(paths: ["private", "drafts/secret.md"], notesFolderIDs: ["folder-one"])
        let body = try JSONSerialization.jsonObject(with: APIClient.encoder.encode(request)) as! [String: Any]
        try policyRequire(body.count == 7 && body["mcp_enabled"] as? Bool == false && body["external_model_enabled"] as? Bool == false, "The direct PUT body contains only policy fields")
        try policyRequire(body["excluded_paths"] as? [String] == ["private", "drafts/secret.md"] && body["excluded_notes_folder_ids"] as? [String] == ["folder-one"], "Exclusion fields encode exactly")
        let response = try APIClient.decoder.decode(SourcePolicyResponse.self, from: JSONSerialization.data(withJSONObject: ["policy": body]))
        try policyRequire(response.policy == request, "Policy responses decode the endpoint envelope")
        let sourceJSON: [String: Any] = ["id": "source", "name": "Example", "kind": "folder", "enabled": true, "credential_saved": false, "status": "ok", "policy": try JSONSerialization.jsonObject(with: APIClient.encoder.encode(legacy.setting(.importMaterials, allowed: false)))]
        let source = try APIClient.decoder.decode(MemorySource.self, from: JSONSerialization.data(withJSONObject: sourceJSON))
        try policyRequire(!source.canImportMemory && source.policy?.mcpEnabled == true, "The native import action respects the policy without hiding local or MCP access")
    }

    static func sourcePolicyPreview() throws {
        let original = SourcePolicy()
        for path in ["../private", "/private", "secrets/*", "a/../../b", "x\u{0}y"] {
            do {
                _ = try original.excluding(paths: [path], notesFolderIDs: [])
                throw PolicyCheckFailure(message: "Unsafe path accepted: \(path)")
            } catch is ServiceError { }
        }
        let normalized = try original.excluding(paths: [" private/ ", "private", "notes/readme.md"], notesFolderIDs: ["b", "a", "b"])
        try policyRequire(normalized.excludedPaths == ["private", "notes/readme.md"] && normalized.excludedNotesFolderIds == ["b", "a"], "Validation trims a directory slash and deduplicates while preserving order")
        for count in [101, 102] {
            do { _ = try original.excluding(paths: Array(repeating: "private", count: count), notesFolderIDs: []); throw PolicyCheckFailure(message: "Too many path exclusions accepted") }
            catch is ServiceError { }
        }
        do { _ = try original.excluding(paths: [], notesFolderIDs: Array(repeating: "folder", count: 51)); throw PolicyCheckFailure(message: "Too many Notes exclusions accepted") }
        catch is ServiceError { }
        let scoped = try original.excluding(paths: ["private"], notesFolderIDs: ["selected"])
        var state = SourcePolicyPreviewState()
        try policyRequire(!state.canSave(scoped, original: original), "Changed exclusions require a completed preview")
        let first = state.begin(scoped)
        let newer = try original.excluding(paths: ["other"], notesFolderIDs: [])
        let second = state.begin(newer)
        let preview = try APIClient.decoder.decode(SourcePolicyPreview.self, from: Data(#"{"affected_documents":2,"samples":[{"id":"one","title":"Example","source_uri":"opaque-id"}],"sample_limit":20}"#.utf8))
        try policyRequire(!state.accept(preview, request: first, policy: scoped), "Late previews cannot approve a newer draft")
        try policyRequire(state.accept(preview, request: second, policy: newer) && state.canSave(newer, original: original), "Only the current exact preview permits saving changed exclusions")
        try policyRequire(!state.canSave(scoped, original: original), "A preview never authorizes a different policy")
        state.invalidate()
        try policyRequire(!state.canSave(newer, original: original) && !state.accept(preview, request: second, policy: newer), "Closing or editing invalidates pending and accepted preview results")
        try policyRequire(state.canSave(original.setting(.mcp, allowed: false), original: original), "Permission-only changes do not require an unrelated exclusion preview")
    }
}
