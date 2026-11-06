import Foundation

public struct EvidenceCitation: Decodable, Identifiable {
    public let citationId: String
    public let recordId: String
    public let sourceId: String
    public let sourceKind: String
    public let revision: Int
    public let title: String
    public let sourceUri: String
    public let sourceObjectId: String?
    public let startChar: Int
    public let endChar: Int
    public let quote: String
    public let contextBefore: String
    public let contextAfter: String
    public let dates: EvidenceDates
    public let quoteSelection: String?
    public let quoteRelevance: String?
    public var uncertaintyMessage: String? { quoteRelevance == "uncertain" ? "Фрагмент требует проверки" : nil }
    public var id: String { citationId }
    var hasValidOffsets: Bool {
        revision > 0 && startChar >= 0 && endChar > startChar
            && endChar - startChar == quote.unicodeScalars.count
            && !citationId.isEmpty && !recordId.isEmpty && !sourceId.isEmpty
    }

    public func matches(record: MemoryRecord) -> Bool {
        guard MemorySourceReference(citation: self).matches(record), let text = record.text,
              let revision = record.revision else { return false }
        return matches(text: text, recordId: record.id, revision: revision)
    }

    /// Python offsets count Unicode codepoints. String equality in Swift is canonically
    /// equivalent, so compare scalars directly to preserve exact source evidence.
    public func matches(text: String, recordId: String, revision: Int) -> Bool {
        guard hasValidOffsets, self.recordId == recordId, self.revision == revision else { return false }
        let scalars = text.unicodeScalars
        guard let start = scalars.index(scalars.startIndex, offsetBy: startChar, limitedBy: scalars.endIndex),
              let end = scalars.index(start, offsetBy: endChar - startChar, limitedBy: scalars.endIndex),
              let before = scalars.index(start, offsetBy: -contextBefore.unicodeScalars.count, limitedBy: scalars.startIndex),
              let after = scalars.index(end, offsetBy: contextAfter.unicodeScalars.count, limitedBy: scalars.endIndex) else { return false }
        return quote.unicodeScalars.elementsEqual(scalars[start..<end])
            && contextBefore.unicodeScalars.elementsEqual(scalars[before..<start])
            && contextAfter.unicodeScalars.elementsEqual(scalars[end..<after])
    }
}

public struct EvidenceBundle: Decodable {
    public let schemaVersion: Int
    public let query: String
    public let citations: [EvidenceCitation]
    public let search: MemorySearchDiagnostics?
    public let incomplete: Bool
    public let reasons: [String]
    public let budgetChars: Int
    public let usedChars: Int
    public let untrustedContent: Bool
    public let notice: String
    private let packet: EvidenceJSON
    public var incompletenessMessages: [String] {
        reasons.reduce([]) { result, reason in
            let message: String
            switch reason {
            case "ambiguous_card_evidence": message = "Карточка содержит несколько возможных подтверждений."
            case "unlocalized_evidence": message = "Не удалось точно определить релевантный фрагмент."
            case "no_matches": message = "Подходящих доказательств не найдено."
            case "source_changed": message = "Часть материалов изменилась во время поиска."
            case "partial_index": message = "Семантический индекс готов частично."
            case "search_fallback": message = "Использован доступный режим поиска."
            case "candidate_limit": message = "Показана часть подходящих материалов."
            case "metadata_too_large", "budget_exhausted": message = "Часть доказательств не поместилась в выбранный бюджет."
            default: message = "Часть доказательств не включена в пакет."
            }
            return result.contains(message) ? result : result + [message]
        }
    }
    private enum CodingKeys: String, CodingKey {
        case schemaVersion, query, citations, search, incomplete, reasons, budgetChars, usedChars, untrustedContent, notice
    }
    public init(from decoder: Decoder) throws {
        packet = try EvidenceJSON(from: decoder)
        let values = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try values.decode(Int.self, forKey: .schemaVersion)
        query = try values.decode(String.self, forKey: .query)
        citations = try values.decode([EvidenceCitation].self, forKey: .citations)
        search = try values.decodeIfPresent(MemorySearchDiagnostics.self, forKey: .search)
        incomplete = try values.decode(Bool.self, forKey: .incomplete)
        reasons = try values.decode([String].self, forKey: .reasons)
        budgetChars = try values.decode(Int.self, forKey: .budgetChars)
        usedChars = try values.decode(Int.self, forKey: .usedChars)
        untrustedContent = try values.decode(Bool.self, forKey: .untrustedContent)
        notice = try values.decode(String.self, forKey: .notice)
        guard schemaVersion == 1, (2000...32000).contains(budgetChars), (0...budgetChars).contains(usedChars),
              untrustedContent, citations.count <= 20, citations.allSatisfy(\.hasValidOffsets),
              Set(citations.map(\.citationId)).count == citations.count else {
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: "Invalid bounded evidence packet"))
        }
    }

    /// The quick panel never exports another record's supplemental proof metadata.
    public func copySelectedContext(record: MemoryRecord) throws -> String {
        guard !citations.isEmpty, citations.allSatisfy({ $0.matches(record: record) }),
              selectedMetadataOnly(packet, record: record) else {
            throw ServiceError("Контекст выбранного материала изменился или недоступен. Повторите поиск.")
        }
        return try copyContext()
    }
    private func selectedMetadataOnly(_ value: EvidenceJSON, record: MemoryRecord) -> Bool {
        switch value {
        case .object(let object):
            if let id = object["record_id"] {
                guard case .string(let identifier) = id, ExactSourceText.equal(identifier, record.id) else { return false }
            }
            if let revision = object["revision"] {
                guard case .integer(let number) = revision, number == Int64(record.revision ?? 0) else { return false }
            }
            return object.values.allSatisfy { selectedMetadataOnly($0, record: record) }
        case .array(let array): return array.allSatisfy { selectedMetadataOnly($0, record: record) }
        default: return true
        }
    }

    /// A compact structured packet keeps citations and trust metadata together.
    /// Clipboard writes are performed only by the native button action.
    public func copyContext() throws -> String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        let data = try encoder.encode(packet)
        guard let text = String(data: data, encoding: .utf8) else { throw ServiceError("Не удалось подготовить контекст.") }
        guard text.unicodeScalars.count <= budgetChars else {
            throw ServiceError("Копируемый пакет превышает бюджет. Увеличьте бюджет и повторите поиск.")
        }
        return text
    }
}
