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
    public let query: String
    public let mode: MemorySearchMode
    public let filter: MemoryBrowseFilter
    fileprivate let generation: UUID
    fileprivate let id: UUID
}

public struct MemoryBrowseState {
    public private(set) var results: [MemoryRecord] = []
    public private(set) var nextCursor: String?
    public private(set) var hasMore = false
    public private(set) var isLoading = false
    public private(set) var error: String?
    public private(set) var search: MemorySearchDiagnostics?
    private var scopeQuery = ""
    private var scopeMode: MemorySearchMode = .lexical
    private var scopeFilter: MemoryBrowseFilter = .all
    private var generation = UUID()
    private var active: MemoryBrowseRequest?

    public init() {}

    public mutating func invalidate() {
        generation = UUID()
        results = []; nextCursor = nil; hasMore = false
        active = nil; isLoading = false; error = nil; search = nil
    }

    public mutating func beginFirstPage(query: String = "", mode: MemorySearchMode = .lexical, filter: MemoryBrowseFilter = .all) -> MemoryBrowseRequest {
        invalidate()
        scopeQuery = query
        scopeMode = query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? .lexical : mode
        scopeFilter = scopeMode == .lexical ? filter : .all
        let request = MemoryBrowseRequest(cursor: nil, query: scopeQuery, mode: scopeMode, filter: scopeFilter, generation: generation, id: UUID())
        active = request; isLoading = true
        return request
    }

    public mutating func beginNextPage() -> MemoryBrowseRequest? {
        guard !isLoading, hasMore, let cursor = nextCursor else { return nil }
        let request = MemoryBrowseRequest(cursor: cursor, query: scopeQuery, mode: scopeMode, filter: scopeFilter, generation: generation, id: UUID())
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
    public mutating func acceptSearch(_ response: MemorySearchResponse, for request: MemoryBrowseRequest) throws -> Bool {
        guard request.generation == generation, active == request, request.cursor == nil else { return false }
        if let diagnostics = response.search, diagnostics.requestedMode != request.mode {
            throw ServiceError("Режим ответа не соответствует запросу. Повторите поиск.")
        }
        let page = MemoryBrowseResponse(results: response.results, nextCursor: nil, hasMore: false)
        let accepted = try accept(page, for: request)
        if accepted { search = response.search }
        return accepted
    }

    @discardableResult
    public mutating func fail(_ message: String, for request: MemoryBrowseRequest) -> Bool {
        guard request.generation == generation, active == request else { return false }
        active = nil; isLoading = false; error = message
        return true
    }
}
