import Foundation

enum ExactSourceText {
    static func equal(_ lhs: String?, _ rhs: String?) -> Bool {
        switch (lhs, rhs) {
        case (.none, .none): return true
        case (.some(let left), .some(let right)): return left.unicodeScalars.elementsEqual(right.unicodeScalars)
        default: return false
        }
    }
}

/// Metadata can change without a text revision; original-source actions compare both.
public struct MemorySourceReference: Equatable {
    private let recordId: String
    private let revision: Int?
    private let sourceId: String?
    private let sourceKind: String?
    private let sourceObjectId: String?
    private let sourceUri: String?
    public init(record: MemoryRecord) {
        recordId = record.id; revision = record.revision; sourceId = record.sourceId
        sourceKind = record.sourceKind; sourceObjectId = record.sourceObjectId; sourceUri = record.sourceUri
    }
    public init(citation: EvidenceCitation) {
        recordId = citation.recordId; revision = citation.revision; sourceId = citation.sourceId
        sourceKind = citation.sourceKind; sourceObjectId = citation.sourceObjectId; sourceUri = citation.sourceUri
    }
    public static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.revision == rhs.revision && ExactSourceText.equal(lhs.recordId, rhs.recordId)
            && ExactSourceText.equal(lhs.sourceId, rhs.sourceId) && ExactSourceText.equal(lhs.sourceKind, rhs.sourceKind)
            && ExactSourceText.equal(lhs.sourceObjectId, rhs.sourceObjectId) && ExactSourceText.equal(lhs.sourceUri, rhs.sourceUri)
    }
    public func matches(_ record: MemoryRecord) -> Bool { self == Self(record: record) }
}
