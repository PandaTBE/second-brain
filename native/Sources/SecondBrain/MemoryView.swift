import SwiftUI
import SecondBrainCore

struct MemoryView: View {
    @ObservedObject var store: AppStore
    @ObservedObject private var memory: MemoryViewModel
    @ViewState private var confirmCards = false
    @ViewState private var confirmRetryErrors = false
    @ViewState private var confirmedLimit = 10
    @ViewState private var showAutomation = false
    @ViewState private var showEmbeddingSettings = false
    @ViewState private var showEvidence = false
    @ViewState private var showFacts = false
    init(store: AppStore) { self.store = store; self.memory = store.memory }

    private var importableSources: [MemorySource] { store.sources.filter { $0.canImportMemory && $0.enabled } }
    private var selection: Binding<String?> { Binding(get: { memory.selectedId }, set: { id in Task { await memory.selectRecord(id) } }) }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 16) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 5) {
                        Text("Ваша память").font(.title2.weight(.semibold))
                        Text("Сессии, решения и знания с проверяемыми источниками.").foregroundStyle(.secondary).font(.callout)
                    }
                    Spacer()
                    Button { memory.openAtlas() } label: {
                        Label("Граф и история", systemImage: "point.3.connected.trianglepath.dotted")
                    }
                    .disabled(memory.atlasURL == nil)
                    .help("Локальный граф и история в браузере. Приложение должно оставаться запущенным.")
                    Menu {
                        Button("Все включённые источники") { Task { await memory.startImport() } }.disabled(importableSources.isEmpty)
                        Divider()
                        ForEach(importableSources) { source in Button(source.name) { Task { await memory.startImport(sourceId: source.id) } } }
                    } label: { Label("Импортировать", systemImage: "square.and.arrow.down") }
                        .disabled(memory.busy || importableSources.isEmpty)
                }
                HStack(spacing: 12) {
                    countCard(.all, value: memory.status?.records, symbol: "doc.text")
                    countCard(.ready, value: memory.status?.cardsReady, symbol: "rectangle.stack")
                    countCard(.pending, value: memory.status?.cardsPending, symbol: "clock")
                    countCard(.error, value: memory.status?.cardsError, symbol: "exclamationmark.circle")
                }
                autoImportBar
                providerBackoffBar
                retryErrorsBar
                HStack(spacing: 12) {
                    Stepper("Пакет: \(memory.cardBatchLimit)", value: $memory.cardBatchLimit, in: MemoryProcessingLimit.range).fixedSize()
                    Button("Одновременно: \(memory.cardConcurrency)") { showAutomation = true }
                        .buttonStyle(.link).font(.caption)
                        .disabled(memory.status == nil)
                        .help("Укажите любое положительное целое число. По умолчанию — 10; фактическая параллельность ограничена размером партии.")
                    Spacer()
                    Button {
                        confirmedLimit = memory.cardBatchLimit; confirmCards = true
                    } label: { Label(store.savedSettings.isLocal ? "Создать карточки · локально" : "Создать карточки · API", systemImage: "sparkles") }
                        .help(memory.providerBlocked ? "Создание карточек ожидает снятия паузы провайдера." : "Создать карточки для выбранного пакета материалов.")
                        .disabled(memory.providerBlocked || memory.busy || !store.hasSavedModel || (memory.status?.cardsPending ?? 0) == 0)
                }
                Text(store.savedSettings.isLocal ? "Карточки создаются моделью Ollama на этом Mac, без API-тарифа. Для локальной модели выполняется один запрос за раз; заданная параллельность сохраняется." : "Создание карточек использует выбранную модель и оплачивается по её тарифам. Лимит ограничивает материалы в одном пакете, а не сумму списания.").font(.caption).foregroundStyle(.secondary)
                if let notice = memory.notice { Label(notice, systemImage: memory.noticeIsError ? "exclamationmark.circle" : "checkmark.circle").font(.callout).foregroundStyle(memory.noticeIsError ? Color.orange : Color.green).textSelection(.enabled) }
                if let error = memory.statusError { Label(error, systemImage: "exclamationmark.circle").font(.callout).foregroundStyle(.orange) }
                if let job = memory.status?.jobs.first(where: { $0.active }) { MemoryJobView(job: job) }
                else if let job = memory.status?.jobs.first { MemoryJobView(job: job) }
                HStack {
                    HStack { Image(systemName: "magnifyingglass").foregroundStyle(.secondary); TextField("Найти решение, ошибку, проект…", text: Binding(get: { memory.query }, set: { memory.changeQuery($0) })).textFieldStyle(.plain).onSubmit { Task { await memory.search() } } }
                        .padding(8).background(.quaternary, in: RoundedRectangle(cornerRadius: 7))
                    Picker("Режим поиска", selection: Binding(get: { memory.searchMode }, set: { memory.changeSearchMode($0) })) {
                        ForEach(MemorySearchMode.allCases) { mode in Text(mode.title).tag(mode) }
                    }.labelsHidden().pickerStyle(.menu).frame(width: 140)
                    Button { showFacts = true } label: { Image(systemName: "list.bullet.rectangle") }
                        .help("Факты, решения и история подтверждений").accessibilityLabel("Факты, решения и история подтверждений")
                    Button { showEvidence = true } label: { Image(systemName: "text.quote") }
                        .help("Доказательства и контекст с цитатами").accessibilityLabel("Доказательства и контекст с цитатами")
                    Button { showEmbeddingSettings = true } label: { Image(systemName: "slider.horizontal.3") }
                        .help("Настройки поиска по смыслу").accessibilityLabel("Настройки поиска по смыслу")
                    if memory.searchLoading { ProgressView().controlSize(.small) }
                    Button { Task { await memory.refreshStatus(); await memory.search() } } label: { Image(systemName: "arrow.clockwise") }.help("Обновить память")
                }
            }.padding(22)
            Divider()
            HSplitView {
                VStack(alignment: .leading, spacing: 0) {
                    VStack(alignment: .leading, spacing: 5) {
                        HStack {
                            Text(memory.listTitle).font(.caption.weight(.medium))
                            Spacer()
                            Text("Загружено: \(memory.results.count)").font(.caption).foregroundStyle(.secondary)
                        }
                        if memory.usesRankedSearch {
                            Text("До \(MemorySearchMode.rankedResultLimit) материалов. Для страниц истории и фильтров состояния выберите «По словам».")
                                .font(.caption2).foregroundStyle(.secondary)
                        }
                        if let diagnostics = memory.searchDiagnostics {
                            Text(diagnostics.message).font(.caption2).foregroundStyle(.secondary)
                            if let fallback = diagnostics.fallbackMessage { Text(fallback).font(.caption2).foregroundStyle(.orange) }
                        }
                    }.padding(12)
                    if memory.resultsHaveUpdates {
                        HStack {
                            Text("В памяти есть обновления.").font(.caption).foregroundStyle(.secondary)
                            Spacer()
                            Button("Обновить список") { Task { await memory.search() } }.font(.caption)
                        }.padding(.horizontal, 12).padding(.bottom, 8)
                    }
                    if let error = memory.searchError {
                        VStack(alignment: .leading, spacing: 8) {
                            Text(error).font(.callout).foregroundStyle(.orange)
                            Button("Повторить загрузку") { Task { await memory.search() } }
                        }.padding(12)
                    }
                    if memory.results.isEmpty && !memory.searchLoading && memory.searchError == nil {
                        ContentUnavailableView(memory.emptyTitle, systemImage: "doc.text.magnifyingglass", description: Text(memory.emptyDescription)).frame(maxHeight: .infinity)
                    } else if memory.searchLoading {
                        ProgressView("Загружаем материалы…").frame(maxWidth: .infinity, maxHeight: .infinity)
                    } else {
                        List(selection: selection) {
                            ForEach(memory.results) { item in
                                MemoryResultRow(item: item).tag(item.id)
                                    .onAppear { Task { await memory.loadMoreIfNeeded(after: item.id) } }
                            }
                            paginationFooter
                        }.listStyle(.inset)
                    }
                }.frame(minWidth: 250, idealWidth: 330)
                MemoryRecordView(record: memory.record, loading: memory.recordLoading, error: memory.recordError,
                                 copySource: { record in await memory.copySourceURI(record) })
                    .frame(minWidth: 320, maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .sheet(isPresented: $showAutomation) { MemoryAutomationSheet(memory: memory) }
        .sheet(isPresented: $showEmbeddingSettings) { EmbeddingSettingsSheet(memory: memory) }
        .sheet(isPresented: $showFacts) {
            FactsSheet(memory: memory, sources: store.sources, localOriginalAccess: .currentDevice)
        }
        .sheet(isPresented: $showEvidence) {
            EvidenceSheet(memory: memory, sources: store.sources, initialQuery: memory.query,
                          initialMode: memory.searchMode, localOriginalAccess: .currentDevice)
        }
        .alert(store.savedSettings.isLocal ? "Создать карточки локально?" : "Создать карточки через API?", isPresented: $confirmCards) {
            Button("Отмена", role: .cancel) {}
            Button("Обработать до \(confirmedLimit)") { Task { await memory.startCards(limit: confirmedLimit, retryErrors: false) } }
        } message: { Text("До \(confirmedLimit) материалов будут отправлены модели \(store.savedSettings.modelName.isEmpty ? store.savedSettings.modelId : store.savedSettings.modelName). \(store.savedSettings.isLocal ? "Ollama обработает текст на этом Mac, используя его ресурсы." : "Это платные API-запросы.") Неизменённые готовые карточки не обрабатываются повторно.") }
        .alert("Повторить все ошибки?", isPresented: $confirmRetryErrors) {
            Button("Отмена", role: .cancel) {}
            Button("Вернуть ошибки в очередь") { Task { await memory.retryAllErrors() } }
        } message: {
            Text("В очередь будут возвращены все текущие карточки с ошибками (сейчас: \(memory.status?.cardsError ?? 0)). Для каждой будет доступна новая попытка и до двух автоматических повторов. \(store.savedSettings.isLocal ? "При включённых автокарточках Ollama обработает их на этом Mac, без API-тарифа." : "При включённых автокарточках запросы к модели оплачиваются по её тарифам.") Настройки автоматизации и пауза провайдера сохранятся.")
        }
        .task { await memory.refreshStatus(); await memory.search() }
    }

    @ViewBuilder
    private var retryErrorsBar: some View {
        if let status = memory.status, status.cardsError > 0 {
            VStack(alignment: .leading, spacing: 5) {
                HStack {
                    Label(store.hasSavedModel ? status.automaticRetryState.title : "Автоповторы ожидают модель", systemImage: "arrow.clockwise.circle")
                        .font(.callout.weight(.medium))
                    Spacer()
                    Button("Повторить все ошибки · \(status.cardsError)") { confirmRetryErrors = true }
                        .disabled(memory.busy)
                        .help(memory.busy ? "Дождитесь завершения текущей операции памяти." : "Вернуть все карточки с ошибками в очередь с новым лимитом попыток.")
                }
                Text(status.retryCountsDescription).font(.caption)
                Text(store.hasSavedModel ? status.automaticRetryState.explanation : "Выберите и сохраните модель в разделе «Модель и API».")
                    .font(.caption).foregroundStyle(.secondary)
                HStack {
                    Text("После 3 неудачных попыток карточки ждут ручного повтора.")
                        .font(.caption).foregroundStyle(.secondary)
                    if status.automaticRetryState == .disabled {
                        Button("Настроить автоматизацию") { showAutomation = true }
                            .buttonStyle(.link).font(.caption)
                    }
                }
            }
            .padding(10)
            .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
        }
    }

    @ViewBuilder
    private var providerBackoffBar: some View {
        if let provider = memory.status?.providerBackoff,
           provider.blocked || provider.concurrencyLabel != nil || provider.isLocalExecution {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: provider.blocked ? "clock.badge.exclamationmark" : provider.isLocalExecution ? "desktopcomputer" : "speedometer")
                    .foregroundStyle(.orange)
                VStack(alignment: .leading, spacing: 4) {
                    Text(provider.executionTitle)
                        .font(.callout.weight(.medium))
                    if provider.blocked {
                        Text(provider.message ?? "Провайдер временно приостановил обработку карточек.")
                            .font(.caption).foregroundStyle(.secondary)
                        if provider.requiresUserAction {
                            Text("Проверьте баланс, лимиты и доступ к модели у провайдера, затем разрешите повтор.")
                                .font(.caption).foregroundStyle(.secondary)
                        } else if let date = provider.retryDate {
                            Text("Автоповтор разрешён после \(date.formatted(date: .abbreviated, time: .standard)).")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Text("Пауза касается карточек. Импорт и поиск через MCP доступны.")
                            .font(.caption).foregroundStyle(.secondary)
                        Text("Кнопка снимает паузу. Прошлые ошибки повторяются отдельно.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    if provider.isLocalExecution {
                        Text("Ollama работает на этом Mac. Один запрос за раз ограничивает расход памяти; платный облачный провайдер не используется.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    if let label = provider.concurrencyLabel {
                        Text(label).font(.caption).foregroundStyle(.secondary)
                    }
                }
                Spacer()
                if provider.blocked {
                    Button("Разрешить повтор") { Task { await memory.resumeProvider() } }
                        .disabled(!memory.action.isEmpty)
                        .help("Снять паузу провайдера. При включённых автокарточках запросы возобновятся автоматически.")
                }
            }.padding(12).background(Color.orange.opacity(0.08), in: RoundedRectangle(cornerRadius: 9))
        }
    }

    private var autoImportBar: some View {
        let available = memory.status != nil && memory.statusError == nil
        return HStack(spacing: 10) {
            Image(systemName: memory.synchronizationPaused ? "pause.circle" : "arrow.triangle.2.circlepath")
                .foregroundStyle(available ? Color.green : Color.secondary)
            VStack(alignment: .leading, spacing: 4) {
                Text(!available ? "Проверяем состояние синхронизации" : memory.synchronizationPaused ? "Синхронизация остановлена" : "Сервис запущен и готов к работе").font(.callout.weight(.medium))
                if memory.synchronizationPaused {
                    Text("Автоимпорт и автоматическая обработка карточек на паузе. Поиск через MCP и материалы доступны.").font(.caption).foregroundStyle(.secondary)
                    Text("Уже начатые запросы модели могут завершиться; новые автоматические запросы не начнутся.").font(.caption).foregroundStyle(.secondary)
                } else {
                    Text("Поиск через MCP доступен.").font(.caption).foregroundStyle(.secondary)
                    if memory.status?.scheduler.autoImportEnabled != false {
                        Text("Изменения сессий подхватываются после паузы в записи около \(memory.status?.scheduler.quietSeconds ?? 90) секунд.").font(.caption).foregroundStyle(.secondary)
                    } else { Text("Автоимпорт сессий выключен в настройках автоматизации.").font(.caption).foregroundStyle(.secondary) }
                    if memory.status?.scheduler.enabled == true { Text("Полная сверка локальных источников каждые \(memory.status?.scheduler.intervalMinutes ?? 15) минут.").font(.caption).foregroundStyle(.secondary) }
                    if memory.status?.scheduler.processCards == true { Text(store.savedSettings.isLocal ? "Локальные автокарточки включены: до \(memory.status?.scheduler.batchLimit ?? 10) материалов за запуск." : "Автокарточки через API включены: до \(memory.status?.scheduler.batchLimit ?? 10) материалов за запуск.").font(.caption).foregroundStyle(.orange) }
                }
            }
            Spacer()
            Button(memory.synchronizationPaused ? "Возобновить синхронизацию" : "Остановить синхронизацию") {
                Task { await memory.setSynchronizationPaused(!memory.synchronizationPaused) }
            }.disabled(memory.status == nil || !memory.action.isEmpty)
                .help("Пауза сохраняет настройки автоматизации и оставляет поиск через MCP доступным.")
            Button { showAutomation = true } label: { Image(systemName: "slider.horizontal.3") }.help("Настроить автоматизацию").disabled(memory.status == nil)
        }.padding(12).background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 9))
    }

    @ViewBuilder
    private var paginationFooter: some View {
        if memory.loadMoreLoading {
            HStack { Spacer(); ProgressView().controlSize(.small); Text("Загружаем ещё…").font(.caption).foregroundStyle(.secondary); Spacer() }
                .padding(.vertical, 8)
        } else if let error = memory.loadMoreError {
            VStack(alignment: .leading, spacing: 8) {
                Text(error).font(.caption).foregroundStyle(.orange)
                HStack {
                    Button("Повторить подгрузку") { Task { await memory.loadMore() } }
                    Button("Обновить список") { Task { await memory.search() } }
                }
            }.padding(.vertical, 8)
        } else if memory.hasMore {
            Button("Загрузить ещё") { Task { await memory.loadMore() } }
                .frame(maxWidth: .infinity).padding(.vertical, 8)
                .help("Материалы также подгружаются автоматически при прокрутке.")
        } else if !memory.results.isEmpty {
            Text("Все материалы загружены").font(.caption).foregroundStyle(.secondary)
                .frame(maxWidth: .infinity).padding(.vertical, 8)
        }
    }

    private func countCard(_ filter: MemoryBrowseFilter, value: Int?, symbol: String) -> some View {
        let selected = memory.filter == filter
        return Button {
            Task { await memory.changeFilter(filter) }
        } label: {
            GroupBox {
                VStack(alignment: .leading, spacing: 8) {
                    Label(filter.title, systemImage: symbol).font(.caption).foregroundStyle(selected ? Color.accentColor : Color.secondary)
                    Text(value.map(String.init) ?? "—").font(.title2.weight(.semibold)).monospacedDigit()
                }.frame(maxWidth: .infinity, alignment: .leading).padding(5)
            }
            .overlay(RoundedRectangle(cornerRadius: 7).stroke(selected ? Color.accentColor : Color.clear, lineWidth: 2))
            .contentShape(RoundedRectangle(cornerRadius: 7))
        }
        .buttonStyle(.plain)
        .frame(maxWidth: .infinity)
        .accessibilityLabel(filter.title)
        .accessibilityValue("\(value.map(String.init) ?? "Нет данных")\(selected ? ", выбран" : "")")
        .accessibilityHint("Показать материалы в этом разделе.")
        .accessibilityAddTraits(.isButton)
        .accessibilityAddTraits(selected ? .isSelected : [])
        .disabled(memory.usesRankedSearch)
        .help(memory.usesRankedSearch ? "Для фильтров состояния выберите режим «По словам»." : "Показать: \(filter.title.lowercased())")
    }
}

