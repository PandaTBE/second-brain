import Foundation
import SecondBrainCore

private struct ConnectorFailure: Error { let message: String }
private func connectorRequire(_ value: Bool, _ message: String) throws {
    if !value { throw ConnectorFailure(message: message) }
}
private func decodeConnector<T: Decodable>(_ value: T.Type, _ object: Any) throws -> T {
    let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
    return try decoder.decode(value, from: JSONSerialization.data(withJSONObject: object))
}
private func connectorJSON<T: Encodable>(_ value: T) throws -> [String: Any] {
    let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
    return try JSONSerialization.jsonObject(with: encoder.encode(value)) as! [String: Any]
}
private func connectorSource(_ kind: String, fields: [String: Any] = [:]) throws -> MemorySource {
    let base: [String: Any] = ["id": "source", "name": "Example", "kind": kind,
                               "enabled": true, "credential_saved": false, "status": "ready"]
    return try decodeConnector(MemorySource.self, base.merging(fields) { _, new in new })
}
private func outlookStart(_ changes: [String: Any] = [:]) throws -> OutlookStartResponse {
    let base: [String: Any] = ["flow_id": "temporary-flow", "user_code": "ABCD-EFGH",
                               "verification_uri": "https://microsoft.com/devicelogin",
                               "expires_in": 900, "interval": 5]
    return try decodeConnector(OutlookStartResponse.self, base.merging(changes) { _, new in new })
}
private let exampleClientID = "00000000-0000-4000-8000-000000000001"

