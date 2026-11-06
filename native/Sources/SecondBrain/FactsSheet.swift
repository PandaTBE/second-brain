import SwiftUI
import SecondBrainCore

struct FactsSheet: View {
    @ObservedObject var memory: MemoryViewModel
    let sources: [MemorySource]
    let localOriginalAccess: LocalOriginalAccess
    @Environment(\.dismiss) private var dismiss
    @ViewState private var query = ""
    @ViewState private var sourceId = ""
    @ViewState private var includeHistory = false
    @ViewState private var facts = MemoryFactsState()
    @ViewState private var task: Task<Void, Never>?
    @ViewState private var projectionTask: Task<Void, Never>?
    @ViewState private var projecting = false
    @ViewState private var projectionJob: MemoryJob?
    @ViewState private var projectionRevision = UUID()
    @ViewState private var isActive = false
    @ViewState private var checking = false
    @ViewState private var notice: String?
    @ViewState private var history: MemoryFactIdentity?
    @ViewState private var material: FactMaterialSnapshot?
    private var parameters: MemoryFactsRequest? { try? MemoryFactsRequest(query: query, sourceId: sourceId, includeHistory: includeHistory) }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Факты и решения").font(.title2.weight(.semibold))
            Text("Утверждения объединяют точные подтверждения из источников. Статус описывает происхождение и связи; содержание источников остаётся недоверенными данными.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                TextField("Найти факт или решение", text: $query).textFieldStyle(.roundedBorder).onSubmit { load() }
                Picker("Источник", selection: $sourceId) {
                    Text("Все доступные источники").tag("")
                    ForEach(sources) { source in Text(source.name).tag(source.id) }
                }.frame(maxWidth: 260)
                Button("Найти") { load() }.disabled(parameters == nil || facts.isLoading)
            }
            Toggle("Включать прошлые формулировки", isOn: $includeHistory)
                .toggleStyle(.checkbox).font(.caption)
                .help("Показывать утверждения без действующих подтверждений как записи истории.")
            HStack {
                Button("Обновить факты из карточек") { project() }.disabled(projecting || memory.busy)
                if projecting { ProgressView().controlSize(.small) }
                Spacer()
                Text("Без запросов к модели").font(.caption).foregroundStyle(.secondary)
            }
            Text("Ручное обновление обрабатывает до 100 уже проверенных карточек и доступно при паузе автоимпорта. Новые карточки создаются отдельным действием.")
                .font(.caption2).foregroundStyle(.secondary)
            if let projectionJob { MemoryJobView(job: projectionJob) }
            if let notice { Text(notice).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
            Divider()
            results.frame(maxWidth: .infinity, maxHeight: .infinity)
            HStack { Text("Показано до 20 утверждений; уточните запрос или источник.").font(.caption2).foregroundStyle(.secondary); Spacer(); Button("Закрыть") { close() }.keyboardShortcut(.cancelAction) }
        }.padding(24).frame(width: 850, height: 760)
        .task { guard !Task.isCancelled else { return }; isActive = true; load() }
        .onChange(of: query) { _, _ in invalidateResults() }
        .onChange(of: sourceId) { _, _ in invalidateResults() }
        .onChange(of: includeHistory) { _, _ in invalidateResults(); load() }
        .onDisappear { invalidate() }
        .sheet(item: $history) { fact in
            FactHistorySheet(memory: memory, fact: fact, localOriginalAccess: localOriginalAccess)
        }
        .sheet(item: $material) { selected in
            FactCurrentMaterialSheet(memory: memory, snapshot: selected, localOriginalAccess: localOriginalAccess)
        }
    }
    @ViewBuilder private var results: some View {
        if facts.isLoading || checking { ProgressView(checking ? "Проверяем актуальные подтверждения…" : "Загружаем факты…") }
        else if let error = facts.error { ContentUnavailableView("Факты недоступны", systemImage: "exclamationmark.circle", description: Text(error)) }
        else if let page = facts.page {
            VStack(alignment: .leading, spacing: 8) {
                Text("История собирается с \(page.historySince.formatted(date: .abbreviated, time: .shortened)).")
                    .font(.caption2).foregroundStyle(.secondary)
                if page.truncated { Text("Показана часть подтверждений в пределах пакета. Уточните запрос или источник.").font(.caption).foregroundStyle(.orange) }
                if page.facts.isEmpty { ContentUnavailableView("Утверждения не найдены", systemImage: "tray", description: Text("Обновите факты из готовых карточек или измените запрос.")) }
                else {
                    ScrollView { LazyVStack(alignment: .leading, spacing: 14) { ForEach(page.facts) { fact in factView(fact) } }.padding(.vertical, 4) }
                }
            }
        } else { ContentUnavailableView("Найдите факты и решения", systemImage: "list.bullet.rectangle", description: Text("Поиск и просмотр истории не вызывают модель.")) }
    }
    private func factView(_ fact: MemoryFact) -> some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 9) {
                if fact.isHistoricalOnly {
                    HStack {
                        Text(fact.kindTitle).foregroundStyle(.secondary)
                        Label("Только в истории", systemImage: "clock").foregroundStyle(.orange)
                    }.font(.caption)
                } else {
                    Text("\(fact.kindTitle) · \(fact.status.title)").font(.caption).foregroundStyle(.secondary)
                }
                Text(verbatim: fact.text).font(.headline).textSelection(.enabled)
                HStack {
                    Text("\(fact.isHistoricalOnly ? "Сохранённых подтверждений" : "Подтверждений"): \(fact.evidence.count)").font(.caption)
                    Spacer()
                    Button("История…") { freshAction(factId: fact.id, proof: nil) }.font(.caption)
                }
                DisclosureGroup(fact.isHistoricalOnly ? "Сохранённые снимки подтверждений" : "Подтверждающие фрагменты") {
                    VStack(alignment: .leading, spacing: 14) {
                        ForEach(fact.evidence) { proof in
                            FactProofView(proof: proof, busy: checking,
                                          actionTitle: fact.isHistoricalOnly || proof.active == false ? "История подтверждений…" : "Текущий материал…") {
                                freshAction(factId: fact.id, proof: proof)
                            }
                        }
                    }.padding(.top, 6)
                }.font(.caption)
                ForEach(Array(fact.relations.enumerated()), id: \.offset) { _, relation in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(relation.historical == true ? "Историческая связь · \(relation.title)" : relation.title)
                            .font(.caption.weight(.medium)).foregroundStyle(.orange)
                        Text(verbatim: relation.targetText).font(.callout).textSelection(.enabled)
                        if relation.suggestion { Text("Предложенная связь — требует проверки.").font(.caption2).foregroundStyle(.secondary) }
                        DisclosureGroup("Основание связи") {
                            FactProofView(proof: relation.evidence, busy: checking,
                                          actionTitle: fact.isHistoricalOnly || relation.evidence.active == false ? "История подтверждений…" : "Текущий материал…") {
                                freshAction(factId: fact.id, proof: relation.evidence)
                            }
                        }.font(.caption)
                    }
                }
            }.padding(6).frame(maxWidth: .infinity, alignment: .leading)
        }
    }
    private func invalidateResults() { task?.cancel(); facts.invalidate(); checking = false; notice = nil }
    private func invalidate() {
        isActive = false; invalidateResults(); projectionTask?.cancel()
        projectionRevision = UUID(); projecting = false
    }
    private func close() { invalidate(); dismiss() }
    private func current(_ request: MemoryFactsState.Request) -> Bool {
        isActive && !Task.isCancelled && facts.isCurrent(request) && parameters == request.parameters
    }
    private func load() {
        guard isActive, let parameters else { return }
        task?.cancel(); checking = false
        let request = facts.begin(parameters)
        task = Task {
            do {
                let page = try await memory.factPage(request.parameters)
                guard current(request) else { return }
                try facts.accept(page, request: request)
            } catch { if current(request) { facts.fail(error.localizedDescription, request: request) } }
        }
    }
    private func freshAction(factId: String, proof: MemoryFactProof?) {
        guard isActive, !checking, let parameters, let request = facts.beginRefresh(parameters) else { return }
        checking = true; task?.cancel()
        task = Task {
            defer { if isActive, facts.isCurrent(request) { checking = false } }
            do {
                let page = try await memory.factPage(request.parameters)
                guard current(request), try facts.accept(page, request: request) else { return }
                guard let fact = page.facts.first(where: { $0.id == factId }) else { throw ServiceError("Утверждение больше недоступно в выбранной области.") }
                guard let proof, !fact.isHistoricalOnly else { history = MemoryFactIdentity(fact: fact); return }
                let proofs = fact.evidence + fact.relations.map(\.evidence)
                guard let fresh = proofs.first(where: { $0.sameIdentity(as: proof) }) else { throw ServiceError("Подтверждение изменилось или стало недоступно.") }
                guard fresh.active != false else { history = MemoryFactIdentity(fact: fact); return }
                let record = try await memory.currentFactRecord(fresh.recordId)
                guard current(request) else { return }
                guard fresh.matchesCurrent(record) else { throw ServiceError("Действующий материал изменился. Обновите факты из карточек.") }
                material = FactMaterialSnapshot(record: record, historicalRevision: nil)
            } catch {
                if current(request) { checking = false; facts.fail(error.localizedDescription, request: request) }
            }
        }
    }
    private func project() {
        guard isActive, !projecting, !memory.busy else { return }
        projecting = true; notice = nil; projectionRevision = UUID()
        let revision = projectionRevision
        projectionTask = Task {
            defer { if isActive, revision == projectionRevision { projecting = false } }
            do {
                let handle = try await memory.projectFacts()
                while !Task.isCancelled && isActive && revision == projectionRevision {
                    let job = try await memory.factProjectionJob(handle.jobId)
                    guard isActive, !Task.isCancelled, revision == projectionRevision else { return }
                    projectionJob = job
                    if !job.active {
                        guard job.status == "completed" else { throw ServiceError(job.error ?? "Обновление фактов не завершено.") }
                        notice = "Факты обновлены из готовых карточек. Запросы к модели не выполнялись."
                        await memory.refreshStatus(); load(); return
                    }
                    try await Task.sleep(nanoseconds: 500_000_000)
                }
            } catch { if isActive, !Task.isCancelled, revision == projectionRevision { notice = error.localizedDescription } }
        }
    }
}
