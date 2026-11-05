import SwiftUI
import SecondBrainCore

enum AppSection: String, CaseIterable, Identifiable {
    case overview = "Обзор", memory = "Память", model = "Модель и API", sources = "Источники", mcp = "Подключение MCP"
    var id: String { rawValue }
    var symbol: String { switch self { case .overview: "square.grid.2x2"; case .memory: "brain.head.profile"; case .model: "cpu"; case .sources: "tray.full"; case .mcp: "point.3.connected.trianglepath.dotted" } }
}

@MainActor
final class AppStore: ObservableObject {
    let service = BackendService()
    let memory = MemoryViewModel()
    @Published var section: AppSection = .overview
    @Published var ready = false
    @Published var starting = false
    @Published var startupError: String?
    @Published var providers: [ProviderInfo] = []
    @Published var draft = ModelSettings()
    @Published var savedSettings = ModelSettings()
    @Published var tokenDraft = ""
    @Published var tokenSaved = false
    @Published var storageWarning: String?
    @Published var models: [ModelInfo] = []
    @Published var currency = "USD"
    @Published var query = ""
    @Published var sortByPrice = true
    @Published var catalogLoading = false
    @Published var catalogError: String?
    @Published var catalogWarning: String?
    @Published var catalogUpdated: String?
    @Published var action = ""
    @Published var notice: String?
    @Published var noticeIsError = false
    @Published var connection: ConnectionResponse?
    @Published var sources: [MemorySource] = []
    @Published var sourcesLoading = false
    @Published var busySources: Set<String> = []
    private var client: APIClient?
    private var draftRevision = 0
    private var scopeRevision = 0
    private var catalogRevision = 0
    private var credentialRevision = 0
    private var sourcesRevision = 0
    private var sourcesRequest = 0
    private var mutationScope: String?
    private var credentialTask: Task<Void, Never>?

    var selectedProvider: ProviderInfo? { providers.first { $0.id == draft.provider } }
    var scopeKey: String { "\(draft.provider)|\(draft.baseUrl.trimmingCharacters(in: .whitespacesAndNewlines))" }
    var requiresToken: Bool { selectedProvider?.needsToken ?? draft.requiresCredential }
    var hasToken: Bool { !requiresToken || draft.canUseCredential(tokenSaved: tokenSaved, tokenDraft: tokenDraft) }
    var hasSavedModel: Bool { !savedSettings.modelId.isEmpty }
    var isSaved: Bool { draft.matchesSavedConnection(savedSettings, tokenSaved: tokenSaved, tokenDraft: tokenDraft) }
    var publicCatalog: Bool { ["openrouter", "simpleclaw", "ollama"].contains(draft.provider) }
    var filteredModels: [ModelInfo] {
        let filtered = models.filter { query.isEmpty || "\($0.name) \($0.id)".localizedCaseInsensitiveContains(query) }
        return sortByPrice ? ModelFormatting.sorted(filtered) : filtered.sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }

    func start() async {
        guard !starting, !(ready && service.isRunning) else { return }
        ready = false
        starting = true; startupError = nil
        defer { starting = false }
        do {
            let base = try await service.start()
            let initial = APIClient(baseURL: base)
            let result: BootstrapResponse = try await initial.get("/api/bootstrap")
            client = APIClient(baseURL: base, csrf: result.csrfToken)
            providers = result.providers; draft = result.settings; savedSettings = result.settings
            tokenSaved = result.tokenSaved; storageWarning = result.warning
            if requiresToken && !result.storage.available && storageWarning == nil { storageWarning = "Связка ключей недоступна. Сохранение секретов требует доступа к Связке ключей macOS." }
            ready = true
            await refreshSources()
            if let client { await memory.configure(client) }
            if publicCatalog || tokenSaved { await loadCatalog() }
        } catch { startupError = error.localizedDescription; await service.stop() }
    }

    private func changed() { draftRevision += 1; connection = nil; notice = nil }
    private func candidateToken() -> String? { requiresToken && !tokenDraft.isEmpty ? tokenDraft : nil }

    func changeProvider(_ id: String) {
        guard id != draft.provider, let provider = providers.first(where: { $0.id == id }) else { return }
        credentialTask?.cancel()
        draft = ModelSettings(provider: id, baseUrl: provider.baseUrl)
        tokenDraft = ""; tokenSaved = false; models = []; catalogError = nil; catalogWarning = nil; catalogUpdated = nil
        scopeRevision += 1; catalogRevision += 1; catalogLoading = false; changed()
        Task { await refreshCredential(); if publicCatalog || tokenSaved { await loadCatalog() } }
    }

