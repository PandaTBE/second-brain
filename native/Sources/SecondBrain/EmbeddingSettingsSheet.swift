import SwiftUI
import SecondBrainCore

struct EmbeddingSettingsSheet: View {
    @ObservedObject var memory: MemoryViewModel
    @Environment(\.dismiss) private var dismiss
    @ViewState private var index = EmbeddingSettingsState()
    @ViewState private var catalogTask: Task<Void, Never>?
    @ViewState private var loading = true
    @ViewState private var saving = false
    @ViewState private var indexing = false
    @ViewState private var error: String?
    @ViewState private var catalogError: String?
    @ViewState private var notice: String?
    @ViewState private var statusRevision = 0

    private var canSave: Bool {
        !loading && !saving && !indexing && !index.selection.isLoading && index.settings != nil
            && (!index.enabled || index.selection.canEnable || (index.settings?.enabled == true && index.selection.selectedId == index.settings?.modelId && index.selection.selectedId != nil))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label("Поиск по смыслу", systemImage: "sparkle.magnifyingglass").font(.title2.weight(.semibold))
            Text("Отдельная локальная embedding-модель создаёт поисковый индекс. Модель карточек и её API-настройки сохраняются.")
                .font(.callout).foregroundStyle(.secondary)
            if loading {
                ProgressView("Загружаем настройки поиска…").frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let settings = index.settings {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        Toggle("Включить локальный семантический индекс", isOn: $index.enabled).toggleStyle(.checkbox)
                        Text("Работает через Ollama на этом Mac. Включение проверяет выбранную модель; затем разрешённые материалы индексируются в фоне. Автоматическая загрузка моделей и облачная обработка не используются.")
                            .font(.caption).foregroundStyle(.secondary)
                        modelPicker
                        indexStatus(settings)
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
            } else {
                ContentUnavailableView("Настройки недоступны", systemImage: "exclamationmark.circle", description: Text("Запустите локальный сервис и повторите загрузку."))
                Button("Повторить") { Task { await load() } }
            }
            if let error { Text(error).font(.callout).foregroundStyle(.orange).textSelection(.enabled) }
            if let notice { Text(notice).font(.callout).foregroundStyle(.secondary).textSelection(.enabled) }
            HStack {
                if saving { ProgressView().controlSize(.small); Text("Проверяем и сохраняем модель…").font(.caption) }
                Spacer()
                Button("Закрыть") { dismiss() }.keyboardShortcut(.cancelAction).disabled(saving || indexing)
                Button("Сохранить настройки поиска") { Task { await save() } }
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(!canSave)
            }
        }
        .padding(24).frame(width: 660, height: 650)
        .disabled(saving).interactiveDismissDisabled(saving || indexing)
        .task {
            await load()
            while !Task.isCancelled {
                do { try await Task.sleep(nanoseconds: 3_000_000_000) } catch { return }
                if !saving && !indexing { await refreshStatus() }
            }
        }
        .onDisappear { catalogTask?.cancel(); index.selection.invalidate(); statusRevision += 1 }
    }

