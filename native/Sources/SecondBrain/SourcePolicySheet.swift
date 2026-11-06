import SwiftUI
import SecondBrainCore

struct SourcePolicySheet: View {
    @ObservedObject var store: AppStore
    let source: MemorySource
    @Environment(\.dismiss) private var dismiss
    @ViewState private var original: SourcePolicy?
    @ViewState private var draft = SourcePolicy()
    @ViewState private var pathsText = ""
    @ViewState private var notesExcluded: Set<String> = []
    @ViewState private var preview = SourcePolicyPreviewState()
    @ViewState private var previewTask: Task<Void, Never>?
    @ViewState private var loading = true
    @ViewState private var saving = false
    @ViewState private var error: String?

    private var localSource: Bool { ["codex", "claude", "qwen", "folder"].contains(source.kind) }
    private var candidate: SourcePolicy? {
        let paths = pathsText.components(separatedBy: .newlines).filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
        let folderOrder = draft.excludedNotesFolderIds + (source.notesFolderIds ?? [])
        let folderIDs = folderOrder.reduce([String]()) { result, id in
            notesExcluded.contains(id) && !result.contains(id) ? result + [id] : result
        }
        return try? draft.excluding(paths: paths, notesFolderIDs: folderIDs)
    }
    private var canSave: Bool {
        guard let original, let candidate else { return false }
        return preview.canSave(candidate, original: original) && !loading && !saving && !preview.isLoading
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label("Разрешения источника", systemImage: "hand.raised").font(.title2.weight(.semibold))
            Text(source.name).font(.headline)
            if loading {
                ProgressView("Загружаем действующие разрешения…").frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if original != nil {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        permissionControls
                        exclusions
                        previewControls
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
            } else {
                ContentUnavailableView("Разрешения недоступны", systemImage: "exclamationmark.circle", description: Text("Повторите загрузку после запуска локального сервиса."))
                Button("Повторить") { Task { await load() } }
            }
            if let error { Text(error).font(.callout).foregroundStyle(.orange).textSelection(.enabled) }
            HStack {
                if saving { ProgressView().controlSize(.small) }
                Spacer()
                Button("Отмена") { dismiss() }.keyboardShortcut(.cancelAction).disabled(saving)
                Button("Сохранить") { Task { await save() } }
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(!canSave)
            }
        }
        .padding(24).frame(width: 660, height: 720)
        .disabled(saving).interactiveDismissDisabled(saving)
        .task { await load() }
        .onDisappear { previewTask?.cancel(); preview.invalidate() }
    }

