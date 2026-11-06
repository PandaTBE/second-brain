import Foundation

public struct EvidenceDates: Decodable {
    public let messageAt: Date?
    public let sourceCreatedAt: Date?
    public let sourceModifiedAt: Date?
    public let importedAt: Date?
    private enum CodingKeys: String, CodingKey { case messageAt, sourceCreatedAt, sourceModifiedAt, importedAt }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        func date(_ key: CodingKeys) throws -> Date? {
            guard let raw = try values.decodeIfPresent(String.self, forKey: key) else { return nil }
            guard let parsed = EvidenceDateCodec.timestamp(raw) else {
                throw DecodingError.dataCorruptedError(forKey: key, in: values, debugDescription: "Invalid source timestamp")
            }
            return parsed
        }
        messageAt = try date(.messageAt); sourceCreatedAt = try date(.sourceCreatedAt)
        sourceModifiedAt = try date(.sourceModifiedAt); importedAt = try date(.importedAt)
    }
}

enum EvidenceDateCodec {
    static func timestamp(_ raw: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: raw) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: raw)
    }
    static func day(_ raw: String) -> Date? {
        guard raw.count == 10 else { return nil }
        let formatter = dayFormatter()
        guard let date = formatter.date(from: raw), formatter.string(from: date) == raw else { return nil }
        return date
    }
    static func dayString(_ date: Date) -> String { dayFormatter().string(from: date) }
    private static func dayFormatter() -> DateFormatter {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd"; formatter.isLenient = false
        return formatter
    }
}
