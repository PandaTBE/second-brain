import SwiftUI
import SecondBrainCore

enum AppSection: String, CaseIterable, Identifiable {
    case overview = "Обзор", memory = "Память", model = "Модель и API", sources = "Источники", backups = "Резервные копии", mcp = "Подключение MCP"
    var id: String { rawValue }
    var symbol: String { switch self { case .overview: "square.grid.2x2"; case .memory: "brain.head.profile"; case .model: "cpu"; case .sources: "tray.full"; case .backups: "externaldrive.badge.timemachine"; case .mcp: "point.3.connected.trianglepath.dotted" } }
}

@MainActor
final class AppStore: ObservableObject {
    let service = BackendService()
    let memory: MemoryViewModel
    let backups = BackupViewModel()
    lazy var quickSearch = QuickSearchController(memory: memory)
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
    @Published private(set) var restoreEpoch = UUID()
    @Published private(set) var isRestoring = false
    private var client: APIClient?
    private var draftRevision = 0
    private var scopeRevision = 0
    private var catalogRevision = 0
    private var credentialRevision = 0
    private var sourcesRevision = 0
    private var sourcesRequest = 0
    private var mutationScope: String?
    private var credentialTask: Task<Void, Never>?
    init(client: APIClient? = nil) {
        self.client = client
        memory = MemoryViewModel(client: client)
        if let client { configureBackups(client) }
    }

