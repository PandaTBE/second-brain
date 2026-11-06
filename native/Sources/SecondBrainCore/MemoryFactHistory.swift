import Foundation

public struct MemoryFactIdentity: Decodable, Identifiable {
    public let id: String
    public let kind: String
    public let text: String
    public init(fact: MemoryFact) { id = fact.id; kind = fact.kind; text = fact.text }
}
public struct MemoryFactHistoryEvent: Decodable, Identifiable {
    public let id: String
    public let proof: MemoryFactProof
    public let observedAt: Date
    public let active: Bool
    public let status: MemoryFactStatus
    public let model: String
    public let promptVersion: String
    public let relations: [MemoryFactRelation]?
    public let relationsTruncated: Bool?
    public var snapshotLabel: String { active ? "Действующее подтверждение" : "Снимок прошлой ревизии" }
    private enum CodingKeys: String, CodingKey { case eventId, observedAt, active, status, model, promptVersion, relations, relationsTruncated }
    public init(from decoder: Decoder) throws {
        proof = try MemoryFactProof(from: decoder)
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self, forKey: .eventId)
        active = try values.decode(Bool.self, forKey: .active)
        status = try values.decode(MemoryFactStatus.self, forKey: .status)
        model = try values.decode(String.self, forKey: .model)
        promptVersion = try values.decode(String.self, forKey: .promptVersion)
        relations = try values.decodeIfPresent([MemoryFactRelation].self, forKey: .relations)
        relationsTruncated = try values.decodeIfPresent(Bool.self, forKey: .relationsTruncated)
        let raw = try values.decode(String.self, forKey: .observedAt)
        guard let date = EvidenceDateCodec.timestamp(raw), !id.isEmpty else {
            throw DecodingError.dataCorruptedError(forKey: .observedAt, in: values, debugDescription: "Invalid history observation")
        }
        observedAt = date
    }
}
public struct MemoryFactHistoryResponse: Decodable {
    public let schemaVersion: Int
    public let fact: MemoryFactIdentity
    public let events: [MemoryFactHistoryEvent]
    public let historySince: Date
    public let untrustedContent: Bool
    public let truncated: Bool
    public let nextCursor: String?
    private enum CodingKeys: String, CodingKey { case schemaVersion, fact, events, historySince, untrustedContent, truncated, nextCursor }
    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try values.decode(Int.self, forKey: .schemaVersion)
        fact = try values.decode(MemoryFactIdentity.self, forKey: .fact)
        events = try values.decode([MemoryFactHistoryEvent].self, forKey: .events)
        untrustedContent = try values.decode(Bool.self, forKey: .untrustedContent)
        truncated = try values.decode(Bool.self, forKey: .truncated)
        nextCursor = try values.decodeIfPresent(String.self, forKey: .nextCursor)
        let raw = try values.decode(String.self, forKey: .historySince)
        guard let date = EvidenceDateCodec.timestamp(raw), schemaVersion == 1, untrustedContent,
              events.count <= 50, events.allSatisfy({ $0.proof.valid && ($0.relations ?? []).allSatisfy { $0.evidence.valid } }), Set(events.map(\.id)).count == events.count else {
            throw DecodingError.dataCorrupted(.init(codingPath: decoder.codingPath, debugDescription: "Invalid bounded fact history"))
        }
        historySince = date
    }
}
