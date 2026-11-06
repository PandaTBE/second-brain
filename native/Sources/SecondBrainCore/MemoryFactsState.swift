import Foundation

public struct MemoryFactsRequest: Equatable {
    public let query: String
    public let sourceId: String?
    public let includeHistory: Bool
    public init(query: String = "", sourceId: String? = nil, includeHistory: Bool = false) throws {
        let query = query.trimmingCharacters(in: .whitespacesAndNewlines)
        let source = sourceId?.trimmingCharacters(in: .whitespacesAndNewlines)
        guard query.unicodeScalars.count <= 512, (source?.unicodeScalars.count ?? 0) <= 128,
              source?.rangeOfCharacter(from: .controlCharacters) == nil else { throw ServiceError("Сократите запрос или фильтр источника.") }
        self.query = query; self.sourceId = source?.isEmpty == false ? source : nil
        self.includeHistory = includeHistory
    }
    public static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.includeHistory == rhs.includeHistory && ExactSourceText.equal(lhs.query, rhs.query)
            && ExactSourceText.equal(lhs.sourceId, rhs.sourceId)
    }
    public var queryItems: [URLQueryItem] {
        let base = [URLQueryItem(name: "q", value: query), URLQueryItem(name: "limit", value: "20")]
            + (includeHistory ? [URLQueryItem(name: "include_history", value: "true")] : [])
        return sourceId.map { base + [URLQueryItem(name: "source_id", value: $0)] } ?? base
    }
}

public struct MemoryFactsState {
    public struct Request: Equatable {
        public let parameters: MemoryFactsRequest
        fileprivate let id: UUID
    }
    public private(set) var page: MemoryFactsResponse?
    public private(set) var isLoading = false
    public private(set) var error: String?
    private var active: Request?
    private var current: Request?
    public init() {}
    public var canPerformAction: Bool { !isLoading && page?.facts.isEmpty == false }
    public func isCurrent(_ request: Request) -> Bool { current == request }
    public mutating func invalidate() { page = nil; active = nil; current = nil; isLoading = false; error = nil }
    public mutating func begin(_ parameters: MemoryFactsRequest) -> Request {
        invalidate()
        let request = Request(parameters: parameters, id: UUID())
        active = request; current = request; isLoading = true
        return request
    }
    public mutating func beginRefresh(_ parameters: MemoryFactsRequest) -> Request? {
        guard canPerformAction, current?.parameters == parameters else { return nil }
        return begin(parameters)
    }
    @discardableResult public mutating func accept(_ page: MemoryFactsResponse, request: Request) throws -> Bool {
        guard active == request else { return false }
        guard ExactSourceText.equal(page.query, request.parameters.query),
              (page.includeHistory ?? false) == request.parameters.includeHistory,
              request.parameters.sourceId == nil || page.facts.allSatisfy({ $0.evidence.allSatisfy { $0.sourceId == request.parameters.sourceId } }) else {
            throw ServiceError("Список фактов не соответствует выбранной области.")
        }
        self.page = page; active = nil; isLoading = false; error = nil
        return true
    }
    @discardableResult public mutating func fail(_ message: String, request: Request) -> Bool {
        guard current == request else { return false }
        page = nil; current = nil; active = nil; isLoading = false; error = message
        return true
    }
}