    var selectedProvider: ProviderInfo? { providers.first { $0.id == draft.provider } }
    var scopeKey: String { draft.connectionScopeKey }
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
        guard !isRestoring, !starting, !(ready && service.isRunning) else { return }
        let lifetime = restoreEpoch
        ready = false
        starting = true; startupError = nil
        defer { if lifetime == restoreEpoch { starting = false } }
        do {
            let base = try await service.start()
            guard lifetime == restoreEpoch else { return }
            let initial = APIClient(baseURL: base)
            let result: BootstrapResponse = try await initial.get("/api/bootstrap")
            guard lifetime == restoreEpoch else { return }
            client = APIClient(baseURL: base, csrf: result.csrfToken)
            if let client { configureBackups(client) }
            providers = result.providers; draft = result.settings; savedSettings = result.settings
            tokenSaved = result.tokenSaved; storageWarning = result.warning
            if requiresToken && !result.storage.available && storageWarning == nil { storageWarning = "Связка ключей недоступна. Сохранение секретов требует доступа к Связке ключей macOS." }
            ready = true
            await refreshSources()
            guard lifetime == restoreEpoch else { return }
            if let client { await memory.configure(client) }
            guard lifetime == restoreEpoch else { return }
            if publicCatalog || tokenSaved { await loadCatalog() }
        } catch { if lifetime == restoreEpoch { startupError = error.localizedDescription; await service.stop() } }
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
        guard value != draft.baseUrl else { return }
        draft = draft.replacingBaseURL(value); tokenDraft = ""; tokenSaved = false; storageWarning = nil
        scopeRevision += 1; catalogRevision += 1; catalogLoading = false; models = []; catalogError = nil; catalogWarning = nil; catalogUpdated = nil; changed()
        credentialTask?.cancel()
        credentialTask = Task {
            do { try await Task.sleep(nanoseconds: 450_000_000) } catch { return }
            guard URL(string: value)?.scheme == "https", URL(string: value)?.host != nil else { return }
            await refreshCredential()
        }
    }

    func changePrivateNetworkAccess(_ allowed: Bool) {
        let updated = draft.settingPrivateNetworkAccess(allowed)
        guard updated != draft else { return }
        credentialTask?.cancel()
        draft = updated; tokenSaved = false; storageWarning = nil
        scopeRevision += 1; catalogRevision += 1; catalogLoading = false
        models = []; catalogError = nil; catalogWarning = nil; catalogUpdated = nil; changed()
        credentialTask = Task { await refreshCredential() }
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
        guard !isRestoring else { return }
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
        guard !isRestoring, let client, !draft.baseUrl.isEmpty else { return }
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
        if let issue = draft.customEndpointValidationMessage { showError(issue); return false }
        if !hasToken { showError("Вставьте API-токен выбранного провайдера."); return false }
        if draft.modelId.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { showError("Выберите модель или укажите точный ID вручную."); return false }
        return true
    }
    private func showError(_ value: String) { notice = value; noticeIsError = true }

    func testConnection() async {
        guard !isRestoring, let client, action.isEmpty, validate() else { return }
        let lifetime = restoreEpoch
        let revision = draftRevision
        action = "test"; notice = nil
        defer { if lifetime == restoreEpoch { action = "" } }
        do {
            let result: ConnectionResponse = try await client.send("/api/test-connection", body: ConnectionRequest(draft, token: candidateToken()))
            guard lifetime == restoreEpoch, revision == draftRevision else { return }
            connection = result; notice = result.message; noticeIsError = result.status != "ok"
        } catch { if lifetime == restoreEpoch, revision == draftRevision { showError(error.localizedDescription) } }
    }

    func save() async {
        guard !isRestoring, let client, action.isEmpty, validate() else { return }
        let lifetime = restoreEpoch
        let revision = draftRevision
        let savingScope = scopeRevision
        action = "save"; mutationScope = scopeKey; credentialRevision += 1; notice = nil
        defer { if lifetime == restoreEpoch { action = ""; mutationScope = nil } }
        do {
            let result: SavedSettings = try await client.send("/api/settings", body: SaveRequest(draft, token: candidateToken()))
            guard lifetime == restoreEpoch else { return }
            savedSettings = result.settings
            if savingScope == scopeRevision { tokenSaved = result.tokenSaved; storageWarning = nil }
            guard revision == draftRevision else { notice = "Предыдущие настройки сохранены. Текущие изменения ещё не сохранены."; noticeIsError = false; return }
            draft = result.settings; tokenSaved = result.tokenSaved; tokenDraft = ""
            notice = result.settings.isLocal ? "Локальная модель сохранена. API-токен не требуется." : "Настройки сохранены. Токен — в Связке ключей macOS."; noticeIsError = false
        } catch { if lifetime == restoreEpoch, revision == draftRevision { showError(error.localizedDescription) } }
    }

    func deleteToken() async {
        guard !isRestoring, requiresToken, let client, action.isEmpty else { return }
        let lifetime = restoreEpoch
        let scope = scopeRevision; let revision = draftRevision
        action = "delete"; mutationScope = scopeKey; credentialRevision += 1
        defer { if lifetime == restoreEpoch { action = ""; mutationScope = nil } }
        do {
            let _: TokenStatus = try await client.send("/api/token", method: "DELETE", body: ScopeRequest(draft))
            guard lifetime == restoreEpoch, scope == scopeRevision else { return }
            tokenSaved = false; if revision == draftRevision { tokenDraft = "" }; changed()
            notice = "Сохранённый токен удалён."; noticeIsError = false
        } catch { if lifetime == restoreEpoch, scope == scopeRevision { showError(error.localizedDescription) } }
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

    private func configureBackups(_ client: APIClient) {
        backups.configure(client)
        backups.willRestore = { [weak self] in self?.beginRestoreMemory() }
        backups.restored = { [weak self] lifetime in
            guard let self else { throw CancellationError() }
            try await completeRestoreMemory(lifetime)
        }
    }

    private func beginRestoreMemory() -> UUID? {
        guard !isRestoring else { return nil }
        restoreEpoch = UUID(); let lifetime = restoreEpoch
        isRestoring = true
        memory.invalidateForRestore()
        credentialTask?.cancel(); credentialTask = nil
        quickSearch.close(restoreFocus: false)
        draftRevision += 1; scopeRevision += 1; catalogRevision += 1; credentialRevision += 1
        sourcesRevision += 1; sourcesRequest += 1
        draft = ModelSettings(); savedSettings = ModelSettings(); providers = []
        tokenDraft = ""; tokenSaved = false; models = []; connection = nil
        action = ""; mutationScope = nil; notice = nil; storageWarning = nil; sourcesLoading = false
        starting = false
        catalogLoading = false; catalogError = nil; catalogWarning = nil; catalogUpdated = nil
        sources = []; busySources = []
        return lifetime
    }

    private func completeRestoreMemory(_ lifetime: UUID) async throws {
        guard lifetime == restoreEpoch, isRestoring, let client else { throw CancellationError() }
        do {
            let result: BootstrapResponse = try await client.get("/api/bootstrap")
            guard lifetime == restoreEpoch else { throw CancellationError() }
            providers = result.providers; draft = result.settings; savedSettings = result.settings
            tokenSaved = result.tokenSaved; storageWarning = result.warning
        } catch {
            if lifetime == restoreEpoch { showError(error.localizedDescription) }
            throw error
        }
        guard lifetime == restoreEpoch else { throw CancellationError() }
        await refreshSources()
        guard lifetime == restoreEpoch else { throw CancellationError() }
        await memory.restoreDidComplete(client)
        guard lifetime == restoreEpoch else { throw CancellationError() }
        isRestoring = false
    }

    func relocateSource(_ source: MemorySource, request: SourceRelocationRequest) async throws {
        guard !isRestoring, let client else { throw CancellationError() }
        let lifetime = restoreEpoch
        let _: SourceResponse = try await client.send("/api/sources/\(source.id)/relocate", body: request)
        guard lifetime == restoreEpoch else { throw CancellationError() }
        sourcesRevision += 1; await refreshSources()
        guard lifetime == restoreEpoch else { throw CancellationError() }
        await memory.sourcePolicyDidChange()
    }

    func reauthenticateSource(_ source: MemorySource, request: SourceReauthenticationRequest) async throws {
        guard !isRestoring, let client else { throw CancellationError() }
        let lifetime = restoreEpoch
        let _: SourceResponse = try await client.send("/api/sources/\(source.id)/reauthenticate", body: request)
        guard lifetime == restoreEpoch else { throw CancellationError() }
        sourcesRevision += 1; await refreshSources()
        guard lifetime == restoreEpoch else { throw CancellationError() }
        await memory.sourcePolicyDidChange()
    }

    func sourcePolicy(_ source: MemorySource) async throws -> SourcePolicy {
        let result: SourcePolicyResponse = try await currentSourceResponse { try await $0.get("/api/sources/\(source.id)/policy") }
        return result.policy
    }

    func previewSourcePolicy(_ source: MemorySource, policy: SourcePolicy) async throws -> SourcePolicyPreview {
        try await currentSourceResponse { try await $0.send("/api/sources/\(source.id)/policy/preview", body: policy) }
    }

    func saveSourcePolicy(_ source: MemorySource, policy: SourcePolicy) async throws {
        guard !isRestoring, let client else { throw CancellationError() }
        let lifetime = restoreEpoch
        sourcesRevision += 1
        busySources = busySources.union([source.id])
        defer { if lifetime == restoreEpoch { busySources = busySources.subtracting([source.id]); sourcesRevision += 1 } }
        let _: SourcePolicyResponse = try await client.send("/api/sources/\(source.id)/policy", method: "PUT", body: policy)
        guard lifetime == restoreEpoch else { throw CancellationError() }
        await memory.sourcePolicyDidChange()
        guard lifetime == restoreEpoch else { throw CancellationError() }
        await refreshSources()
        guard lifetime == restoreEpoch else { throw CancellationError() }
        notice = "Разрешения источника сохранены. Исключённые материалы скрыты; оригиналы не изменены."
        noticeIsError = false
    }

    func appleNotesFolders() async throws -> AppleNotesFoldersResponse {
        try await currentSourceResponse { try await $0.send("/api/sources/apple-notes/folders", body: EmptyBody()) }
    }

    func startOutlook(clientID: String) async throws -> OutlookStartResponse {
        try await currentSourceResponse { try await $0.send("/api/sources/outlook/auth/start", body: OutlookStartRequest(clientId: clientID)) }
    }

    func pollOutlook(flowID: String) async throws -> OutlookPollResponse {
        try await currentSourceResponse { try await $0.send("/api/sources/outlook/auth/poll", body: OutlookFlowRequest(flowId: flowID)) }
    }

    func cancelOutlook(flowID: String) async throws {
        guard let client else { throw ServiceError("Локальный сервис не запущен.") }
        let _: OutlookCancelResponse = try await client.send("/api/sources/outlook/auth/cancel", body: OutlookFlowRequest(flowId: flowID))
    }

    func notionPages(token: String, query: String, cursor: String?) async throws -> NotionPagesResponse {
        try await currentSourceResponse { try await $0.send("/api/sources/notion/pages", body: NotionPagesRequest(token: token, query: query, cursor: cursor)) }
    }

    func githubRepositories(token: String, page: Int) async throws -> GitHubRepositoriesResponse {
        try await currentSourceResponse { try await $0.send("/api/sources/github/repositories", body: GitHubRepositoriesRequest(token: token, page: page)) }
    }

    private func currentSourceResponse<Value>(_ operation: (APIClient) async throws -> Value) async throws -> Value {
        guard !isRestoring, let client else { throw CancellationError() }
        let lifetime = restoreEpoch
        do {
            let value = try await operation(client)
            guard lifetime == restoreEpoch else { throw CancellationError() }
            return value
        } catch {
            guard lifetime == restoreEpoch else { throw CancellationError() }
            throw error
        }
    }

    func addSource(_ request: AddSourceRequest) async -> String? {
        guard !isRestoring, let client else { return "Дождитесь завершения восстановления." }
        let lifetime = restoreEpoch
        sourcesRevision += 1
        defer { if lifetime == restoreEpoch { sourcesRevision += 1 } }
        do {
            let result: SourceResponse = try await client.send("/api/sources", body: request)
            guard lifetime == restoreEpoch else { return "Операция устарела после восстановления." }
            sources = sources + [result.source]
            return nil
        } catch { return lifetime == restoreEpoch ? error.localizedDescription : "Операция устарела после восстановления." }
    }

    func checkSource(_ source: MemorySource) async {
        guard !isRestoring, let client, !busySources.contains(source.id) else { return }
        let lifetime = restoreEpoch
        sourcesRevision += 1
        busySources = busySources.union([source.id])
        defer { if lifetime == restoreEpoch { busySources = busySources.subtracting([source.id]); sourcesRevision += 1 } }
        do {
            let result: SourceResponse = try await client.send("/api/sources/\(source.id)/test", body: EmptyBody())
            guard lifetime == restoreEpoch else { return }
            sources = sources.map { $0.id == source.id ? result.source : $0 }
        } catch { if lifetime == restoreEpoch { showError(error.localizedDescription); await refreshSources() } }
    }

    func setMailImport(_ source: MemorySource, enabled: Bool) async {
        guard !isRestoring, let client, source.isMailSource, !busySources.contains(source.id) else { return }
        let lifetime = restoreEpoch
        sourcesRevision += 1
        busySources = busySources.union([source.id])
        defer { if lifetime == restoreEpoch { busySources = busySources.subtracting([source.id]); sourcesRevision += 1 } }
        do {
            let result: SourceResponse = try await client.send(
                "/api/sources/\(source.id)/mail-import",
                body: MailImportToggleRequest(enabled: enabled)
            )
            guard lifetime == restoreEpoch else { return }
            sources = sources.map { $0.id == source.id ? result.source : $0 }
            notice = enabled ? "Импорт писем включён. Можно запустить его вручную или использовать расписание памяти." : "Импорт писем приостановлен. Сохранённые материалы доступны в поиске."
            noticeIsError = false
            await memory.refreshStatus()
        } catch {
            guard lifetime == restoreEpoch else { return }
            showError(error.localizedDescription)
            await refreshSources()
        }
    }

    func removeSource(_ source: MemorySource) async {
        guard !isRestoring, let client else { return }
        let lifetime = restoreEpoch
        sourcesRevision += 1
        defer { if lifetime == restoreEpoch { sourcesRevision += 1 } }
        do {
            let _: DeletedResponse = try await client.send("/api/sources/\(source.id)", method: "DELETE", body: EmptyBody())
            guard lifetime == restoreEpoch else { return }
            sources = sources.filter { $0.id != source.id }
        } catch { if lifetime == restoreEpoch { showError(error.localizedDescription) } }
    }
}
