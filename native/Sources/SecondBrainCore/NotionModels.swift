import Foundation

public struct NotionPage: Decodable, Identifiable, Equatable {
    public let id: String
    public let title: String
    public let url: String
}

public struct NotionPagesResponse: Decodable {
    public let pages: [NotionPage]
    public let nextCursor: String?
    public init(pages: [NotionPage], nextCursor: String?) {
        self.pages = pages; self.nextCursor = nextCursor
    }
}

public struct NotionPagesRequest: Encodable {
    public let token: String
    public let query: String
    public let cursor: String?
    public init(token: String, query: String, cursor: String?) {
        self.token = token; self.query = query; self.cursor = cursor
    }
}

/// Page discovery is tied to the token/query revision; late responses cannot widen its scope.
public struct NotionPageSelection {
    public struct Request: Equatable {
        let revision: Int
        public let cursor: String?
    }
    public private(set) var pages: [NotionPage] = []
    public private(set) var selectedIds: Set<String> = []
    public private(set) var nextCursor: String?
    public private(set) var isLoading = false
    public private(set) var didLoad = false
    private var revision = 0
    public init() {}

    public mutating func invalidate() {
        revision += 1; pages = []; selectedIds = []; nextCursor = nil
        isLoading = false; didLoad = false
    }

    public mutating func begin(continuation: Bool = false) -> Request? {
        guard !isLoading, !continuation || nextCursor != nil else { return nil }
        if !continuation { invalidate() }
        isLoading = true
        return Request(revision: revision, cursor: continuation ? nextCursor : nil)
    }

    @discardableResult
    public mutating func accept(_ response: NotionPagesResponse, request: Request?) -> Bool {
        guard let request, request.revision == revision, isLoading else { return false }
        let priorIds = Set(pages.map(\.id))
        let additions = response.pages.reduce(into: [NotionPage]()) { result, page in
            if !priorIds.contains(page.id) && !result.contains(where: { $0.id == page.id }) { result.append(page) }
        }
        pages = pages + additions
        nextCursor = response.nextCursor == request.cursor ? nil : response.nextCursor
        isLoading = false; didLoad = true
        return true
    }

    @discardableResult
    public mutating func fail(request: Request?) -> Bool {
        guard let request, request.revision == revision, isLoading else { return false }
        isLoading = false
        return true
    }

    public mutating func toggle(_ id: String) {
        guard pages.contains(where: { $0.id == id }) else { return }
        if selectedIds.contains(id) { selectedIds = selectedIds.subtracting([id]) }
        else if selectedIds.count < 100 { selectedIds = selectedIds.union([id]) }
    }
}
