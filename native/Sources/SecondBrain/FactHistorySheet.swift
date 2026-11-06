import SwiftUI
import SecondBrainCore

struct FactHistorySheet: View {
    @ObservedObject var memory: MemoryViewModel
    let fact: MemoryFactIdentity
    let localOriginalAccess: LocalOriginalAccess
    @Environment(\.dismiss) private var dismiss
    @ViewState private var history = MemoryFactHistoryState()
    @ViewState private var task: Task<Void, Never>?
    @ViewState private var isActive = false
    @ViewState private var checking = false
    @ViewState private var material: FactMaterialSnapshot?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("История подтверждений").font(.title2.weight(.semibold))
            if let authorized = history.page?.fact.text {
                Text(verbatim: authorized).font(.headline).textSelection(.enabled)
            }
            Text("Наблюдения из всех доступных источников. Снимки прошлых ревизий сохраняются отдельно от текущего материала; более поздняя дата сама по себе не отменяет решение.")
                .font(.caption).foregroundStyle(.secondary)
            if let page = history.page {
                Text("История собирается с \(page.historySince.formatted(date: .abbreviated, time: .shortened)). Предыдущие версии до включения функции не восстанавливаются.")
                    .font(.caption2).foregroundStyle(.secondary)
            }
            HStack { Text("Наблюдений загружено: \(history.events.count)").font(.caption); Spacer(); Button("Обновить историю") { loadFirst() }.disabled(history.isLoading || checking) }
            Divider()
            results.frame(maxWidth: .infinity, maxHeight: .infinity)
            HStack { Spacer(); Button("Закрыть") { close() }.keyboardShortcut(.cancelAction) }
        }.padding(24).frame(width: 800, height: 730)
        .task { guard !Task.isCancelled else { return }; isActive = true; loadFirst() }
        .onDisappear { invalidate() }
        .sheet(item: $material) { selected in
            FactCurrentMaterialSheet(memory: memory, snapshot: selected, localOriginalAccess: localOriginalAccess)
        }
    }
    @ViewBuilder private var results: some View {
        if checking || (history.isLoading && history.events.isEmpty) {
            ProgressView(checking ? "Проверяем доступ к снимку и текущему материалу…" : "Загружаем историю…")
        } else if let error = history.error {
            ContentUnavailableView("Не удалось выполнить действие", systemImage: "exclamationmark.circle", description: Text(error))
        } else if history.events.isEmpty {
            ContentUnavailableView("Наблюдения не найдены", systemImage: "clock", description: Text("История появляется после обновления фактов из карточек и учитывает текущие разрешения источников."))
        } else {
            ScrollView {
                // Eager rows keep fetched history available to Accessibility scroll actions.
                // SwiftUI's lazy AX materialization can trap while resolving off-screen ForEach rows.
                VStack(alignment: .leading, spacing: 14) {
                    ForEach(history.events) { event in eventView(event) }
                    if history.isLoading { ProgressView("Загружаем более ранние наблюдения…") }
                    else if history.nextCursor != nil { Button("Загрузить более ранние наблюдения") { loadMore() } }
                    if history.page?.truncated == true { Text("Страница ограничена по числу наблюдений и размеру. Более ранние записи доступны следующей страницей, если есть продолжение.").font(.caption2).foregroundStyle(.secondary) }
                }.padding(.vertical, 4)
            }
        }
    }
    private func eventView(_ event: MemoryFactHistoryEvent) -> some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 9) {
                Text(event.snapshotLabel).font(.callout.weight(.medium)).foregroundStyle(event.active ? Color.primary : Color.secondary)
                Text("Наблюдалось в Second Brain: \(event.observedAt.formatted(date: .abbreviated, time: .shortened))")
                    .font(.caption).foregroundStyle(.secondary)
                Text("Статус наблюдения: \(event.status.title)").font(.caption).foregroundStyle(.secondary)
                FactProofView(proof: event.proof, busy: checking || history.isLoading) { showCurrent(event) }
                if let relations = event.relations, !relations.isEmpty {
                    DisclosureGroup("Связи при этом наблюдении") {
                        VStack(alignment: .leading, spacing: 12) {
                            ForEach(Array(relations.enumerated()), id: \.offset) { _, relation in
                                VStack(alignment: .leading, spacing: 5) {
                                    Text("Историческая связь · \(relation.title)").font(.caption.weight(.medium)).foregroundStyle(.secondary)
                                    Text(verbatim: relation.targetText).font(.callout).textSelection(.enabled)
                                    if relation.suggestion { Text("Предложенная связь — требует проверки.").font(.caption2).foregroundStyle(.secondary) }
                                    FactProofView(proof: relation.evidence, busy: true)
                                }
                            }
                        }.padding(.top, 5)
                    }.font(.caption)
                }
                if event.relationsTruncated == true {
                    Text("Часть связей наблюдения не поместилась в пакет истории.").font(.caption2).foregroundStyle(.secondary)
                }
                DisclosureGroup("Обработка карточки") {
                    Text(verbatim: "Модель: \(event.model) · версия промпта: \(event.promptVersion)")
                        .font(.caption2).foregroundStyle(.secondary).textSelection(.enabled)
                }.font(.caption)
            }.padding(6).frame(maxWidth: .infinity, alignment: .leading)
        }
    }
    private func invalidate() { isActive = false; task?.cancel(); history.invalidate(); checking = false }
    private func close() { invalidate(); dismiss() }
    private func current(_ request: MemoryFactHistoryState.Request) -> Bool {
        isActive && !Task.isCancelled && request.factId == fact.id && history.isCurrent(request)
    }
    private func loadFirst() {
        guard isActive else { return }
        task?.cancel(); checking = false
        load(history.begin(factId: fact.id))
    }
    private func loadMore() {
        guard isActive, !checking, let request = history.beginNextPage() else { return }
        load(request)
    }
    private func load(_ request: MemoryFactHistoryState.Request) {
        task = Task {
            do {
                let page = try await memory.factHistory(request.factId, before: request.before)
                guard current(request) else { return }
                try history.accept(page, request: request)
            } catch { if current(request) { history.fail(error.localizedDescription, request: request) } }
        }
    }
    private func showCurrent(_ selected: MemoryFactHistoryEvent) {
        guard isActive, !checking, let request = history.beginRefresh(eventId: selected.id) else { return }
        checking = true; task?.cancel()
        task = Task {
            defer { if isActive, history.isCurrent(request) { checking = false } }
            do {
                let page = try await memory.factHistory(request.factId, before: request.before)
                guard current(request), try history.accept(page, request: request) else { return }
                guard let fresh = page.events.first(where: { $0.id == selected.id }), fresh.proof.sameIdentity(as: selected.proof) else {
                    throw ServiceError("Наблюдение изменилось или больше недоступно. Обновите историю.")
                }
                let record = try await memory.currentFactRecord(fresh.proof.recordId)
                guard current(request) else { return }
                if fresh.active {
                    guard fresh.proof.matchesCurrent(record) else { throw ServiceError("Действующий материал изменился. Обновите факты из карточек.") }
                } else {
                    // Historical source text is a saved observation, not a claim about this revision.
                    guard record.sourceId == fresh.proof.sourceId else { throw ServiceError("Текущий материал относится к другому источнику.") }
                }
                material = FactMaterialSnapshot(record: record, historicalRevision: fresh.active ? nil : fresh.proof.revision)
            } catch {
                if current(request) { checking = false; history.fail(error.localizedDescription, request: request) }
            }
        }
    }
}
