import Foundation

/// The final await must not authorize a URI for a different source or revision.
public struct OriginalSourceOperations {
    private let client: APIClient
    public init(client: APIClient) { self.client = client }

    public func target(for record: MemoryRecord, localFiles: LocalOriginalAccess = .currentDevice) async throws -> OriginalSourceTarget {
        guard let revision = record.revision, revision > 0, let kind = record.sourceKind else {
            throw ServiceError("Сначала обновите материал перед открытием оригинала.")
        }
        let expected = MemorySourceReference(record: record)
        let response: MemoryOpenTarget = try await client.get("/api/memory/records/\(record.id)/open-target")
        try Task.checkCancellation()
        guard let current = response.reference, current == expected else {
            throw ServiceError("Материал или ссылка изменились. Обновите материал и выберите действие заново.")
        }
        guard let target = response.target(sourceKind: kind, localFiles: localFiles) else {
            throw ServiceError(response.message ?? "Оригинал недоступен. Проверьте путь источника после восстановления.")
        }
        return target
    }
}