    private var permissionControls: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(SourcePermission.allCases) { permission in
                Toggle(permission.title, isOn: Binding(
                    get: { draft.allows(permission) },
                    set: { draft = draft.setting(permission, allowed: $0); invalidatePreview() }
                )).toggleStyle(.checkbox)
            }
            Text("MCP передаёт текст запрашивающему агенту. Codex или Claude могут обрабатывать его в облаке, даже если локальная обработка включена, а внешняя обработка карточек выключена.")
                .font(.caption).foregroundStyle(.secondary)
            Text("Разрешение обработки само по себе не запускает модель. Запрет внешней обработки сохраняет локальную память и не отменяет уже отправленный запрос.")
                .font(.caption).foregroundStyle(.secondary)
            if source.isMailSource && source.mailImportEnabled != true {
                Text("Импорт писем также приостановлен отдельной настройкой подключения. Его включение здесь не изменяет эту настройку.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder private var exclusions: some View {
        if localSource {
            VStack(alignment: .leading, spacing: 8) {
                Text("Исключить файлы и папки").font(.headline)
                Text("Относительные пути внутри источника, по одному в строке. Путь исключает сам файл или всё поддерево папки; маски не поддерживаются.")
                    .font(.caption).foregroundStyle(.secondary)
                if let path = source.path { Text(path).font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled) }
                TextEditor(text: Binding(get: { pathsText }, set: { pathsText = $0; invalidatePreview() }))
                    .font(.body.monospaced()).frame(minHeight: 90, maxHeight: 130)
                    .overlay(RoundedRectangle(cornerRadius: 4).stroke(.quaternary))
                    .accessibilityLabel("Исключённые относительные пути")
                Text("До 100 путей, каждый до 2048 символов. Исходные файлы останутся на месте.").font(.caption).foregroundStyle(.secondary)
            }
        } else if source.kind == "apple_notes" {
            VStack(alignment: .leading, spacing: 8) {
                Text("Исключить выбранные папки Заметок").font(.headline)
                ForEach(source.notesFolderIds ?? [], id: \.self) { folderID in
                    Toggle(isOn: Binding(get: { notesExcluded.contains(folderID) }, set: { selected in
                        notesExcluded = selected ? notesExcluded.union([folderID]) : notesExcluded.subtracting([folderID])
                        invalidatePreview()
                    })) {
                        Text(folderID).font(.caption).lineLimit(2).textSelection(.enabled)
                    }.toggleStyle(.checkbox).accessibilityLabel("Исключить папку Заметок \(folderID)")
                }
                Text("Показаны идентификаторы папок, выбранных при подключении. Старые материалы без сохранённого идентификатора папки будут скрыты при наличии исключений до повторного импорта. Заметки не изменяются.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        } else {
            Text("Область импорта задаётся настройками подключения: выбранными страницами, репозиториями или периодом входящих писем.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private var previewControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Исключённые материалы исчезнут из активного поиска, MCP и производных индексов. Оригиналы и настройки подключения сохранятся.")
                .font(.caption).foregroundStyle(.secondary)
            if localSource || source.kind == "apple_notes" {
                HStack {
                    Button("Показать последствия исключений") { previewExclusions() }
                        .disabled(candidate == nil || preview.isLoading)
                    if preview.isLoading { ProgressView().controlSize(.small) }
                }
                if let result = preview.result {
                    Text("Изменения затронут материалы: \(result.affectedDocuments).")
                        .font(.callout.weight(.medium)).accessibilityAddTraits(.updatesFrequently)
                    Text("Учтены исключения и ограничения видимости в приложении или через MCP.")
                        .font(.caption).foregroundStyle(.secondary)
                    ForEach(result.samples.prefix(20)) { sample in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(sample.title.isEmpty ? "Без названия" : sample.title).font(.caption).lineLimit(2)
                            if let uri = sample.sourceUri { Text(uri).font(.caption2).foregroundStyle(.secondary).lineLimit(1) }
                        }
                    }
                } else {
                    Text("После изменения исключений сначала проверьте их последствия.").font(.caption).foregroundStyle(.secondary)
                }
                if candidate == nil {
                    Text("Проверьте пути: без абсолютных путей, .., масок и управляющих символов.").font(.caption).foregroundStyle(.orange)
                }
            }
        }
    }

    private func load() async {
        loading = true; error = nil
        do {
            let policy = try await store.sourcePolicy(source)
            guard !Task.isCancelled else { return }
            original = policy; draft = policy; pathsText = policy.excludedPaths.joined(separator: "\n")
            notesExcluded = Set(policy.excludedNotesFolderIds); preview.invalidate()
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
        if !Task.isCancelled { loading = false }
    }

    private func invalidatePreview() {
        previewTask?.cancel(); previewTask = nil; preview.invalidate(); error = nil
    }

    private func previewExclusions() {
        guard let candidate else { return }
        let request = preview.begin(candidate); error = nil
        previewTask = Task {
            do {
                let result = try await store.previewSourcePolicy(source, policy: candidate)
                guard !Task.isCancelled else { return }
                preview.accept(result, request: request, policy: candidate)
            } catch {
                if !Task.isCancelled, preview.fail(request) { self.error = error.localizedDescription }
            }
        }
    }

    private func save() async {
        guard canSave, let candidate else { return }
        saving = true; error = nil
        defer { saving = false }
        do { try await store.saveSourcePolicy(source, policy: candidate); dismiss() }
        catch { self.error = error.localizedDescription }
    }
}
