import Foundation
import SecondBrainCore

private struct BackupCheckFailure: Error { let message: String }
private func backupRequire(_ condition: @autoclosure () -> Bool, _ message: String) throws {
    if !condition() { throw BackupCheckFailure(message: message) }
}

extension CoreChecks {
    static func backupPassphraseViewState() throws {
        let composed = BackupPassphraseValue("synthetic-caf\u{e9}")
        let decomposed = BackupPassphraseValue("synthetic-cafe\u{301}")
        try backupRequire(composed.text == decomposed.text && composed != decomposed, "View state must distinguish canonically equal password bytes")
        try backupRequire(BackupPassphrase.exportRequest(path: "/synthetic/archive.age", passphrase: composed, confirmation: decomposed) == nil, "An action must reject a byte-distinct confirmation even if the UI was enabled earlier")
        let request = BackupPassphrase.exportRequest(path: "/synthetic/archive.age", passphrase: decomposed, confirmation: decomposed)
        try backupRequire(request?.passphrase.utf8.elementsEqual(decomposed.text.utf8) == true, "Matching state must export exact bytes without normalization")
        try backupRequire(BackupPassphrase.exportRequest(path: "", passphrase: composed, confirmation: composed) == nil, "Export still requires a chosen destination")
        try backupRequire(BackupPassphraseValue().text.isEmpty && BackupPassphraseValue() != composed, "Clearing must replace secret view state with empty bytes")
    }

    static func backupPassphraseConfirmation() throws {
        let composed = "synthetic-caf\u{e9}"
        let decomposed = "synthetic-cafe\u{301}"
        try backupRequire(composed == decomposed, "Regression fixture must be canonically equal in Swift")
        try backupRequire(!BackupPassphrase.matchesConfirmation(composed, decomposed), "Confirmation must reject distinct password bytes even when they look identical")
        try backupRequire(BackupPassphrase.matchesConfirmation(decomposed, decomposed), "The exact entered password must remain accepted")
        let request = BackupExportRequest(path: "/synthetic/archive.age", passphrase: decomposed)
        let data = try APIClient.encoder.encode(request)
        let body = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        try backupRequire((body["passphrase"] as? String)?.utf8.elementsEqual(decomposed.utf8) == true, "Export must preserve the exact password without normalization")
    }

    static func backupSettingsContract() throws {
        let defaults = BackupSettings()
        try backupRequire(!defaults.enabled && defaults.intervalHours == 24, "Backups are opt-in")
        let validated = try defaults.validated()
        let body = try JSONSerialization.jsonObject(with: APIClient.encoder.encode(validated)) as! [String: Any]
        try backupRequire(body["max_copies"] as? Int == 10 && body["max_total_bytes"] as? Int == 1_073_741_824, "Retention fields use the backend contract")
        try backupRequire(!body.keys.contains("passphrase"), "Scheduled plain backups never persist an export password")
        for invalid in [BackupSettings(intervalHours: 0), BackupSettings(maxCopies: 51), BackupSettings(directory: "../elsewhere"), BackupSettings(maxTotalBytes: 1)] {
            do { _ = try invalid.validated(); throw BackupCheckFailure(message: "Invalid backup settings accepted") }
            catch is ServiceError { }
        }
        let request = BackupInspectRequest(path: "/synthetic/private.age", passphrase: "synthetic-password")
        let payload = try JSONSerialization.jsonObject(with: APIClient.encoder.encode(request)) as! [String: Any]
        try backupRequire(payload["path"] as? String == "/synthetic/private.age", "Inspect uses the selected path")
    }

    static func backupInspectionContract() throws {
        let preview = try APIClient.decoder.decode(BackupInspection.self, from: Data(#"{"inspection_id":"opaque-ticket","expires_at":"2026-11-06T12:10:00Z","created_at":"2026-11-06T12:00:00Z","app_version":"0.1.0","documents":8,"sources":2,"foreign_installation":true,"requires_relocation":[{"source_id":"one","name":"Local","kind":"folder"}],"requires_reauthentication":[],"warnings":["Synthetic warning"]}"#.utf8))
        var state = BackupInspectionState()
        let first = state.begin(path: "/synthetic/one.age")
        let second = state.begin(path: "/synthetic/two.age")
        try backupRequire(!state.accept(preview, request: first), "A stale response cannot authorize restore")
        try backupRequire(state.accept(preview, request: second), "The current staged preview is accepted")
        let before = ISO8601DateFormatter().date(from: "2026-11-06T12:05:00Z")!
        let after = ISO8601DateFormatter().date(from: "2026-11-06T12:11:00Z")!
        try backupRequire(state.restoreRequest(now: before)?.inspectionId == "opaque-ticket", "Restore sends only the reviewed staging ticket")
        let restore = try JSONSerialization.jsonObject(with: APIClient.encoder.encode(state.restoreRequest(now: before)!)) as! [String: Any]
        try backupRequire(Set(restore.keys) == ["inspection_id"], "Restore contains no password, path or unchecked replacement content")
        try backupRequire(state.restoreRequest(now: after) == nil, "Expired staging requires a new inspection")
        state.invalidate()
        try backupRequire(state.restoreRequest(now: before) == nil && !state.accept(preview, request: second), "Closing clears restore authority")
    }

    static func sourceRecoveryContract() throws {
        let pending = try APIClient.decoder.decode(MemorySource.self, from: Data(#"{"id":"restored","name":"Restored","kind":"folder","path":"/previous-machine","enabled":true,"credential_saved":false,"status":"not_checked","requires_relocation":true,"requires_reauthentication":false}"#.utf8))
        try backupRequire(pending.requiresRelocation && !pending.canImportMemory, "Restored paths stay dormant until explicitly chosen")
        let remote = try APIClient.decoder.decode(MemorySource.self, from: Data(#"{"id":"remote","name":"Remote","kind":"github","enabled":true,"credential_saved":true,"status":"ok","requires_reauthentication":true}"#.utf8))
        try backupRequire(remote.requiresReauthentication && !remote.canImportMemory, "Coinciding credential metadata cannot reactivate a restored connector")
        let legacy = try APIClient.decoder.decode(MemorySource.self, from: Data(#"{"id":"old","name":"Existing","kind":"folder","enabled":true,"credential_saved":false,"status":"ok"}"#.utf8))
        try backupRequire(!legacy.requiresRelocation && !legacy.requiresReauthentication, "Existing sources preserve legacy behavior")
    }

    static func restoreOpeningContract() throws {
        let denied = try APIClient.decoder.decode(MemoryOpenTarget.self, from: Data(#"{"available":false,"uri":"file:///previous-machine/note.md","message":"Relocate source"}"#.utf8))
        try backupRequire(denied.target(sourceKind: "folder", localFiles: .currentDevice) == nil, "A stale provenance URI never bypasses a current server denial")
        let local = try APIClient.decoder.decode(MemoryOpenTarget.self, from: Data(#"{"available":true,"uri":"file:///synthetic/chosen/note.md"}"#.utf8))
        try backupRequire(local.target(sourceKind: "folder", localFiles: .requiresRelocation) == nil, "Local relocation denial remains fail closed")
        try backupRequire(local.target(sourceKind: "apple_notes", localFiles: .currentDevice) == nil, "Opaque Notes IDs never become an invented file link")
        let hostile = try APIClient.decoder.decode(MemoryOpenTarget.self, from: Data(#"{"available":true,"uri":"javascript:alert(1)"}"#.utf8))
        try backupRequire(hostile.target(sourceKind: "folder", localFiles: .currentDevice) == nil, "Server responses still pass the native URI safety boundary")
    }
}