private struct MemoryResultRow: View {
    let item: MemoryRecord
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(item.card?.title ?? item.title).font(.callout.weight(.medium)).lineLimit(2)
            Text(item.displaySnippet).font(.caption).foregroundStyle(.secondary).lineLimit(3)
            HStack { Text(SourceFormatting.label(item.sourceKind ?? "")); Spacer(); Image(systemName: item.cardStatus == "ready" ? "rectangle.stack.fill" : item.cardStatus == "error" ? "exclamationmark.circle" : "clock") }.font(.caption2).foregroundStyle(.secondary)
        }.padding(.vertical, 5)
    }
}

struct MemoryJobView: View {
    let job: MemoryJob
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack { Label(job.title, systemImage: job.active ? "arrow.triangle.2.circlepath" : job.status == "error" ? "exclamationmark.circle" : "checkmark.circle").font(.callout.weight(.medium)); Spacer(); Text(job.active ? job.progressLabel : job.status == "error" ? "Ошибка" : "Завершено").font(.caption).foregroundStyle(.secondary) }
            if job.active { ProgressView(value: Double(job.processed ?? 0), total: Double(max(job.total ?? 1, 1))) }
            if let concurrency = job.concurrencyLabel { Text(concurrency).font(.caption).foregroundStyle(.secondary).monospacedDigit() }
            if let error = job.error { Text(error).font(.caption).foregroundStyle(.orange).textSelection(.enabled) }
            if job.kind == "import", !job.active, job.stats != nil {
                Text("Новых: \(job.importCounter("imported")) · обновлено: \(job.importCounter("updated")) · без изменений: \(job.importCounter("unchanged")) · пропущено: \(job.importCounter("skipped")) · ошибок: \(job.importCounter("errors"))").font(.caption).foregroundStyle(.secondary)
            }
        }.padding(12).background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
    }
}
