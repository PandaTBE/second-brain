import Foundation

public struct BackupOperations {
    private let client: APIClient
    public init(client: APIClient) { self.client = client }
    public func status() async throws -> BackupStatus { try await client.get("/api/backups") }
    public func save(_ settings: BackupSettings) async throws -> BackupStatus {
        try await client.send("/api/backups/settings", method: "PUT", body: settings.validated())
    }
    public func createLocal() async throws -> BackupCreatedResponse {
        try await client.send("/api/backups/local", body: EmptyBody(), timeout: 120)
    }
    public func export(_ request: BackupExportRequest) async throws -> BackupExportResponse {
        try await client.send("/api/backups/export", body: request, timeout: 120)
    }
    public func inspect(_ request: BackupInspectRequest) async throws -> BackupInspection {
        try await client.send("/api/backups/inspect", body: request, timeout: 120)
    }
    public func restore(_ request: BackupRestoreRequest) async throws -> BackupRestoreResponse {
        try await client.send("/api/backups/restore", body: request, timeout: 120)
    }
    public func delete(_ id: String) async throws -> DeletedResponse {
        try await client.send("/api/backups/\(id)", method: "DELETE", body: EmptyBody())
    }
}
