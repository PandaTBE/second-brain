import Foundation

public struct MemoryProvenanceTimestamp {
    public enum Kind { case message, sourceModified, sourceCreated, imported }
    public let kind: Kind
    public let date: Date
    public var label: String {
        switch kind {
        case .message: return "Сообщение"
        case .sourceModified: return "Изменён источник"
        case .sourceCreated: return "Создан источник"
        case .imported: return "Импортирован"
        }
    }
}
extension MemoryRecord {
    public var provenanceTimestamp: MemoryProvenanceTimestamp? {
        let candidates: [(MemoryProvenanceTimestamp.Kind, String?)] = [
            (.message, messageAt), (.sourceModified, sourceModifiedAt), (.sourceCreated, sourceCreatedAt), (.imported, importedAt)
        ]
        for (kind, raw) in candidates {
            if let raw, let date = EvidenceDateCodec.timestamp(raw) { return MemoryProvenanceTimestamp(kind: kind, date: date) }
        }
        return nil
    }
}
