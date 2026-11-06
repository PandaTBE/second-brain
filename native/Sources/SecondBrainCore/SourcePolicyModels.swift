import Foundation

public enum SourcePermission: String, CaseIterable, Identifiable {
    case importMaterials, localSearch, mcp, localModel, externalModel
    public var id: String { rawValue }
    public var title: String {
        switch self {
        case .importMaterials: "Импорт новых материалов"
        case .localSearch: "Поиск в приложении"
        case .mcp: "Доступ через MCP"
        case .localModel: "Обработка локальной моделью"
        case .externalModel: "Обработка внешней моделью"
        }
    }
}

/// Permissions are independent. A missing legacy policy preserves existing behavior;
/// it never enables a model queue or configures a provider.
public struct SourcePolicy: Codable, Equatable, Hashable {
    public let importEnabled: Bool
    public let localSearchEnabled: Bool
    public let mcpEnabled: Bool
    public let localModelEnabled: Bool
    public let externalModelEnabled: Bool
    public let excludedPaths: [String]
    public let excludedNotesFolderIds: [String]

    public init(importEnabled: Bool = true, localSearchEnabled: Bool = true,
                mcpEnabled: Bool = true, localModelEnabled: Bool = true,
                externalModelEnabled: Bool = true, excludedPaths: [String] = [],
                excludedNotesFolderIds: [String] = []) {
        self.importEnabled = importEnabled; self.localSearchEnabled = localSearchEnabled
        self.mcpEnabled = mcpEnabled; self.localModelEnabled = localModelEnabled
        self.externalModelEnabled = externalModelEnabled
        self.excludedPaths = excludedPaths; self.excludedNotesFolderIds = excludedNotesFolderIds
    }

    private enum CodingKeys: String, CodingKey {
        case importEnabled, localSearchEnabled, mcpEnabled, localModelEnabled,
             externalModelEnabled, excludedPaths, excludedNotesFolderIds
    }
    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        self.init(importEnabled: try values.decodeIfPresent(Bool.self, forKey: .importEnabled) ?? true,
                  localSearchEnabled: try values.decodeIfPresent(Bool.self, forKey: .localSearchEnabled) ?? true,
                  mcpEnabled: try values.decodeIfPresent(Bool.self, forKey: .mcpEnabled) ?? true,
                  localModelEnabled: try values.decodeIfPresent(Bool.self, forKey: .localModelEnabled) ?? true,
                  externalModelEnabled: try values.decodeIfPresent(Bool.self, forKey: .externalModelEnabled) ?? true,
                  excludedPaths: try values.decodeIfPresent([String].self, forKey: .excludedPaths) ?? [],
                  excludedNotesFolderIds: try values.decodeIfPresent([String].self, forKey: .excludedNotesFolderIds) ?? [])
    }

    public func allows(_ permission: SourcePermission) -> Bool {
        switch permission {
        case .importMaterials: importEnabled
        case .localSearch: localSearchEnabled
        case .mcp: mcpEnabled
        case .localModel: localModelEnabled
        case .externalModel: externalModelEnabled
        }
    }

    public func setting(_ permission: SourcePermission, allowed: Bool) -> Self {
        Self(importEnabled: permission == .importMaterials ? allowed : importEnabled,
             localSearchEnabled: permission == .localSearch ? allowed : localSearchEnabled,
             mcpEnabled: permission == .mcp ? allowed : mcpEnabled,
             localModelEnabled: permission == .localModel ? allowed : localModelEnabled,
             externalModelEnabled: permission == .externalModel ? allowed : externalModelEnabled,
             excludedPaths: excludedPaths, excludedNotesFolderIds: excludedNotesFolderIds)
    }

    public func excluding(paths: [String], notesFolderIDs: [String]) throws -> Self {
        guard paths.count <= 100, notesFolderIDs.count <= 50 else {
            throw ServiceError("Допускается до 100 исключений путей и до 50 папок Заметок.")
        }
        let normalized = try paths.map { raw in
            var path = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            if path.hasSuffix("/") { path = String(path.dropLast()) }
            let parts = path.split(separator: "/", omittingEmptySubsequences: false)
            guard !path.isEmpty, path.count <= 2048, !path.hasPrefix("/"),
                  path.rangeOfCharacter(from: .controlCharacters) == nil,
                  path.rangeOfCharacter(from: CharacterSet(charactersIn: "\\*?[]")) == nil,
                  parts.allSatisfy({ !$0.isEmpty && $0 != "." && $0 != ".." }) else {
                throw ServiceError("Укажите относительные пути внутри источника: без .., масок и управляющих символов.")
            }
            return path
        }
        guard notesFolderIDs.allSatisfy({ !$0.isEmpty && $0.count <= 2048 && $0.rangeOfCharacter(from: .controlCharacters) == nil }) else {
            throw ServiceError("Недопустимый идентификатор папки Заметок.")
        }
        func unique(_ values: [String]) -> [String] {
            values.reduce([]) { result, value in result.contains(value) ? result : result + [value] }
        }
        return Self(importEnabled: importEnabled, localSearchEnabled: localSearchEnabled,
                    mcpEnabled: mcpEnabled, localModelEnabled: localModelEnabled,
                    externalModelEnabled: externalModelEnabled, excludedPaths: unique(normalized),
                    excludedNotesFolderIds: unique(notesFolderIDs))
    }
}

public struct SourcePolicyResponse: Decodable { public let policy: SourcePolicy }
public struct SourcePolicyPreview: Decodable {
    public let affectedDocuments: Int
    public let samples: [Sample]
    public let sampleLimit: Int
    public struct Sample: Decodable, Identifiable {
        public let id: String
        public let title: String
        public let sourceUri: String?
    }
}

/// Request generations bind an exclusion preview to its exact policy draft.
public struct SourcePolicyPreviewState {
    public struct Request: Equatable { fileprivate let revision: Int }
    public private(set) var result: SourcePolicyPreview?
    public private(set) var isLoading = false
    private var revision = 0
    private var pendingPolicy: SourcePolicy?
    private var acceptedPolicy: SourcePolicy?
    public init() {}

    public mutating func invalidate() {
        revision += 1; pendingPolicy = nil; acceptedPolicy = nil; result = nil; isLoading = false
    }
    public mutating func begin(_ policy: SourcePolicy) -> Request {
        invalidate(); pendingPolicy = policy; isLoading = true
        return Request(revision: revision)
    }
    @discardableResult public mutating func accept(_ result: SourcePolicyPreview, request: Request, policy: SourcePolicy) -> Bool {
        guard request.revision == revision, isLoading, pendingPolicy == policy else { return false }
        self.result = result; acceptedPolicy = policy; pendingPolicy = nil; isLoading = false
        return true
    }
    @discardableResult public mutating func fail(_ request: Request) -> Bool {
        guard request.revision == revision, isLoading else { return false }
        pendingPolicy = nil; isLoading = false
        return true
    }
    public func canSave(_ policy: SourcePolicy, original: SourcePolicy) -> Bool {
        (policy.excludedPaths == original.excludedPaths && policy.excludedNotesFolderIds == original.excludedNotesFolderIds)
            || (acceptedPolicy == policy && result != nil && !isLoading)
    }
}
