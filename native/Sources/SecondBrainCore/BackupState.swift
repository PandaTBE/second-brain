import Foundation

/// age derives its key from exact password bytes, without Unicode normalization.
public enum BackupPassphrase {
    public static func matchesConfirmation(_ passphrase: String, _ confirmation: String) -> Bool {
        passphrase.utf8.elementsEqual(confirmation.utf8)
    }
}

/// Restore accepts only the current server-staged inspection and expires with it.
public struct BackupInspectionState {
    public struct Request: Equatable { fileprivate let revision: Int }
    public private(set) var inspection: BackupInspection?
    public private(set) var isLoading = false
    private var revision = 0
    public init() {}
    public mutating func invalidate() { revision += 1; inspection = nil; isLoading = false }
    public mutating func begin(path: String) -> Request {
        invalidate(); isLoading = true
        return Request(revision: revision)
    }
    @discardableResult public mutating func accept(_ result: BackupInspection, request: Request) -> Bool {
        guard request.revision == revision, isLoading, !result.inspectionId.isEmpty,
              result.expiration != nil, result.documents >= 0, result.sources >= 0 else { return false }
        inspection = result; isLoading = false
        return true
    }
    @discardableResult public mutating func fail(_ request: Request) -> Bool {
        guard request.revision == revision, isLoading else { return false }
        isLoading = false; inspection = nil
        return true
    }
    public func restoreRequest(now: Date = Date()) -> BackupRestoreRequest? {
        guard !isLoading, let inspection, let expiration = inspection.expiration, now < expiration else { return nil }
        return BackupRestoreRequest(inspectionId: inspection.inspectionId)
    }
}
