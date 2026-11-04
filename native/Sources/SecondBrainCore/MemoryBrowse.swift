import Foundation

public enum MemoryBrowseFilter: String, CaseIterable, Identifiable {
    case all, ready, pending, error
    public var id: String { rawValue }
    public var title: String {
        switch self {
        case .all: return "Материалы"
        case .ready: return "Карточки"
        case .pending: return "Ожидают"
        case .error: return "С ошибкой"
        }
    }
    public var emptyTitle: String {
        switch self {
        case .all: return "Память пока пуста"
        case .ready: return "Готовых карточек пока нет"
        case .pending: return "Нет ожидающих материалов"
        case .error: return "Материалов с ошибками нет"
        }
    }
}

public struct MemoryBrowseResponse: Decodable {
    public let results: [MemoryRecord]
    public let nextCursor: String?
    public let hasMore: Bool
}

public struct MemoryBrowseRequest: Equatable {
    public let cursor: String?
    fileprivate let generation: UUID
    fileprivate let id: UUID
}

public struct MemoryBrowseState {
    public private(set) var results: [MemoryRecord] = []
    public private(set) var nextCursor: String?
    public private(set) var hasMore = false
    public private(set) var isLoading = false
    public private(set) var error: String?
    private var generation = UUID()
    private var active: MemoryBrowseRequest?

    public init() {}

    public mutating func invalidate() {
        generation = UUID()
        results = []; nextCursor = nil; hasMore = false
        active = nil; isLoading = false; error = nil
    }

    public mutating func beginFirstPage() -> MemoryBrowseRequest {
        invalidate()
        let request = MemoryBrowseRequest(cursor: nil, generation: generation, id: UUID())
        active = request; isLoading = true
        return request
    }

    public mutating func beginNextPage() -> MemoryBrowseRequest? {
        guard !isLoading, hasMore, let cursor = nextCursor else { return nil }
        let request = MemoryBrowseRequest(cursor: cursor, generation: generation, id: UUID())
        active = request; isLoading = true; error = nil
        return request
    }

    @discardableResult
    public mutating func accept(_ page: MemoryBrowseResponse, for request: MemoryBrowseRequest) throws -> Bool {
        guard request.generation == generation, active == request else { return false }
        if page.hasMore && (page.nextCursor?.isEmpty != false || page.nextCursor == request.cursor) {
            throw ServiceError("Не удалось загрузить следующую страницу. Попробуйте ещё раз.")
        }
        var merged = request.cursor == nil ? [] : results
        var positions = Dictionary(uniqueKeysWithValues: merged.enumerated().map { ($0.element.id, $0.offset) })
        for record in page.results {
            if let index = positions[record.id] { merged[index] = record }
            else { positions[record.id] = merged.count; merged.append(record) }
        }
        results = merged
        nextCursor = page.hasMore ? page.nextCursor : nil
        hasMore = page.hasMore
        active = nil; isLoading = false; error = nil
        return true
    }

    @discardableResult
    public mutating func fail(_ message: String, for request: MemoryBrowseRequest) -> Bool {
        guard request.generation == generation, active == request else { return false }
        active = nil; isLoading = false; error = message
        return true
    }
}