    func changeBaseURL(_ value: String) {
        draft.baseUrl = value; tokenDraft = ""; tokenSaved = false
        scopeRevision += 1; catalogRevision += 1; catalogLoading = false; models = []; catalogWarning = nil; catalogUpdated = nil; changed()
        credentialTask?.cancel()
        credentialTask = Task {
            do { try await Task.sleep(nanoseconds: 450_000_000) } catch { return }
            guard URL(string: value)?.scheme == "https", URL(string: value)?.host != nil else { return }
            await refreshCredential()
        }
    }

    func changeToken(_ value: String) {
        tokenDraft = value
        if !publicCatalog { catalogRevision += 1; catalogLoading = false; models = []; catalogError = nil; catalogWarning = nil }
        changed()
    }

    func selectModel(_ model: ModelInfo) {
        draft.modelId = model.id; draft.modelName = model.name; changed()
    }
    func enterModelID(_ value: String) { draft.modelId = value; draft.modelName = value; changed() }

    func refreshCredential() async {
        guard requiresToken else { tokenSaved = false; storageWarning = nil; return }
        guard let client, !draft.baseUrl.isEmpty, mutationScope != scopeKey else { return }
        credentialRevision += 1
        let epoch = credentialRevision; let scope = scopeRevision
        do {
            let result: TokenStatus = try await client.send("/api/credential-status", body: ScopeRequest(draft))
            guard scope == scopeRevision, epoch == credentialRevision else { return }
            tokenSaved = result.tokenSaved; storageWarning = nil
        } catch {
            guard scope == scopeRevision, epoch == credentialRevision else { return }
            storageWarning = error.localizedDescription
        }
    }

    func loadCatalog() async {
        guard let client, !draft.baseUrl.isEmpty else { return }
        catalogRevision += 1
        let epoch = catalogRevision; let scope = scopeRevision
        catalogLoading = true; catalogError = nil; catalogWarning = nil
        defer { if epoch == catalogRevision { catalogLoading = false } }
        do {
            let result: CatalogResponse = try await client.send("/api/models", body: ModelRequest(draft, token: candidateToken()))
            guard scope == scopeRevision, epoch == catalogRevision else { return }
            models = result.models; currency = result.currency ?? "USD"; catalogUpdated = result.fetchedAt
            catalogWarning = result.message ?? (result.truncated == true ? "Показана часть каталога. Нужную модель можно указать по точному ID." : nil)
        } catch { if scope == scopeRevision, epoch == catalogRevision { catalogError = error.localizedDescription } }
    }

    private func validate() -> Bool {
        if draft.baseUrl.isEmpty { showError("Укажите адрес API."); return false }
        if !hasToken { showError("Вставьте API-токен выбранного провайдера."); return false }
        if draft.modelId.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { showError("Выберите модель или укажите точный ID вручную."); return false }
        return true
    }
    private func showError(_ value: String) { notice = value; noticeIsError = true }

    func testConnection() async {
        guard let client, action.isEmpty, validate() else { return }
        let revision = draftRevision
        action = "test"; notice = nil
        defer { action = "" }
        do {
            let result: ConnectionResponse = try await client.send("/api/test-connection", body: ConnectionRequest(draft, token: candidateToken()))
            guard revision == draftRevision else { return }
            connection = result; notice = result.message; noticeIsError = result.status != "ok"
        } catch { if revision == draftRevision { showError(error.localizedDescription) } }
    }

    func save() async {
        guard let client, action.isEmpty, validate() else { return }
        let revision = draftRevision
        let savingScope = scopeRevision
        action = "save"; mutationScope = scopeKey; credentialRevision += 1; notice = nil
        defer { action = ""; mutationScope = nil }
        do {
            let result: SavedSettings = try await client.send("/api/settings", body: SaveRequest(draft, token: candidateToken()))
            savedSettings = result.settings
            if savingScope == scopeRevision { tokenSaved = result.tokenSaved; storageWarning = nil }
            guard revision == draftRevision else { notice = "Предыдущие настройки сохранены. Текущие изменения ещё не сохранены."; noticeIsError = false; return }
            draft = result.settings; tokenSaved = result.tokenSaved; tokenDraft = ""
            notice = result.settings.isLocal ? "Локальная модель сохранена. API-токен не требуется." : "Настройки сохранены. Токен — в Связке ключей macOS."; noticeIsError = false
        } catch { if revision == draftRevision { showError(error.localizedDescription) } }
    }

