import Foundation

public enum JSONValue: Decodable {
    case string(String), number(Double), bool(Bool), object([String: JSONValue]), array([JSONValue]), null
    public init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer()
        if value.decodeNil() { self = .null }
        else if let result = try? value.decode(Bool.self) { self = .bool(result) }
        else if let result = try? value.decode(Double.self) { self = .number(result) }
        else if let result = try? value.decode(String.self) { self = .string(result) }
        else if let result = try? value.decode([String: JSONValue].self) { self = .object(result) }
        else { self = .array(try value.decode([JSONValue].self)) }
    }
    public var integer: Int? {
        if case .number(let value) = self, value.isFinite, value >= Double(Int.min), value < Double(Int.max) { return Int(value) }
        return nil
    }
}

public struct MemoryAtom: Decodable, Identifiable {
    public let text: String?
    public let kind: String?
    public let evidence: String?
    public let recordId: String?
    public let revision: Int?
    public let sourceUri: String?
    public var id: String { "\(recordId ?? "")|\(revision ?? 0)|\(kind ?? "")|\(text ?? "")|\(evidence ?? "")" }
}
public struct MemoryCard: Decodable {
    public let title: String?
    public let summary: String?
    public let keywords: [String]?
    public let questions: [String]?
    public let atoms: [MemoryAtom]?
}
public struct MemoryRecord: Decodable, Identifiable {
    public let id: String
    public let revision: Int?
    public let sourceId: String?
    public let sourceKind: String?
    public let sourceObjectId: String?
    public let title: String
    public let text: String?
    public let sourceUri: String?
    public let createdAt: String?
    public let updatedAt: String?
    public let cardStatus: String?
    public let card: MemoryCard?
    public let cardError: String?
    public let cardAttempts: Int?
    public let cardRetryAt: String?
    public let cardWaitingForUser: Bool?
    public let model: String?
    public let promptVersion: String?
    public let snippet: String?
    public let summary: String?
    public let score: Double?
}
public struct MemorySearchResponse: Decodable { public let results: [MemoryRecord] }
public struct MemoryRecordResponse: Decodable { public let record: MemoryRecord }
public struct MemoryJob: Decodable, Identifiable {
    public let id: String
    public let kind: String
    public let phase: String?
    public let status: String
    public let createdAt: String?
    public let startedAt: String?
    public let finishedAt: String?
    public let processed: Int?
    public let total: Int?
    public let concurrency: Int?
    public let inFlight: Int?
    public let stats: [String: JSONValue]?
    public let error: String?
    public var active: Bool { ["queued", "running"].contains(status) }
    public var isCardPhase: Bool {
        if let phase { return phase == "cards" }
        return kind == "cards" || stats?["import"] != nil
    }
    public var title: String { isCardPhase ? "Создание карточек" : "Импорт источников" }
    public var progressLabel: String {
        let unit = isCardPhase ? "материалов" : "источников"
        return "\(processed ?? 0) из \(total ?? 0) \(unit)"
    }
    public var concurrencyLabel: String? {
        guard isCardPhase, let concurrency else { return nil }
        let limit = MemoryCardConcurrency.clamped(concurrency)
        if active, let inFlight {
            return "Одновременно: \(min(max(inFlight, 0), limit)) из \(limit)"
        }
        return "Одновременно: до \(limit)"
    }
    public func importCounter(_ name: String) -> Int {
        let counters: [String: JSONValue]?
        if case .object(let nested)? = stats?["import"] { counters = nested }
        else { counters = stats }
        if name == "errors", case .array(let errors)? = counters?[name] { return errors.count }
        return counters?[name]?.integer ?? 0
    }
}
public struct MemoryScheduler: Decodable {
    public let enabled: Bool
    public let intervalMinutes: Int
    public let processCards: Bool
    public let batchLimit: Int
    public let cardConcurrency: Int?
    public let autoImportEnabled: Bool?
    public let paused: Bool?
    public let pollSeconds: Int?
    public let quietSeconds: Int?
    public let nextRunAt: String?
    public let lastAutoCheckAt: String?
    public var request: MemoryScheduleRequest {
        MemoryScheduleRequest(enabled: enabled, intervalMinutes: intervalMinutes, processCards: processCards, batchLimit: batchLimit, autoImportEnabled: autoImportEnabled ?? true, paused: paused ?? false, cardConcurrency: cardConcurrency ?? MemoryCardConcurrency.defaultValue)
    }
}
public struct MemoryStatus: Decodable {
    public let records: Int
    public let cardsPending: Int
    public let cardsReady: Int
    public let cardsError: Int
    public let cardsRetryPending: Int?
    public let cardsWaitingUser: Int?
    public let jobs: [MemoryJob]
    public let scheduler: MemoryScheduler
    public let schedulerRunning: Bool
    public let providerBackoff: MemoryProviderBackoff?
    public var busy: Bool { jobs.contains { $0.active } }
}
public struct MemoryProviderBackoff: Decodable {
    public let blocked: Bool
    public let retryAt: String?
    public let requiresUserAction: Bool
    public let message: String?
    public let effectiveConcurrency: Int
    public let configuredConcurrency: Int

