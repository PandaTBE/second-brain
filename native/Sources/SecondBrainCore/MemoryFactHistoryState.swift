import Foundation

public struct MemoryFactHistoryState {
    public struct Request: Equatable {
        public let factId: String
        public let before: String?
        fileprivate let append: Bool
        fileprivate let id: UUID
    }
    private struct Origin { let before: String? }
    public private(set) var page: MemoryFactHistoryResponse?
    public private(set) var events: [MemoryFactHistoryEvent] = []
    public private(set) var isLoading = false
    public private(set) var error: String?
    public private(set) var nextCursor: String?
    private var origins: [String: Origin] = [:]
    private var factId: String?
    private var current: Request?
    private var active: Request?
    public init() {}
    public func isCurrent(_ request: Request) -> Bool { current == request }
    public func before(for eventId: String) -> String? { origins[eventId]?.before }
    public mutating func invalidate() {
        page = nil; events = []; origins = [:]; factId = nil; current = nil; active = nil
        isLoading = false; error = nil; nextCursor = nil
    }
    public mutating func begin(factId: String) -> Request { begin(factId: factId, before: nil) }
    private mutating func begin(factId: String, before: String?) -> Request {
        invalidate(); self.factId = factId
        let request = Request(factId: factId, before: before, append: false, id: UUID())
        current = request; active = request; isLoading = true
        return request
    }
    public mutating func beginNextPage() -> Request? {
        guard !isLoading, let factId, let cursor = nextCursor else { return nil }
        let request = Request(factId: factId, before: cursor, append: true, id: UUID())
        current = request; active = request; isLoading = true; error = nil
        return request
    }
    public mutating func beginRefresh(eventId: String) -> Request? {
        guard !isLoading, events.contains(where: { $0.id == eventId }), let origin = origins[eventId], let factId else { return nil }
        return begin(factId: factId, before: origin.before)
    }
    @discardableResult public mutating func accept(_ page: MemoryFactHistoryResponse, request: Request) throws -> Bool {
        guard active == request else { return false }
        guard page.fact.id == request.factId, page.nextCursor == nil || (page.nextCursor?.isEmpty == false && page.nextCursor != request.before) else {
            throw ServiceError("Не удалось продолжить историю выбранного факта.")
        }
        var result = request.append ? events : []
        var updatedOrigins = request.append ? origins : [:]
        for event in page.events {
            if let index = result.firstIndex(where: { $0.id == event.id }) { result[index] = event }
            else { result.append(event) }
            updatedOrigins[event.id] = Origin(before: request.before)
        }
        events = result; origins = updatedOrigins; self.page = page; nextCursor = page.nextCursor
        active = nil; isLoading = false; error = nil
        return true
    }
    @discardableResult public mutating func fail(_ message: String, request: Request) -> Bool {
        guard current == request else { return false }
        page = nil; events = []; origins = [:]; current = nil; active = nil; nextCursor = nil
        isLoading = false; error = message
        return true
    }
}
