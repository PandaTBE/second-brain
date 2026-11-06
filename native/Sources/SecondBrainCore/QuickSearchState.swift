import Foundation

/// The quick panel owns its requests independently from the main memory window.
public struct QuickSearchState {
    public struct Request: Equatable {
        public let query: String
        public let mode: MemorySearchMode
        fileprivate let id: UUID
    }
    public struct Action {
        public let record: MemoryRecord
        public let request: Request
        fileprivate let id: UUID
        public func evidenceRequest(budgetChars: Int = 6000) throws -> EvidenceRequest {
            try EvidenceRequest(query: request.query, sourceId: record.sourceId, recordId: record.id,
                                budgetChars: budgetChars, mode: request.mode)
        }
    }
    public private(set) var isPresented = false
    public private(set) var results: [MemoryRecord] = []
    public private(set) var selectedId: String?
    public private(set) var diagnostics: MemorySearchDiagnostics?
    public private(set) var error: String?
    public private(set) var isLoading = false
    private var current: Request?
    private var accepted: Request?
    private var action: Action?
    public init() {}
    public var selectedRecord: MemoryRecord? { results.first { $0.id == selectedId } }
    public var isActing: Bool { action != nil }
    public mutating func present() { dismiss(); isPresented = true }
    public mutating func dismiss() {
        isPresented = false; results = []; selectedId = nil; diagnostics = nil
        error = nil; isLoading = false; current = nil; accepted = nil; action = nil
    }
    public mutating func begin(query: String, mode: MemorySearchMode = .lexical) -> Request? {
        guard isPresented else { return nil }
        results = []; selectedId = nil; diagnostics = nil; error = nil; accepted = nil; action = nil; current = nil; isLoading = false
        let query = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return nil }
        guard query.unicodeScalars.count <= 512, query.rangeOfCharacter(from: .controlCharacters) == nil else {
            error = "Введите запрос до 512 символов без управляющих знаков."; return nil
        }
        let request = Request(query: query, mode: mode, id: UUID())
        current = request; isLoading = true
        return request
    }
    public func isCurrent(_ request: Request) -> Bool { isPresented && current == request }
    @discardableResult public mutating func accept(_ response: MemorySearchResponse, request: Request) throws -> Bool {
        guard isCurrent(request), isLoading else { return false }
        guard response.search?.requestedMode == nil || response.search?.requestedMode == request.mode else {
            throw ServiceError("Ответ не соответствует режиму поиска. Повторите запрос.")
        }
        var seen = Set<String>()
        results = Array(response.results.filter { seen.insert($0.id).inserted }.prefix(8))
        selectedId = results.first?.id; diagnostics = response.search; isLoading = false; accepted = request
        return true
    }
    @discardableResult public mutating func fail(_ message: String, request: Request) -> Bool {
        guard isCurrent(request) else { return false }
        results = []; selectedId = nil; diagnostics = nil; accepted = nil; action = nil
        isLoading = false; error = message
        return true
    }
    public mutating func select(_ id: String?) {
        guard id != selectedId, results.contains(where: { $0.id == id }) else { return }
        selectedId = id; action = nil
    }
    public mutating func moveSelection(by offset: Int) {
        guard !results.isEmpty else { return }
        let index = results.firstIndex { $0.id == selectedId } ?? 0
        select(results[min(max(index + offset, 0), results.count - 1)].id)
    }
    public mutating func beginAction() -> Action? {
        guard isPresented, !isLoading, action == nil, let request = accepted, request == current,
              let record = selectedRecord, (record.revision ?? 0) > 0 else { return nil }
        let result = Action(record: record, request: request, id: UUID())
        action = result; return result
    }
    public func isCurrentAction(_ candidate: Action) -> Bool {
        isPresented && current == candidate.request && accepted == candidate.request && action?.id == candidate.id
            && selectedRecord.map { MemorySourceReference(record: candidate.record).matches($0) } == true
    }
    public mutating func finishAction(_ candidate: Action) {
        if isCurrentAction(candidate) { action = nil }
    }
}