    public var concurrencyLabel: String? {
        guard effectiveConcurrency < configuredConcurrency else { return nil }
        return "Сейчас одновременно: до \(MemoryCardConcurrency.clamped(effectiveConcurrency)) · В настройках: \(MemoryCardConcurrency.clamped(configuredConcurrency))"
    }

    public var retryDate: Date? {
        guard let retryAt else { return nil }
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = parser.date(from: retryAt) { return date }
        parser.formatOptions = [.withInternetDateTime]
        return parser.date(from: retryAt)
    }
}
public struct MemoryProviderResumeRequest: Encodable { public init() {} }
public struct MemoryProviderResumeResponse: Decodable { public let providerBackoff: MemoryProviderBackoff }
public struct MemoryJobResponse: Decodable { public let jobId: String }
public struct MemoryImportRequest: Encodable {
    public let sourceId: String?
    public init(sourceId: String? = nil) { self.sourceId = sourceId }
}
public struct MemoryCardsRequest: Encodable {
    public let limit: Int
    public let retryErrors: Bool
    public init(limit: Int, retryErrors: Bool = false) { self.limit = MemoryProcessingLimit.clamped(limit); self.retryErrors = retryErrors }
}
public struct MemoryScheduleRequest: Codable, Equatable {
    public var enabled: Bool
    public var intervalMinutes: Int
    public var processCards: Bool
    public var batchLimit: Int
    public var cardConcurrency: Int
    public var autoImportEnabled: Bool
    public var paused: Bool
    public init(enabled: Bool = false, intervalMinutes: Int = 15, processCards: Bool = false, batchLimit: Int = 10, autoImportEnabled: Bool = true, paused: Bool = false, cardConcurrency: Int = MemoryCardConcurrency.defaultValue) {
        self.enabled = enabled; self.intervalMinutes = intervalMinutes; self.processCards = processCards
        self.batchLimit = MemoryProcessingLimit.clamped(batchLimit); self.autoImportEnabled = autoImportEnabled
        self.paused = paused
        self.cardConcurrency = MemoryCardConcurrency.clamped(cardConcurrency)
    }
    public func applyingSynchronizationPause(_ paused: Bool) -> Self {
        var updated = self
        updated.paused = paused
        return updated
    }
}
public struct MemorySyncRequest: Encodable {
    public let paused: Bool
    public init(paused: Bool) { self.paused = paused }
}
public struct MemoryScheduleResponse: Decodable { public let scheduler: MemoryScheduler }
public struct MCPConfiguration: Decodable {
    public let command: String
    public let args: [String]
    public let claudeJson: String
    public let codexToml: String
    public let instructions: String
}
public enum MemoryCardConcurrency {
    public static let defaultValue = 10
    public static func clamped(_ value: Int) -> Int { max(value, 1) }
    public static func parse(_ text: String) -> Int? {
        guard let value = Int(text.trimmingCharacters(in: .whitespacesAndNewlines)), value >= 1 else { return nil }
        return value
    }
}

public enum MemoryProcessingLimit {
    public static let range = 1...50
    public static func clamped(_ value: Int) -> Int { min(max(value, range.lowerBound), range.upperBound) }
}
