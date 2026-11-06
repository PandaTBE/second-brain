import Foundation

public struct EvidenceRequest: Equatable {
    public let query: String
    public let sourceId: String?
    public let recordId: String?
    public let project: String?
    public let dateFrom: String?
    public let dateTo: String?
    public let budgetChars: Int
    public let mode: MemorySearchMode

    public init(query: String, sourceId: String? = nil, recordId: String? = nil, project: String? = nil,
                dateFrom: String? = nil, dateTo: String? = nil,
                budgetChars: Int = 12000, mode: MemorySearchMode = .hybrid) throws {
        let query = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty, query.unicodeScalars.count <= 512 else {
            throw ServiceError("Введите запрос длиной от 1 до 512 символов.")
        }
        guard (2000...32000).contains(budgetChars) else {
            throw ServiceError("Бюджет контекста: от 2000 до 32000 символов.")
        }
        func optional(_ raw: String?, max: Int) throws -> String? {
            guard let value = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else { return nil }
            guard value.unicodeScalars.count <= max, value.rangeOfCharacter(from: .controlCharacters) == nil else {
                throw ServiceError("Сократите значение фильтра и уберите управляющие символы.")
            }
            return value
        }
        let from = try optional(dateFrom, max: 64), to = try optional(dateTo, max: 64)
        func date(_ raw: String?) throws -> Date? {
            guard let raw else { return nil }
            guard let result = EvidenceDateCodec.day(raw) ?? EvidenceDateCodec.timestamp(raw) else {
                throw ServiceError("Укажите существующую дату ГГГГ-ММ-ДД или время ISO 8601 с часовым поясом.")
            }
            return result
        }
        let start = try date(from), end = try date(to)
        if let start, let end, start > end { throw ServiceError("Начало периода должно быть не позже его окончания.") }
        self.query = query; self.sourceId = try optional(sourceId, max: 128); self.recordId = try optional(recordId, max: 128)
        self.project = try optional(project, max: 256); self.dateFrom = from; self.dateTo = to
        self.budgetChars = budgetChars; self.mode = mode
    }
    public static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.budgetChars == rhs.budgetChars && lhs.mode == rhs.mode
            && ExactSourceText.equal(lhs.query, rhs.query) && ExactSourceText.equal(lhs.sourceId, rhs.sourceId)
            && ExactSourceText.equal(lhs.recordId, rhs.recordId) && ExactSourceText.equal(lhs.project, rhs.project) && ExactSourceText.equal(lhs.dateFrom, rhs.dateFrom)
            && ExactSourceText.equal(lhs.dateTo, rhs.dateTo)
    }
    public static func dayString(_ date: Date) -> String { EvidenceDateCodec.dayString(date) }
    public var queryItems: [URLQueryItem] {
        var result = [URLQueryItem(name: "q", value: query),
                      URLQueryItem(name: "budget_chars", value: String(budgetChars)),
                      URLQueryItem(name: "mode", value: mode.rawValue)]
        for (name, value) in [("source_id", sourceId), ("record_id", recordId), ("project", project), ("date_from", dateFrom), ("date_to", dateTo)] {
            if let value { result += [URLQueryItem(name: name, value: value)] }
        }
        return result
    }
}

public struct EvidenceState {
    public struct Request: Equatable {
        public let parameters: EvidenceRequest
        fileprivate let id: UUID
    }
    public private(set) var bundle: EvidenceBundle?
    public private(set) var isLoading = false
    public private(set) var error: String?
    private var active: Request?
    private var current: Request?
    public init() {}
    public var canPerformAction: Bool { !isLoading && bundle?.citations.isEmpty == false }
    public func isCurrent(_ request: Request) -> Bool { current == request }
    public mutating func invalidate() { active = nil; current = nil; bundle = nil; error = nil; isLoading = false }
    public mutating func beginRefresh(_ parameters: EvidenceRequest) -> Request? {
        guard canPerformAction, current?.parameters == parameters else { return nil }
        return begin(parameters)
    }
    public mutating func begin(_ parameters: EvidenceRequest) -> Request {
        invalidate()
        let request = Request(parameters: parameters, id: UUID())
        active = request; current = request; isLoading = true
        return request
    }
    @discardableResult public mutating func accept(_ bundle: EvidenceBundle, request: Request) throws -> Bool {
        guard active == request else { return false }
        guard ExactSourceText.equal(bundle.query, request.parameters.query), bundle.budgetChars == request.parameters.budgetChars,
              bundle.search?.requestedMode == nil || bundle.search?.requestedMode == request.parameters.mode,
              request.parameters.sourceId == nil || bundle.citations.allSatisfy({ $0.sourceId == request.parameters.sourceId }),
              request.parameters.recordId == nil || bundle.citations.allSatisfy({ $0.recordId == request.parameters.recordId }) else {
            throw ServiceError("Пакет не соответствует запросу. Повторите поиск доказательств.")
        }
        self.bundle = bundle; active = nil; isLoading = false; error = nil
        return true
    }
    @discardableResult public mutating func fail(_ message: String, request: Request) -> Bool {
        guard current == request else { return false }
        active = nil; current = nil; bundle = nil; isLoading = false; error = message
        return true
    }
}
