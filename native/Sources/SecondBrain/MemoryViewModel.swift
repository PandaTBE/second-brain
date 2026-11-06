import SwiftUI
import AppKit
import SecondBrainCore

@MainActor
final class MemoryViewModel: ObservableObject {
    @Published var status: MemoryStatus?
    @Published var statusLoading = false
    @Published var statusError: String?
    @Published var query = ""
    @Published private var browsing = MemoryBrowseState()
    @Published private(set) var filter: MemoryBrowseFilter = .all
    @Published private(set) var searchMode: MemorySearchMode = .lexical
    @Published private(set) var resultsHaveUpdates = false
    @Published var selectedId: String?
    @Published var record: MemoryRecord?
    @Published var recordLoading = false
    @Published var recordError: String?
    @Published var cardBatchLimit = 10
    @Published var action = ""
    @Published var notice: String?
    @Published var noticeIsError = false
    @Published var scheduleDraft = MemoryScheduleRequest()
    @Published var scheduleEdited = false
    @Published var mcp: MCPConfiguration?
    @Published var mcpLoading = false
    @Published var mcpError: String?
    @Published private(set) var restoreEpoch = UUID()
    @Published private(set) var isRestoring = false
    private var client: APIClient?
    private var statusRequest = 0
    private var detailRequest = 0
    private var scheduleRevision = 0
    private var pollTask: Task<Void, Never>?
    private var searchTask: Task<Void, Never>?
    private var lastCompletedJobs: Set<String> = []
    private var receivedStatus = false
    init(client: APIClient? = nil) { self.client = client }