    private var modelPicker: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Модель для embeddings").font(.headline)
                Spacer()
                Button("Обновить локальные модели") { loadModels() }.disabled(index.selection.isLoading || indexing)
                if index.selection.isLoading { ProgressView().controlSize(.small) }
            }
            Picker("Установленная модель", selection: Binding(get: { index.selection.selectedId }, set: { id in
                if let id { _ = index.selection.select(id) }
            })) {
                Text("Выберите установленную модель").tag(String?.none)
                if let selected = index.selection.selectedId, !index.selection.models.contains(where: { $0.id == selected }) {
                    Text("Сохранённая: \(selected)").tag(Optional(selected))
                }
                ForEach(index.selection.models) { model in Text(model.name).tag(Optional(model.id)) }
            }.disabled(index.selection.isLoading || index.selection.models.isEmpty || indexing)
            if let catalogError { Text(catalogError).font(.caption).foregroundStyle(.orange).textSelection(.enabled) }
            if index.selection.didLoad && index.selection.models.isEmpty {
                Text("Локальные модели не найдены. Установите Ollama и подходящую embedding-модель самостоятельно, затем обновите список.")
                    .font(.caption).foregroundStyle(.secondary)
            } else {
                Text("Список содержит только установленные локальные модели. Перед включением проверяется поддержка embeddings и размерность; обычная модель карточек может не поддерживать эту операцию.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func indexStatus(_ state: EmbeddingSettings) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Состояние индекса").font(.headline)
            Text(state.enabled ? "Семантический индекс включён" : "Семантический индекс выключен — поиск по словам доступен")
                .font(.callout)
            Text("Готово: \(state.ready) · ожидают: \(state.pending) · ошибки: \(state.errors)")
                .font(.callout).monospacedDigit().accessibilityAddTraits(.updatesFrequently)
            if let message = state.message { Text(message).font(.caption).foregroundStyle(.secondary) }
            if let model = state.modelId {
                LabeledContent("Модель индекса", value: model).font(.caption)
            }
            if let dimension = state.dimension {
                LabeledContent("Размерность", value: "\(dimension)").font(.caption)
            }
            DisclosureGroup("Сведения об индексе") {
                VStack(alignment: .leading, spacing: 5) {
                    if let digest = state.digest { Text("Версия модели: \(digest)").textSelection(.enabled) }
                    if let generation = state.generation { Text("Поколение индекса: \(generation)").textSelection(.enabled) }
                    Text("Версия подготовки текста: \(state.prepVersion)")
                }.font(.caption.monospaced()).foregroundStyle(.secondary)
            }
            HStack {
                Button("Индексировать следующий пакет") { Task { await startIndex() } }
                    .disabled(!state.enabled || memory.busy || memory.synchronizationPaused || indexing || state.pending == 0)
                if indexing { ProgressView().controlSize(.small) }
                Spacer()
                Button("Обновить состояние") { Task { await refreshStatus() } }.disabled(indexing)
            }
            Text(memory.synchronizationPaused ? "Синхронизация приостановлена. Возобновите её в меню приложения или настройках памяти." : "Фоновая индексация соблюдает общую паузу синхронизации и разрешение локальной обработки каждого источника.")
                .font(.caption).foregroundStyle(.secondary)
            Text("Пока индекс готовится или Ollama недоступна, поиск продолжает находить совпадения по словам. При смене модели строится новое поколение индекса.")
                .font(.caption).foregroundStyle(.secondary)
            Text("После перезапуска Ollama снова сохраните выбранную модель, затем возобновите индексацию. Это повторит неудавшиеся задания, сохранив готовые векторы; менять модель не требуется.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private func load() async {
        loading = true; error = nil; statusRevision += 1
        let request = statusRevision
        do {
            let response = try await memory.embeddingSettings()
            guard !Task.isCancelled, request == statusRevision else { return }
            index.acceptStatus(response)
        } catch { if !Task.isCancelled, request == statusRevision { self.error = error.localizedDescription } }
        if !Task.isCancelled, request == statusRevision { loading = false }
    }

    private func refreshStatus() async {
        statusRevision += 1
        let request = statusRevision
        do {
            let response = try await memory.embeddingSettings()
            guard !Task.isCancelled, request == statusRevision else { return }
            index.acceptStatus(response)
        } catch { if !Task.isCancelled, request == statusRevision { self.error = error.localizedDescription } }
    }

    private func loadModels() {
        catalogTask?.cancel(); catalogError = nil
        let request = index.selection.begin()
        catalogTask = Task {
            do {
                let response = try await memory.embeddingModels()
                guard !Task.isCancelled else { return }
                if index.selection.accept(response, request: request) { catalogError = response.message }
            } catch {
                if !Task.isCancelled, index.selection.fail(request) { catalogError = error.localizedDescription }
            }
        }
    }

    private func save() async {
        guard canSave else { return }
        saving = true; error = nil; notice = nil; statusRevision += 1
        defer { saving = false }
        do {
            let response = try await memory.saveEmbeddingSettings(EmbeddingSettingsRequest(enabled: index.enabled, modelId: index.selection.selectedId))
            index.acceptSaved(response)
            notice = response.enabled ? "Локальная модель проверена и сохранена. Индекс будет подготовлен в фоне." : "Семантический индекс выключен. Поиск по словам доступен."
        } catch { self.error = error.localizedDescription }
    }

    private func startIndex() async {
        guard !indexing, !memory.busy, !memory.synchronizationPaused else { return }
        indexing = true; error = nil; notice = nil
        defer { indexing = false }
        do {
            let _ = try await memory.startEmbeddingIndex()
            notice = "Индексация следующего пакета запущена."
            await refreshStatus()
        } catch { self.error = error.localizedDescription }
    }
}
