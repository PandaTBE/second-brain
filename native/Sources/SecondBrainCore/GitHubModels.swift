import Foundation

public struct GitHubRepository: Decodable, Identifiable, Equatable {
    public let id: Int
    public let fullName: String
    public let description: String?
    public let `private`: Bool
    public let defaultBranch: String
    public let url: String
}

public struct GitHubRepositoriesResponse: Decodable {
    public let repositories: [GitHubRepository]
    public let nextPage: Int?
    public init(repositories: [GitHubRepository], nextPage: Int?) {
        self.repositories = repositories; self.nextPage = nextPage
    }
}

public struct GitHubRepositoriesRequest: Encodable {
    public let token: String
    public let page: Int
    public init(token: String, page: Int = 1) { self.token = token; self.page = page }
}

public enum GitHubImportContent: String, CaseIterable {
    case docs, issues, pullRequests = "pull_requests"
    public var title: String {
        switch self {
        case .docs: "Документация Markdown"
        case .issues: "Issues"
        case .pullRequests: "Pull requests"
        }
    }
}

/// Candidate credentials never enter this state; changing the token invalidates its scope.
public struct GitHubRepositorySelection {
    public struct Request: Equatable {
        let revision: Int
        let sequence: Int
        public let page: Int
    }
    public private(set) var repositories: [GitHubRepository] = []
    public private(set) var selectedIds: Set<Int> = []
    public private(set) var nextPage: Int?
    public private(set) var didLoad = false
    private var revision = 0
    private var sequence = 0
    private var activeRequest: Request?
    public init() {}
    public var isLoading: Bool { activeRequest != nil }
    public var selectedNames: [String] {
        repositories.filter { selectedIds.contains($0.id) }.map(\.fullName).sorted()
    }

    public func filteredRepositories(query: String) -> [GitHubRepository] {
        let query = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return repositories }
        return repositories.filter {
            $0.fullName.localizedCaseInsensitiveContains(query)
            || ($0.description?.localizedCaseInsensitiveContains(query) ?? false)
        }
    }

    public mutating func invalidate() {
        revision += 1; repositories = []; selectedIds = []; nextPage = nil
        activeRequest = nil; didLoad = false
    }

    public mutating func begin(continuation: Bool = false) -> Request? {
        guard !isLoading, !continuation || nextPage != nil else { return nil }
        let page: Int
        if continuation {
            guard let nextPage else { return nil }
            page = nextPage
        } else {
            page = 1
        }
        if !continuation { invalidate() }
        sequence += 1
        let request = Request(revision: revision, sequence: sequence, page: page)
        activeRequest = request
        return request
    }

    @discardableResult
    public mutating func accept(_ response: GitHubRepositoriesResponse, request: Request?) -> Bool {
        guard let request, request == activeRequest else { return false }
        let known = Set(repositories.map(\.id))
        let additions = response.repositories.reduce(into: [GitHubRepository]()) { result, row in
            if !known.contains(row.id) && !result.contains(where: { $0.id == row.id }) {
                result.append(row)
            }
        }
        repositories = repositories + additions
        nextPage = response.nextPage.flatMap { $0 > request.page ? $0 : nil }
        activeRequest = nil; didLoad = true
        return true
    }

    @discardableResult
    public mutating func fail(request: Request?) -> Bool {
        guard let request, request == activeRequest else { return false }
        activeRequest = nil
        return true
    }

    public mutating func toggle(_ id: Int) {
        guard repositories.contains(where: { $0.id == id }) else { return }
        if selectedIds.contains(id) { selectedIds = selectedIds.subtracting([id]) }
        else if selectedIds.count < 20 { selectedIds = selectedIds.union([id]) }
    }
}
