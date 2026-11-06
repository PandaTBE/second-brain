import Foundation

public struct BackupSettings: Codable, Equatable {
    public var enabled: Bool
    public var directory: String?
    public var intervalHours: Int
    public var maxCopies: Int
    public var maxTotalBytes: Int
    public init(enabled: Bool = false, directory: String? = nil, intervalHours: Int = 24,
                maxCopies: Int = 10, maxTotalBytes: Int = 1_073_741_824) {
        self.enabled = enabled; self.directory = directory; self.intervalHours = intervalHours
        self.maxCopies = maxCopies; self.maxTotalBytes = maxTotalBytes
    }
    public func validated() throws -> Self {
        guard (1...168).contains(intervalHours), (1...50).contains(maxCopies),
              (1_048_576...10_737_418_240).contains(maxTotalBytes) else {
            throw ServiceError("Проверьте интервал, число копий и предел общего размера.")
        }
        if let directory {
            guard directory.hasPrefix("/"), directory.count <= 4096,
                  directory.rangeOfCharacter(from: .controlCharacters) == nil else {
                throw ServiceError("Выберите папку резервных копий по полному пути.")
            }
        }
        return self
    }
}

public struct ManagedBackup: Decodable, Identifiable {
    public let id: String
    public let name: String
    public let createdAt: String
    public let sizeBytes: Int
    public let encrypted: Bool
}
public struct BackupStatus: Decodable {
    public let settings: BackupSettings
    public let backups: [ManagedBackup]
    public let lastError: String?
    public let warning: String?
    public let limits: Limits
    public struct Limits: Decodable { public let maxArchiveBytes: Int }
}
public struct BackupCreatedResponse: Decodable { public let backup: ManagedBackup }
public struct BackupExportResponse: Decodable {
    public let path: String
    public let sizeBytes: Int
    public let encrypted: Bool
    public let format: String
}
public struct BackupRecoverySource: Decodable, Identifiable {
    public let sourceId: String
    public let name: String
    public let kind: String
    public var id: String { sourceId }
}
public struct BackupInspection: Decodable {
    public let inspectionId: String
    public let expiresAt: String
    public let createdAt: String
    public let appVersion: String
    public let documents: Int
    public let sources: Int
    public let foreignInstallation: Bool
    public let requiresRelocation: [BackupRecoverySource]
    public let requiresReauthentication: [BackupRecoverySource]
    public let warnings: [String]
    public var expiration: Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: expiresAt) ?? ISO8601DateFormatter().date(from: expiresAt)
    }
}
public struct BackupRestoreResponse: Decodable {
    public let restored: Bool
    public let documents: Int
    public let sources: Int
    public let foreignInstallation: Bool
    public let restartRecommended: Bool
    public let paused: Bool
}
public struct BackupExportRequest: Encodable {
    public let path: String
    public let passphrase: String
    public init(path: String, passphrase: String) { self.path = path; self.passphrase = passphrase }
}
public struct BackupInspectRequest: Encodable {
    public let path: String
    public let passphrase: String?
    public init(path: String, passphrase: String?) { self.path = path; self.passphrase = passphrase }
}
public struct BackupRestoreRequest: Encodable {
    public let inspectionId: String
    public init(inspectionId: String) { self.inspectionId = inspectionId }
}
public struct SourceRelocationRequest: Encodable {
    public let path: String?
    public let notesFolderIds: [String]
    public init(path: String? = nil, notesFolderIds: [String] = []) {
        self.path = path; self.notesFolderIds = notesFolderIds
    }
}
public struct SourceReauthenticationRequest: Encodable {
    public let token: String?
    public let appPassword: String?
    public let outlookAuthId: String?
    public init(token: String? = nil, appPassword: String? = nil, outlookAuthId: String? = nil) {
        self.token = token; self.appPassword = appPassword; self.outlookAuthId = outlookAuthId
    }
}
public struct MemoryOpenTarget: Decodable {
    public let uri: String?
    public let available: Bool
    public let message: String?
    public let reference: MemorySourceReference?
    public func target(sourceKind: String, localFiles: LocalOriginalAccess) -> OriginalSourceTarget? {
        guard available, let uri else { return nil }
        return OriginalSourceLink.target(uri: uri, sourceKind: sourceKind, localFiles: localFiles)
    }
}
