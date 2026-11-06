import Foundation

public enum MemoryFactStatus: String, Decodable {
    case sourceSupported = "source_supported", candidate
    case possibleConflict = "possible_conflict", explicitSupersedes = "explicit_supersedes"
    public init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer().decode(String.self)
        self = Self(rawValue: value) ?? .candidate
    }
    public var title: String {
        switch self {
        case .sourceSupported: "Подтверждается источниками"
        case .candidate: "Кандидат — требует проверки"
        case .possibleConflict: "Возможный конфликт"
        case .explicitSupersedes: "Явно заменяет предыдущее решение"
        }
    }
}

public struct MemoryFactProof: Decodable, Identifiable {
    public let citationId: String
    public let recordId: String
    public let revision: Int
    public let sourceId: String
    public let sourceKind: String
    public let sourceObjectId: String?
    public let title: String
    public let sourceUri: String
    public let start: Int
    public let end: Int
    public let quote: String
    public let contextBefore: String?
    public let contextAfter: String?
    public let dates: EvidenceDates
    public let active: Bool?
    public let supportStatus: MemoryFactStatus?
    public var id: String { citationId }
    var valid: Bool { revision > 0 && start >= 0 && end > start && end - start == quote.unicodeScalars.count }
    public func sameIdentity(as other: Self) -> Bool {
        revision == other.revision && start == other.start && end == other.end
            && ExactSourceText.equal(citationId, other.citationId) && ExactSourceText.equal(recordId, other.recordId)
            && ExactSourceText.equal(sourceId, other.sourceId) && ExactSourceText.equal(sourceKind, other.sourceKind)
            && ExactSourceText.equal(sourceObjectId, other.sourceObjectId) && ExactSourceText.equal(sourceUri, other.sourceUri)
            && ExactSourceText.equal(quote, other.quote)
    }
    /// Use only for current proofs. A historical quote remains a saved snapshot.
    public func matchesCurrent(_ record: MemoryRecord) -> Bool {
        guard active != false, valid, revision == record.revision, ExactSourceText.equal(recordId, record.id),
              ExactSourceText.equal(sourceId, record.sourceId), ExactSourceText.equal(sourceKind, record.sourceKind),
              ExactSourceText.equal(sourceObjectId, record.sourceObjectId), ExactSourceText.equal(sourceUri, record.sourceUri),
              let text = record.text else { return false }
        let scalars = text.unicodeScalars
        guard let first = scalars.index(scalars.startIndex, offsetBy: start, limitedBy: scalars.endIndex),
              let last = scalars.index(first, offsetBy: end - start, limitedBy: scalars.endIndex) else { return false }
        let before = (contextBefore ?? "").unicodeScalars
        let after = (contextAfter ?? "").unicodeScalars
        guard let beforeIndex = scalars.index(first, offsetBy: -before.count, limitedBy: scalars.startIndex),
              let afterIndex = scalars.index(last, offsetBy: after.count, limitedBy: scalars.endIndex) else { return false }
        return quote.unicodeScalars.elementsEqual(scalars[first..<last])
            && before.elementsEqual(scalars[beforeIndex..<first]) && after.elementsEqual(scalars[last..<afterIndex])
    }
}

public struct MemoryFactRelation: Decodable {
    public let kind: String
    public let targetId: String
    public let targetText: String
    public let suggestion: Bool
    public let historical: Bool?
    public let evidence: MemoryFactProof
    public var title: String {
        switch kind {
        case "possible_conflict": "Возможный конфликт"
        case "explicit_supersedes", "supersedes": suggestion ? "Предложенная связь замены" : "Явно заменяет предыдущее решение"
        default: "Связь с другим утверждением"
        }
    }
}

public struct MemoryFact: Decodable, Identifiable {
    public let id: String
    public let kind: String
    public let text: String
    public let status: MemoryFactStatus
    public let evidence: [MemoryFactProof]
    public let relations: [MemoryFactRelation]
    public let historicalOnly: Bool?
    public var isHistoricalOnly: Bool { historicalOnly == true }
    public var kindTitle: String {
        switch kind { case "decision": "Решение"; case "fact": "Факт"; case "preference": "Предпочтение"; case "constraint": "Ограничение"; default: "Утверждение" }
    }
}

public struct MemoryFactsResponse: Decodable {
    public let schemaVersion: Int
    public let query: String
    public let includeHistory: Bool?
    public let facts: [MemoryFact]
    public let historySince: Date
    public let untrustedContent: Bool
    public let truncated: Bool
    private enum CodingKeys: String, CodingKey { case schemaVersion, query, includeHistory, facts, historySince, untrustedContent, truncated }
    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try values.decode(Int.self, forKey: .schemaVersion)
        query = try values.decode(String.self, forKey: .query)
        includeHistory = try values.decodeIfPresent(Bool.self, forKey: .includeHistory)
        facts = try values.decode([MemoryFact].self, forKey: .facts)
        untrustedContent = try values.decode(Bool.self, forKey: .untrustedContent)
        truncated = try values.decode(Bool.self, forKey: .truncated)
        let since = try values.decode(String.self, forKey: .historySince)
        guard let date = EvidenceDateCodec.timestamp(since), schemaVersion == 1, untrustedContent,
              facts.count <= 20, Set(facts.map(\.id)).count == facts.count,
              facts.allSatisfy({ $0.evidence.allSatisfy(\.valid) && $0.relations.allSatisfy { $0.evidence.valid } }) else {
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: "Invalid facts packet"))
        }
        historySince = date
    }
}

public struct FactsProjectionRequest: Encodable {
    public let limit: Int
    public init() { limit = 100 }
}
