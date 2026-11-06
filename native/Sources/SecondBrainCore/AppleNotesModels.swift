import Foundation

public struct AppleNotesFolder: Decodable, Identifiable, Equatable {
    public let id: String
    public let name: String
    public let account: String
    public let path: String
    public let noteCount: Int?
}

public struct AppleNotesFoldersResponse: Decodable {
    public let folders: [AppleNotesFolder]
    public let warnings: [String]?
}

/// Only discovered IDs can enter the selection; replacing a discovery revokes old IDs.
public struct AppleNotesFolderSelection {
    public struct Request: Equatable { fileprivate let revision: Int }
    public private(set) var folders: [AppleNotesFolder] = []
    public private(set) var selectedIds: Set<String> = []
    public private(set) var isLoading = false
    public private(set) var didLoad = false
    private var revision = 0
    public init() {}

    public mutating func invalidate() {
        revision += 1; folders = []; selectedIds = []; isLoading = false; didLoad = false
    }
    public mutating func begin() -> Request? {
        guard !isLoading else { return nil }
        invalidate(); isLoading = true
        return Request(revision: revision)
    }
    @discardableResult public mutating func accept(_ response: AppleNotesFoldersResponse, request: Request) -> Bool {
        guard request.revision == revision, isLoading else { return false }
        folders = response.folders.reduce(into: []) { result, folder in
            if !folder.id.isEmpty && !result.contains(where: { $0.id == folder.id }) { result.append(folder) }
        }
        isLoading = false; didLoad = true
        return true
    }
    @discardableResult public mutating func fail(_ request: Request) -> Bool {
        guard request.revision == revision, isLoading else { return false }
        isLoading = false
        return true
    }
    public mutating func toggle(_ id: String) {
        guard folders.contains(where: { $0.id == id }) else { return }
        if selectedIds.contains(id) { selectedIds = selectedIds.subtracting([id]) }
        else if selectedIds.count < 50 { selectedIds = selectedIds.union([id]) }
    }
    public func filtered(_ query: String) -> [AppleNotesFolder] {
        guard !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return folders }
        return folders.filter { ($0.name + " " + $0.account + " " + $0.path).localizedCaseInsensitiveContains(query) }
    }
}
