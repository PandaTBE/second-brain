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
    private var client: APIClient?
    private var statusRequest = 0
    private var detailRequest = 0
    private var scheduleRevision = 0
    private var pollTask: Task<Void, Never>?
    private var searchTask: Task<Void, Never>?
    private var lastCompletedJobs: Set<String> = []
    private var receivedStatus = false

    var busy: Bool { !action.isEmpty || status?.busy == true }
    var providerBlocked: Bool { status?.providerBackoff?.blocked == true }
    var synchronizationPaused: Bool { status?.scheduler.paused ?? false }
    var cardConcurrency: Int { MemoryCardConcurrency.clamped(status?.scheduler.cardConcurrency ?? MemoryCardConcurrency.defaultValue) }
    var autoImportEnabled: Bool { !synchronizationPaused && (status?.scheduler.autoImportEnabled ?? true) }
    var atlasURL: URL? { client.flatMap { MemoryAtlasLink.url(baseURL: $0.baseURL) } }
    var results: [MemoryRecord] { browsing.results }
    var searchLoading: Bool { browsing.isLoading && browsing.results.isEmpty }
    var loadMoreLoading: Bool { browsing.isLoading && !browsing.results.isEmpty }
    var hasMore: Bool { browsing.hasMore }
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
        pollTask?.cancel(); pollTask = nil; searchTask?.cancel()
        browsing.invalidate(); clearSelection()
        self.client = client
        await refreshStatus()
        await search()
        await loadMCP()
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
        guard pollTask == nil else { return }
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

    func changeQuery(_ value: String) {
        query = value
        browsing.invalidate(); clearSelection()
        searchTask?.cancel()
        searchTask = Task {
            do { try await Task.sleep(nanoseconds: 300_000_000) } catch { return }
            await search()
        }
    }

    func changeFilter(_ value: MemoryBrowseFilter) async {
        guard filter != value else { return }
        searchTask?.cancel()
        filter = value
        browsing.invalidate(); clearSelection()
        await search()
    }

    func search() async {
        guard client != nil else { return }
        let request = browsing.beginFirstPage()
        resultsHaveUpdates = false
        await loadPage(request, query: query, filter: filter)
    }

    func loadMore() async {
        guard let request = browsing.beginNextPage() else { return }
        await loadPage(request, query: query, filter: filter)
    }

    func loadMoreIfNeeded(after id: String) async {
        guard results.last?.id == id, loadMoreError == nil else { return }
        await loadMore()
    }

    private func loadPage(_ request: MemoryBrowseRequest, query: String, filter: MemoryBrowseFilter) async {
        guard let client else { return }
        var parameters = [
            URLQueryItem(name: "q", value: query),
            URLQueryItem(name: "card_status", value: filter.rawValue),
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

    private func clearSelection() {
        detailRequest += 1
        selectedId = nil; record = nil; recordLoading = false; recordError = nil
    }

    func startImport(sourceId: String? = nil) async {
        guard let client, !busy else { return }
        action = "import"; notice = nil
        defer { action = "" }
        do {
            let _: MemoryJobResponse = try await client.send("/api/memory/import", body: MemoryImportRequest(sourceId: sourceId))
            notice = "Импорт запущен. Создание карточек через модель не запускается."; noticeIsError = false
            await refreshStatus(); ensurePolling()
        } catch { notice = error.localizedDescription; noticeIsError = true }
    }

    func startCards(limit: Int, retryErrors: Bool) async {
        guard let client, !busy, !providerBlocked else { return }
        action = "cards"; notice = nil
        defer { action = "" }
        do {
            let _: MemoryJobResponse = try await client.send("/api/memory/cards", body: MemoryCardsRequest(limit: limit, retryErrors: retryErrors))
            notice = "Обработка запущена: не более \(MemoryProcessingLimit.clamped(limit)) материалов в этом пакете."; noticeIsError = false
            await refreshStatus(); ensurePolling()
        } catch { notice = error.localizedDescription; noticeIsError = true }
    }

    func retryAllErrors() async {
        guard let client else { return }
        guard !busy else {
            notice = "Дождитесь завершения текущей операции памяти и повторите действие."
            noticeIsError = true
            return
        }
        action = "retry-errors"; notice = nil
        defer { action = "" }
        do {
            let response: MemoryRetryErrorsResponse = try await client.send(
                "/api/memory/cards/retry-errors", body: MemoryRetryErrorsRequest()
            )
            notice = response.notice; noticeIsError = false
            await refreshStatus(); await search()
            if let selectedId { await selectRecord(selectedId) }
            ensurePolling()
        } catch { notice = error.localizedDescription; noticeIsError = true }
    }

    func resumeProvider() async {
        guard let client, action.isEmpty, providerBlocked else { return }
        action = "provider-resume"; notice = nil
        defer { action = "" }
        do {
            let _: MemoryProviderResumeResponse = try await client.send("/api/memory/provider-resume", body: MemoryProviderResumeRequest())
            notice = "Пауза провайдера снята. При включённых автокарточках обработка продолжится автоматически. Прошлые ошибки повторяются отдельно."
            noticeIsError = false
            await refreshStatus(); ensurePolling()
        } catch { notice = error.localizedDescription; noticeIsError = true }
    }

    func editSchedule(_ value: MemoryScheduleRequest) {
        scheduleDraft = value; scheduleEdited = true; scheduleRevision += 1
    }

    func applySchedule() async {
        guard let client, action.isEmpty, status != nil else { return }
        let revision = scheduleRevision
        let request = scheduleDraft
        action = "schedule"
        defer { action = "" }
        do {
            let response: MemoryScheduleResponse = try await client.send("/api/memory/schedule", body: request)
            if revision == scheduleRevision { scheduleDraft = response.scheduler.request; scheduleEdited = false }
            notice = "Настройки автоматизации сохранены."; noticeIsError = false
            await refreshStatus()
        } catch { notice = error.localizedDescription; noticeIsError = true }
    }

    func setSynchronizationPaused(_ paused: Bool) async {
        guard let client, action.isEmpty, status != nil else { return }
        action = "sync"
        defer { action = "" }
        do {
            let response: MemoryScheduleResponse = try await client.send("/api/memory/sync", body: MemorySyncRequest(paused: paused))
            if scheduleEdited {
                scheduleDraft = scheduleDraft.applyingSynchronizationPause(response.scheduler.paused ?? paused)
            } else { scheduleDraft = response.scheduler.request }
            notice = paused ? "Синхронизация остановлена. Поиск через MCP доступен. Уже начатые запросы модели могут завершиться; новые автоматические запросы не начнутся." : "Синхронизация возобновлена. Изменения сессий будут подхвачены автоматически."
            noticeIsError = false
            await refreshStatus()
            ensurePolling()
        } catch { notice = error.localizedDescription; noticeIsError = true }
    }

    func setAutoImport(_ enabled: Bool) async {
        guard let client, action.isEmpty, let current = status?.scheduler.request else { return }
        var request = current
        request.autoImportEnabled = enabled
        action = "autoimport"
        defer { action = "" }
        do {
            let _: MemoryScheduleResponse = try await client.send("/api/memory/schedule", body: request)
            if !scheduleEdited { scheduleDraft = request }
            notice = enabled ? "Автоимпорт Codex, Claude и Qwen Code включён." : "Автоимпорт приостановлен."; noticeIsError = false
            await refreshStatus()
        } catch { notice = error.localizedDescription; noticeIsError = true }
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
        mcpLoading = true
        defer { mcpLoading = false }
        do { mcp = try await client.get("/api/memory/mcp"); mcpError = nil }
        catch { mcpError = error.localizedDescription }
    }
}