    func deleteToken() async {
        guard requiresToken, let client, action.isEmpty else { return }
        let scope = scopeRevision; let revision = draftRevision
        action = "delete"; mutationScope = scopeKey; credentialRevision += 1
        defer { action = ""; mutationScope = nil }
        do {
            let _: TokenStatus = try await client.send("/api/token", method: "DELETE", body: ScopeRequest(draft))
            guard scope == scopeRevision else { return }
            tokenSaved = false; if revision == draftRevision { tokenDraft = "" }; changed()
            notice = "Сохранённый токен удалён."; noticeIsError = false
        } catch { if scope == scopeRevision { showError(error.localizedDescription) } }
    }

    func refreshSources() async {
        guard let client else { return }
        sourcesRequest += 1
        let request = sourcesRequest; let revision = sourcesRevision
        sourcesLoading = true
        defer { if request == sourcesRequest { sourcesLoading = false } }
        do {
            let result: SourcesResponse = try await client.get("/api/sources")
            guard revision == sourcesRevision, request == sourcesRequest else { return }
            sources = result.sources
        } catch { if revision == sourcesRevision, request == sourcesRequest { showError(error.localizedDescription) } }
    }

    func notionPages(token: String, query: String, cursor: String?) async throws -> NotionPagesResponse {
        guard let client else { throw ServiceError("Локальный сервис не запущен.") }
        return try await client.send("/api/sources/notion/pages", body: NotionPagesRequest(token: token, query: query, cursor: cursor))
    }

    func githubRepositories(token: String, page: Int) async throws -> GitHubRepositoriesResponse {
        guard let client else { throw ServiceError("Локальный сервис не запущен.") }
        return try await client.send("/api/sources/github/repositories", body: GitHubRepositoriesRequest(token: token, page: page))
    }

    func addSource(_ request: AddSourceRequest) async -> String? {
        guard let client else { return "Локальный сервис не запущен." }
        sourcesRevision += 1
        defer { sourcesRevision += 1 }
        do {
            let result: SourceResponse = try await client.send("/api/sources", body: request)
            sources = sources + [result.source]
            return nil
        } catch { return error.localizedDescription }
    }

    func checkSource(_ source: MemorySource) async {
        guard let client, !busySources.contains(source.id) else { return }
        sourcesRevision += 1
        busySources = busySources.union([source.id])
        defer { busySources = busySources.subtracting([source.id]); sourcesRevision += 1 }
        do {
            let result: SourceResponse = try await client.send("/api/sources/\(source.id)/test", body: EmptyBody())
            sources = sources.map { $0.id == source.id ? result.source : $0 }
        } catch { showError(error.localizedDescription); await refreshSources() }
    }

    func setMailImport(_ source: MemorySource, enabled: Bool) async {
        guard let client, source.kind == "imap", !busySources.contains(source.id) else { return }
        sourcesRevision += 1
        busySources = busySources.union([source.id])
        defer { busySources = busySources.subtracting([source.id]); sourcesRevision += 1 }
        do {
            let result: SourceResponse = try await client.send(
                "/api/sources/\(source.id)/mail-import",
                body: MailImportToggleRequest(enabled: enabled)
            )
            sources = sources.map { $0.id == source.id ? result.source : $0 }
            notice = enabled ? "Импорт писем включён. Можно запустить его вручную или использовать расписание памяти." : "Импорт писем приостановлен. Сохранённые материалы доступны в поиске."
            noticeIsError = false
            await memory.refreshStatus()
        } catch {
            showError(error.localizedDescription)
            await refreshSources()
        }
    }

    func removeSource(_ source: MemorySource) async {
        guard let client else { return }
        sourcesRevision += 1
        defer { sourcesRevision += 1 }
        do {
            let _: DeletedResponse = try await client.send("/api/sources/\(source.id)", method: "DELETE", body: EmptyBody())
            sources = sources.filter { $0.id != source.id }
        } catch { showError(error.localizedDescription) }
    }
}
