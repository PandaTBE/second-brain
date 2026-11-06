import Foundation

public struct EmbeddingSettings: Decodable {
    public let enabled: Bool
    public let modelId: String?
    public let digest: String?
    public let dimension: Int?
    public let generation: String?
    public let prepVersion: Int
    public let ready: Int
    public let pending: Int
    public let errors: Int
    public let message: String?
}

/// First successful load initializes the draft, including recovery after a failed load.
/// Subsequent status polls update progress while preserving the user's edits.
public struct EmbeddingSettingsState {
    public private(set) var settings: EmbeddingSettings?
    public var enabled = false
    public var selection = EmbeddingModelSelection()
    public init() {}
    public mutating func acceptStatus(_ state: EmbeddingSettings) {
        if settings == nil {
            enabled = state.enabled
            selection = EmbeddingModelSelection(selectedId: state.modelId)
        }
        settings = state
    }
    public mutating func acceptSaved(_ state: EmbeddingSettings) {
        settings = state; enabled = state.enabled
        selection = EmbeddingModelSelection(selectedId: state.modelId)
    }
}

public struct EmbeddingSettingsRequest: Encodable {
    public let enabled: Bool
    public let modelId: String?
    public init(enabled: Bool, modelId: String?) { self.enabled = enabled; self.modelId = modelId }
}

public struct EmbeddingModel: Decodable, Identifiable, Equatable {
    public let id: String
    public let name: String
    public let digest: String
}
public struct EmbeddingModelsResponse: Decodable {
    public let models: [EmbeddingModel]
    public let message: String?
}
public struct EmbeddingIndexRequest: Encodable {
    public let limit: Int
    public init(limit: Int = 8) { self.limit = limit }
}
public struct EmbeddingIndexResponse: Decodable { public let jobId: String }

/// Discovery does not choose or enable a model. Saving still requires the backend probe.
public struct EmbeddingModelSelection {
    public struct Request: Equatable { fileprivate let revision: Int }
    public private(set) var models: [EmbeddingModel] = []
    public private(set) var selectedId: String?
    public private(set) var isLoading = false
    public private(set) var didLoad = false
    private var revision = 0
    public init(selectedId: String? = nil) { self.selectedId = selectedId }
    public var canEnable: Bool {
        !isLoading && models.contains { $0.id == selectedId && !$0.digest.isEmpty }
    }
    public mutating func invalidate() {
        revision += 1; models = []; isLoading = false; didLoad = false
    }
    public mutating func begin() -> Request {
        invalidate(); isLoading = true
        return Request(revision: revision)
    }
    @discardableResult public mutating func accept(_ response: EmbeddingModelsResponse, request: Request) -> Bool {
        guard request.revision == revision, isLoading else { return false }
        models = response.models.reduce([]) { result, model in
            guard !model.id.isEmpty, !model.digest.isEmpty, !result.contains(where: { $0.id == model.id }) else { return result }
            return result + [model]
        }
        isLoading = false; didLoad = true
        return true
    }
    @discardableResult public mutating func fail(_ request: Request) -> Bool {
        guard request.revision == revision, isLoading else { return false }
        isLoading = false
        return true
    }
    @discardableResult public mutating func select(_ id: String) -> Bool {
        guard !isLoading, models.contains(where: { $0.id == id && !$0.digest.isEmpty }) else { return false }
        selectedId = id
        return true
    }
}

public enum MemorySearchMode: String, CaseIterable, Identifiable, Decodable {
    case lexical, semantic, hybrid
    public static let rankedResultLimit = 20
    public var id: String { rawValue }
    public var title: String {
        switch self {
        case .lexical: "По словам"
        case .semantic: "По смыслу"
        case .hybrid: "Гибридный"
        }
    }
}

public enum MemorySearchExecutionMode: String, Decodable {
    case lexical, semantic, hybrid, record
    public var title: String {
        switch self {
        case .lexical: "По словам"
        case .semantic: "По смыслу"
        case .hybrid: "Гибридный"
        case .record: "Контекст выбранного материала"
        }
    }
}

public struct MemorySearchDiagnostics: Decodable {
    public let requestedMode: MemorySearchMode
    public let usedMode: MemorySearchExecutionMode
    public let fallbackReason: String?
    public let generation: String?
    public let queryEmbeddingMs: Double?
    public let vectorSearchMs: Double?
    public let eligible: Int
    public let ready: Int
    public let partial: Bool
    public var message: String {
        var result = "Использован режим: \(usedMode.title.lowercased())."
        if usedMode != .record && requestedMode.rawValue != usedMode.rawValue { result += " Запрошен: \(requestedMode.title.lowercased())." }
        if partial { result += " Индекс готов частично: \(ready) из \(eligible)." }
        return result
    }
    public var fallbackMessage: String? {
        guard fallbackReason != nil else { return nil }
        switch fallbackReason {
        case "embeddings_disabled": return "Семантический индекс выключен. Включите его в настройках поиска."
        case "index_empty", "no_ready_embeddings": return "Семантический индекс ещё не готов. Показаны совпадения по словам."
        case "local_model_unavailable", "embedding_unavailable": return "Ollama или выбранная локальная модель недоступны. Показаны совпадения по словам."
        default: return "Семантический поиск сейчас недоступен. Показаны совпадения по словам."
        }
    }
}