    var busy: Bool { isRestoring || !action.isEmpty || status?.busy == true }
    var providerBlocked: Bool { status?.providerBackoff?.blocked == true }
    var synchronizationPaused: Bool { isRestoring || (status?.scheduler.paused ?? false) }
    var cardConcurrency: Int { MemoryCardConcurrency.clamped(status?.scheduler.cardConcurrency ?? MemoryCardConcurrency.defaultValue) }
    var autoImportEnabled: Bool { !synchronizationPaused && (status?.scheduler.autoImportEnabled ?? true) }
    var atlasURL: URL? { client.flatMap { MemoryAtlasLink.url(baseURL: $0.baseURL) } }
    var results: [MemoryRecord] { browsing.results }
    var searchLoading: Bool { browsing.isLoading && browsing.results.isEmpty }
    var loadMoreLoading: Bool { browsing.isLoading && !browsing.results.isEmpty }
    var hasMore: Bool { browsing.hasMore }
    var searchDiagnostics: MemorySearchDiagnostics? { browsing.search }
    var usesRankedSearch: Bool { searchMode != .lexical && !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    var searchError: String? { browsing.results.isEmpty ? browsing.error : nil }
    var loadMoreError: String? { browsing.results.isEmpty ? nil : browsing.error }
    var listTitle: String {
        if !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return "Результаты поиска · \(filter.title.lowercased())" }
        return filter == .all ? "Последние материалы" : filter.title
    }
    var emptyTitle: String {
        query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? filter.emptyTitle : "Ничего не найдено"
    }
    var emptyDescription: String {
        if !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return "Попробуйте другие слова или выберите другой фильтр." }
        return filter == .all ? "Добавьте источник. Codex, Claude и Qwen Code будут импортироваться автоматически." : "Здесь появятся материалы с выбранным статусом."
    }

    func configure(_ client: APIClient) async {
        let lifetime = restoreEpoch
        pollTask?.cancel(); pollTask = nil; searchTask?.cancel()
        browsing.invalidate(); clearSelection()
        self.client = client
        await refreshStatus()
        guard lifetime == restoreEpoch else { return }
        await search()
        guard lifetime == restoreEpoch else { return }
        await loadMCP()
        guard lifetime == restoreEpoch else { return }
        ensurePolling()
    }

    func refreshStatus() async {
        guard let client else { return }
        statusRequest += 1
        let request = statusRequest
        statusLoading = true
        defer { if request == statusRequest { statusLoading = false } }
        do {
            let response: MemoryStatus = try await client.get("/api/memory/status")
            guard request == statusRequest else { return }
            let completed = Set(response.jobs.filter { ["completed", "error"].contains($0.status) }.map(\.id))
            let changed = receivedStatus && !completed.subtracting(lastCompletedJobs).isEmpty
            receivedStatus = true
            lastCompletedJobs = completed
            status = response; statusError = nil
            if scheduleEdited {
                scheduleDraft = scheduleDraft.applyingSynchronizationPause(response.scheduler.paused ?? false)
            } else { scheduleDraft = response.scheduler.request }
            if changed {
                resultsHaveUpdates = true
                if let selectedId { await selectRecord(selectedId) }
            }
        } catch { if request == statusRequest { statusError = error.localizedDescription } }
    }

    private func ensurePolling() {
        guard !isRestoring, client != nil, pollTask == nil else { return }
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                let interval: UInt64 = self.status?.busy == true ? 1 : 5
                do { try await Task.sleep(nanoseconds: interval * 1_000_000_000) } catch { return }
                guard !Task.isCancelled else { return }
                await self.refreshStatus()
            }
        }
    }

    func sourcePolicyDidChange() async {
        let lifetime = restoreEpoch
        searchTask?.cancel(); searchTask = nil
        browsing.invalidate(); clearSelection()
        statusRequest += 1; status = nil
        await refreshStatus()
        guard lifetime == restoreEpoch else { return }
        await search()
    }

    func invalidateForRestore() {
        restoreEpoch = UUID(); isRestoring = true
        pollTask?.cancel(); pollTask = nil; searchTask?.cancel(); searchTask = nil
        browsing.invalidate(); clearSelection(); client = nil
        query = ""; filter = .all; searchMode = .lexical
        statusRequest += 1; scheduleRevision += 1
        scheduleEdited = false; scheduleDraft = MemoryScheduleRequest(autoImportEnabled: false, paused: true)
        status = nil; statusError = nil; statusLoading = false; notice = nil; action = ""
        mcp = nil; mcpError = nil; mcpLoading = false
        receivedStatus = false; lastCompletedJobs = []; resultsHaveUpdates = false
    }

    func restoreDidComplete(_ client: APIClient) async {
        isRestoring = false
        await configure(client)
    }

    private func currentResponse<Value>(_ operation: (APIClient) async throws -> Value) async throws -> Value {
        guard !isRestoring, let client else { throw CancellationError() }
        let lifetime = restoreEpoch
        do {
            let value = try await operation(client)
            guard lifetime == restoreEpoch, !isRestoring else { throw CancellationError() }
            return value
        } catch {
            guard lifetime == restoreEpoch, !isRestoring else { throw CancellationError() }
            throw error
        }
    }

    func originalTarget(_ record: MemoryRecord, localFiles: LocalOriginalAccess = .currentDevice) async throws -> OriginalSourceTarget {
        try await currentResponse { try await OriginalSourceOperations(client: $0).target(for: record, localFiles: localFiles) }
    }

    func changeSearchMode(_ value: MemorySearchMode) {
        guard value != searchMode else { return }
        searchMode = value
        if usesRankedSearch { filter = .all }
        searchTask?.cancel()
        browsing.invalidate(); clearSelection()
        searchTask = Task { await search() }
    }

    func factPage(_ request: MemoryFactsRequest) async throws -> MemoryFactsResponse {
        try await currentResponse { try await $0.get("/api/memory/facts", query: request.queryItems) }
    }

    func factHistory(_ factId: String, before: String? = nil) async throws -> MemoryFactHistoryResponse {
        guard client != nil else { throw ServiceError("Локальный сервис не запущен.") }
        guard !factId.isEmpty, factId.count <= 128, factId.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "_" || $0 == "-") }),
              (before?.unicodeScalars.count ?? 0) <= 4096 else { throw ServiceError("Недопустимый идентификатор истории.") }
        var query = [URLQueryItem(name: "limit", value: "50")]
        if let before { query += [URLQueryItem(name: "before", value: before)] }
        return try await currentResponse { try await $0.get("/api/memory/facts/\(factId)/history", query: query) }
    }

    func currentFactRecord(_ recordId: String) async throws -> MemoryRecord {
        guard client != nil, !recordId.isEmpty, recordId.count <= 128,
              recordId.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "_" || $0 == "-") }) else { throw ServiceError("Материал недоступен.") }
        let response: MemoryRecordResponse = try await currentResponse { try await $0.get("/api/memory/records/\(recordId)") }
        return response.record
    }

    func projectFacts() async throws -> MemoryJobResponse {
        let lifetime = restoreEpoch
        let result: MemoryJobResponse = try await currentResponse { try await $0.send("/api/memory/facts/project", body: FactsProjectionRequest()) }
        guard lifetime == restoreEpoch else { throw CancellationError() }
        await refreshStatus()
        guard lifetime == restoreEpoch else { throw CancellationError() }
        ensurePolling()
        return result
    }

    func factProjectionJob(_ jobId: String) async throws -> MemoryJob {
        guard client != nil, !jobId.isEmpty, jobId.count <= 128,
              jobId.allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "-" || $0 == "_") }) else { throw ServiceError("Задание недоступно.") }
        let response: MemoryJobStatusResponse = try await currentResponse { try await $0.get("/api/memory/jobs/\(jobId)") }
        return response.job
    }

    func quickSearch(query: String, mode: MemorySearchMode) async throws -> MemorySearchResponse {
        guard client != nil else { throw ServiceError("Локальный сервис не запущен. Откройте Second Brain и дождитесь запуска.") }
        return try await currentResponse { try await $0.get("/api/memory/search", query: [
            URLQueryItem(name: "q", value: query), URLQueryItem(name: "mode", value: mode.rawValue),
            URLQueryItem(name: "limit", value: "8")
        ]) }
    }

    func evidenceBundle(_ request: EvidenceRequest) async throws -> EvidenceBundle {
        try await currentResponse { try await $0.get("/api/memory/evidence", query: request.queryItems) }
    }

    func currentCitationRecord(_ citation: EvidenceCitation) async throws -> MemoryRecord {
        guard client != nil else { throw ServiceError("Локальный сервис не запущен.") }
        let response: MemoryRecordResponse = try await currentResponse { try await $0.get("/api/memory/records/\(citation.recordId)") }
        guard citation.matches(record: response.record) else {
            throw ServiceError("Материал или его источник изменились. Обновите пакет доказательств.")
        }
        return response.record
    }

    func embeddingSettings() async throws -> EmbeddingSettings {
        try await currentResponse { try await $0.get("/api/memory/embeddings") }
    }

    func embeddingModels() async throws -> EmbeddingModelsResponse {
        try await currentResponse { try await $0.get("/api/memory/embeddings/models") }
    }

    func saveEmbeddingSettings(_ request: EmbeddingSettingsRequest) async throws -> EmbeddingSettings {
        let lifetime = restoreEpoch
        let response: EmbeddingSettings = try await currentResponse { try await $0.send("/api/memory/embeddings", body: request) }
        searchTask?.cancel(); browsing.invalidate(); clearSelection()
        await search()
        guard lifetime == restoreEpoch else { throw CancellationError() }
        return response
    }

    func startEmbeddingIndex() async throws -> EmbeddingIndexResponse {
        let lifetime = restoreEpoch
        let response: EmbeddingIndexResponse = try await currentResponse { try await $0.send("/api/memory/embeddings/index", body: EmbeddingIndexRequest()) }
        await refreshStatus()
        guard lifetime == restoreEpoch else { throw CancellationError() }
        ensurePolling()
        return response
    }

    func changeQuery(_ value: String) {
        query = String(value.prefix(512))
        if usesRankedSearch { filter = .all }
        browsing.invalidate(); clearSelection()
        searchTask?.cancel()
        searchTask = Task {
            do { try await Task.sleep(nanoseconds: 300_000_000) } catch { return }
            await search()
        }
    }

    func changeFilter(_ value: MemoryBrowseFilter) async {
        guard filter != value, !usesRankedSearch else { return }
        searchTask?.cancel()
        filter = value
        browsing.invalidate(); clearSelection()
        await search()
    }

    func search() async {
        guard client != nil else { return }
        let request = browsing.beginFirstPage(query: query, mode: searchMode, filter: filter)
        resultsHaveUpdates = false
        await loadPage(request)
    }

    func loadMore() async {
        guard let request = browsing.beginNextPage() else { return }
        await loadPage(request)
    }

    func loadMoreIfNeeded(after id: String) async {
        guard results.last?.id == id, loadMoreError == nil else { return }
        await loadMore()
    }

    private func loadPage(_ request: MemoryBrowseRequest) async {
        guard let client else { return }
        if request.mode != .lexical {
            do {
                let response: MemorySearchResponse = try await client.get("/api/memory/search", query: [
                    URLQueryItem(name: "q", value: request.query),
                    URLQueryItem(name: "mode", value: request.mode.rawValue),
                    URLQueryItem(name: "limit", value: String(MemorySearchMode.rankedResultLimit))
                ])
                try browsing.acceptSearch(response, for: request)
            } catch { browsing.fail(error.localizedDescription, for: request) }
            return
        }
        var parameters = [
            URLQueryItem(name: "q", value: request.query),
            URLQueryItem(name: "card_status", value: request.filter.rawValue),
            URLQueryItem(name: "limit", value: "30")
        ]
        if let cursor = request.cursor { parameters.append(URLQueryItem(name: "cursor", value: cursor)) }
        do {
            let response: MemoryBrowseResponse = try await client.get("/api/memory/browse", query: parameters)
            try browsing.accept(response, for: request)
        } catch {
            browsing.fail(error.localizedDescription, for: request)
        }
    }

    func selectRecord(_ id: String?) async {
        guard !isRestoring else { return }
        guard let id else { clearSelection(); return }
        selectedId = id; detailRequest += 1
        let request = detailRequest
        guard let client else { record = nil; recordLoading = false; recordError = nil; return }
        recordLoading = true; recordError = nil
        defer { if request == detailRequest { recordLoading = false } }
        do {
            let result: MemoryRecordResponse = try await client.get("/api/memory/records/\(id)")
            guard request == detailRequest, selectedId == id else { return }
            record = result.record
        } catch { if request == detailRequest { recordError = error.localizedDescription } }
    }

    func copySourceURI(_ displayed: MemoryRecord) async -> Bool {
        guard let client, selectedId == displayed.id, record?.id == displayed.id,
              displayed.revision != nil else { return false }
        let request = detailRequest
        let reference = MemorySourceReference(record: displayed)
        do {
            let response: MemoryRecordResponse = try await client.get("/api/memory/records/\(displayed.id)")
            guard !Task.isCancelled, request == detailRequest, selectedId == displayed.id else { return false }
            guard reference.matches(response.record), let uri = response.record.sourceUri, !uri.isEmpty else {
                record = response.record
                notice = "Материал или ссылка изменились. Выберите действие заново."; noticeIsError = true
                return false
            }
            guard !Task.isCancelled, request == detailRequest, selectedId == displayed.id else { return false }
            NSPasteboard.general.clearContents()
            guard NSPasteboard.general.setString(uri, forType: .string) else {
                throw ServiceError("Не удалось скопировать ссылку.")
            }
            return true
        } catch {
            guard !Task.isCancelled, request == detailRequest, selectedId == displayed.id else { return false }
            record = nil; recordError = error.localizedDescription
            return false
        }
    }

    private func clearSelection() {
        detailRequest += 1
        selectedId = nil; record = nil; recordLoading = false; recordError = nil
    }

    func startImport(sourceId: String? = nil) async {
        guard let client, !busy else { return }
        let lifetime = restoreEpoch
        action = "import"; notice = nil
        defer { if lifetime == restoreEpoch { action = "" } }
        do {
            let _: MemoryJobResponse = try await client.send("/api/memory/import", body: MemoryImportRequest(sourceId: sourceId))
            guard lifetime == restoreEpoch else { return }
            notice = "Импорт запущен. Создание карточек через модель не запускается."; noticeIsError = false
            await refreshStatus(); ensurePolling()
        } catch { if lifetime == restoreEpoch { notice = error.localizedDescription; noticeIsError = true } }
    }

    func startCards(limit: Int, retryErrors: Bool) async {
        guard let client, !busy, !providerBlocked else { return }
        let lifetime = restoreEpoch
        action = "cards"; notice = nil
        defer { if lifetime == restoreEpoch { action = "" } }
        do {
            let _: MemoryJobResponse = try await client.send("/api/memory/cards", body: MemoryCardsRequest(limit: limit, retryErrors: retryErrors))
            guard lifetime == restoreEpoch else { return }
            notice = "Обработка запущена: не более \(MemoryProcessingLimit.clamped(limit)) материалов в этом пакете."; noticeIsError = false
            await refreshStatus(); ensurePolling()
        } catch { if lifetime == restoreEpoch { notice = error.localizedDescription; noticeIsError = true } }
    }

    func retryAllErrors() async {
        guard let client else { return }
        let lifetime = restoreEpoch
        guard !busy else {
            notice = "Дождитесь завершения текущей операции памяти и повторите действие."
            noticeIsError = true
            return
        }
        action = "retry-errors"; notice = nil
        defer { if lifetime == restoreEpoch { action = "" } }
        do {
            let response: MemoryRetryErrorsResponse = try await client.send(
                "/api/memory/cards/retry-errors", body: MemoryRetryErrorsRequest()
            )
            guard lifetime == restoreEpoch else { return }
            notice = response.notice; noticeIsError = false
            await refreshStatus()
            guard lifetime == restoreEpoch else { return }
            await search()
            guard lifetime == restoreEpoch else { return }
            if let selectedId { await selectRecord(selectedId) }
            ensurePolling()
        } catch { if lifetime == restoreEpoch { notice = error.localizedDescription; noticeIsError = true } }
    }

    func resumeProvider() async {
        guard let client, action.isEmpty, providerBlocked else { return }
        let lifetime = restoreEpoch
        action = "provider-resume"; notice = nil
        defer { if lifetime == restoreEpoch { action = "" } }
        do {
            let _: MemoryProviderResumeResponse = try await client.send("/api/memory/provider-resume", body: MemoryProviderResumeRequest())
            guard lifetime == restoreEpoch else { return }
            notice = "Пауза провайдера снята. При включённых автокарточках обработка продолжится автоматически. Прошлые ошибки повторяются отдельно."
            noticeIsError = false
            await refreshStatus(); ensurePolling()
        } catch { if lifetime == restoreEpoch { notice = error.localizedDescription; noticeIsError = true } }
    }

    func editSchedule(_ value: MemoryScheduleRequest) {
        scheduleDraft = value; scheduleEdited = true; scheduleRevision += 1
    }

    func applySchedule() async {
        guard let client, action.isEmpty, status != nil else { return }
        let lifetime = restoreEpoch
        let revision = scheduleRevision
        let request = scheduleDraft
        action = "schedule"
        defer { if lifetime == restoreEpoch { action = "" } }
        do {
            let response: MemoryScheduleResponse = try await client.send("/api/memory/schedule", body: request)
            guard lifetime == restoreEpoch else { return }
            if revision == scheduleRevision { scheduleDraft = response.scheduler.request; scheduleEdited = false }
            notice = "Настройки автоматизации сохранены."; noticeIsError = false
            await refreshStatus()
        } catch { if lifetime == restoreEpoch { notice = error.localizedDescription; noticeIsError = true } }
    }

    func setSynchronizationPaused(_ paused: Bool) async {
        guard let client, action.isEmpty, status != nil else { return }
        let lifetime = restoreEpoch
        action = "sync"
        defer { if lifetime == restoreEpoch { action = "" } }
        do {
            let response: MemoryScheduleResponse = try await client.send("/api/memory/sync", body: MemorySyncRequest(paused: paused))
            guard lifetime == restoreEpoch else { return }
            if scheduleEdited {
                scheduleDraft = scheduleDraft.applyingSynchronizationPause(response.scheduler.paused ?? paused)
            } else { scheduleDraft = response.scheduler.request }
            notice = paused ? "Синхронизация остановлена. Поиск через MCP доступен. Уже начатые запросы модели могут завершиться; новые автоматические запросы не начнутся." : "Синхронизация возобновлена. Изменения сессий будут подхвачены автоматически."
            noticeIsError = false
            await refreshStatus()
            ensurePolling()
        } catch { if lifetime == restoreEpoch { notice = error.localizedDescription; noticeIsError = true } }
    }

    func setAutoImport(_ enabled: Bool) async {
        guard let client, action.isEmpty, let current = status?.scheduler.request else { return }
        let lifetime = restoreEpoch
        var request = current
        request.autoImportEnabled = enabled
        action = "autoimport"
        defer { if lifetime == restoreEpoch { action = "" } }
        do {
            let _: MemoryScheduleResponse = try await client.send("/api/memory/schedule", body: request)
            guard lifetime == restoreEpoch else { return }
            if !scheduleEdited { scheduleDraft = request }
            notice = enabled ? "Автоимпорт Codex, Claude и Qwen Code включён." : "Автоимпорт приостановлен."; noticeIsError = false
            await refreshStatus()
        } catch { if lifetime == restoreEpoch { notice = error.localizedDescription; noticeIsError = true } }
    }

    func openAtlas() {
        guard let url = atlasURL else {
            notice = "Граф пока недоступен. Дождитесь запуска локального сервиса."
            noticeIsError = true
            return
        }
        if !NSWorkspace.shared.open(url) {
            notice = "Не удалось открыть браузер. Проверьте приложение браузера по умолчанию."
            noticeIsError = true
        }
    }

    func loadMCP() async {
        guard let client else { return }
        let lifetime = restoreEpoch
        mcpLoading = true
        defer { if lifetime == restoreEpoch { mcpLoading = false } }
        do {
            let response: MCPConfiguration = try await client.get("/api/memory/mcp")
            guard lifetime == restoreEpoch else { return }
            mcp = response; mcpError = nil
        } catch { if lifetime == restoreEpoch { mcpError = error.localizedDescription } }
    }
}