extension CoreChecks {
    static func appleNotesConnector() throws {
        try connectorRequire(SourceFormatting.label("apple_notes") == "Apple Заметки", "Apple Notes must have a native source label")
        let legacy = try connectorSource("folder")
        try connectorRequire(legacy.notesFolderIds == nil && legacy.outlookClientId == nil && legacy.outlookAccountId == nil, "Old source JSON must decode without new connector fields")
        let source = try connectorSource("apple_notes", fields: ["notes_folder_ids": ["folder-1"]])
        try connectorRequire(source.canImportMemory && source.location == "Выбрано папок: 1", "Notes source must expose selected folder scope")
        try connectorRequire(SourceFormatting.count(kind: "apple_notes", count: 3) == "3 заметок", "Notes counts must not say files")
        let folders = (0..<52).map { ["id": "folder-\($0)", "name": "Folder \($0)", "account": "iCloud", "path": "Projects/\($0)"] }
        let response = try decodeConnector(AppleNotesFoldersResponse.self, ["folders": folders + [folders[0]]])
        var selection = AppleNotesFolderSelection()
        try connectorRequire(selection.selectedIds.isEmpty && !selection.didLoad, "Discovery must not preselect folders")
        let request = selection.begin()!
        try connectorRequire(selection.begin() == nil, "Folder discovery must not overlap")
        try connectorRequire(selection.accept(response, request: request) && selection.folders.count == 52, "Folder IDs must be deduplicated")
        selection.toggle("unknown")
        try connectorRequire(selection.selectedIds.isEmpty, "Only discovered stable IDs may be selected")
        for folder in selection.folders { selection.toggle(folder.id) }
        try connectorRequire(selection.selectedIds.count == 50, "Folder selection must enforce backend scope limit")
        let before = selection.selectedIds
        try connectorRequire(selection.filtered("Projects/51").count == 1 && selection.selectedIds == before, "Searching must not broaden or discard selected folder scope")
        let payload = try connectorJSON(AddSourceRequest(kind: "apple_notes", name: "Notes", notesFolderIds: selection.selectedIds.sorted()))
        try connectorRequire((payload["notes_folder_ids"] as? [String])?.count == 50 && payload["token"] == nil && payload["password"] == nil && payload["path"] == nil, "Notes requests must send selected IDs without credentials or filesystem access")
        let reload = selection.begin()!
        try connectorRequire(selection.selectedIds.isEmpty && !selection.accept(response, request: request), "Reload must revoke previous folder IDs and responses")
        selection.invalidate()
        try connectorRequire(!selection.accept(response, request: reload), "Closing discovery must ignore late folder results")
    }
    static func outlookConnector() throws {
        try connectorRequire(SourceFormatting.label("outlook") == "Outlook / Microsoft 365", "Outlook must have its own OAuth source label")
        let paused = try connectorSource("outlook", fields: ["username": "example@example.test", "mail_import_enabled": false])
        let active = try connectorSource("outlook", fields: ["mail_import_enabled": true, "outlook_client_id": exampleClientID, "outlook_account_id": "account"])
        try connectorRequire(paused.isMailSource && !paused.canImportMemory && paused.location == "example@example.test" && active.canImportMemory, "Outlook must honor the mail import toggle")
        let requestJSON = try connectorJSON(OutlookStartRequest(clientId: " \(exampleClientID)\n"))
        try connectorRequire(requestJSON["client_id"] as? String == exampleClientID && requestJSON.count == 1, "Device login starts with only the trimmed public Client ID")
        let pollJSON = try connectorJSON(OutlookFlowRequest(flowId: "flow"))
        try connectorRequire(pollJSON["flow_id"] as? String == "flow" && pollJSON.count == 1, "Poll and cancel must use only a temporary flow ID")
        let create = try connectorJSON(AddSourceRequest(kind: "outlook", name: "Outlook", mailImportEnabled: false, mailLookbackDays: 90, outlookAuthId: "ticket"))
        try connectorRequire(create["outlook_auth_id"] as? String == "ticket" && create["mail_import_enabled"] as? Bool == false && create["mail_lookback_days"] as? Int == 90, "Final Add must preserve the explicitly chosen mail scope")
        try connectorRequire(create["token"] == nil && create["password"] == nil && create["client_secret"] == nil, "Outlook UI must never send raw OAuth credentials")
        try connectorRequire(OutlookSignInState.validClientID(exampleClientID) && !OutlookSignInState.validClientID("secret") && !OutlookSignInState.validClientID("{\(exampleClientID)}"), "Client ID must have GUID format")
        for unsafe in ["http://microsoft.com/devicelogin", "https://microsoft.com.evil.test/devicelogin", "https://user@microsoft.com/devicelogin", "https://microsoft.com/devicelogin?code=secret", "https://microsoft.com/devicelogin#code", "https://microsoft.com:8443/devicelogin", "https://evil.test"] {
            try connectorRequire(OutlookSignInState.trustedVerificationURL(unsafe) == nil, "Reject untrusted Microsoft login URL: \(unsafe)")
        }
        try connectorRequire(OutlookSignInState.trustedVerificationURL("https://microsoft.com/devicelogin") != nil, "Allow the canonical Microsoft device login page")
        try connectorRequire(OutlookSignInState.trustedVerificationURL("https://www.microsoft.com/devicelogin")?.absoluteString == "https://microsoft.com/devicelogin", "Allow the exact Microsoft www login host and canonicalize it")
        let now = Date(timeIntervalSince1970: 1_000)
        var state = OutlookSignInState()
        try connectorRequire(state.begin(clientID: "invalid") == nil && !state.isStarting, "Invalid Client IDs must not start login")
        let first = state.begin(clientID: exampleClientID)!
        try connectorRequire(!state.accept(try outlookStart(["verification_uri": "https://evil.test"]), request: first, now: now), "Reject a malicious verification URL before displaying a code")
        try connectorRequire(state.accept(try outlookStart(), request: first, now: now) && state.isWaiting && state.userCode == "ABCD-EFGH", "A valid login response must show the code and wait")
        let slow = try decodeConnector(OutlookPollResponse.self, ["status": "slow_down"])
        try connectorRequire(state.accept(slow, request: first, now: now) && state.interval == 10, "Server slow_down must reduce polling rate")
        let pending = try decodeConnector(OutlookPollResponse.self, ["status": "pending", "interval": 5])
        try connectorRequire(state.accept(pending, request: first, now: now) && state.interval == 10, "Polling interval must not decrease")
        let complete = try decodeConnector(OutlookPollResponse.self, ["status": "completed", "auth_id": "ticket", "account": ["id": "account", "username": "example@example.test", "display_name": "Example"]])
        try connectorRequire(state.accept(complete, request: first, now: now) && state.isAuthorized && state.userCode == nil && state.verificationURL == nil, "Successful login must retain only ticket/account and hide the device code")
        try connectorRequire(state.invalidate() == "temporary-flow" && !state.isAuthorized && state.account == nil, "Cancellation must revoke flow and clear account/ticket")
        try connectorRequire(!state.accept(complete, request: first, now: now), "Late completion after cancellation must not reconnect")
        let second = state.begin(clientID: exampleClientID)!
        try connectorRequire(!state.accept(try outlookStart(), request: first, now: now), "A stale start response must not replace a newer attempt")
        try connectorRequire(state.accept(try outlookStart(["expires_in": 1]), request: second, now: now), "A new login attempt must remain usable")
        try connectorRequire(state.pollDelay(now: now) == 1 && state.pollDelay(now: now.addingTimeInterval(2)) == nil, "Polling sleep must stop at the code expiry rather than outlive it")
        try connectorRequire(!state.accept(complete, request: second, now: now.addingTimeInterval(2)), "Expired device codes must not complete locally")
    }
}
