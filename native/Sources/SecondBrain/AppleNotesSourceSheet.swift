import SwiftUI
import SecondBrainCore

struct AppleNotesSourceSheet: View {
    @ObservedObject var store: AppStore
    @Environment(\.dismiss) private var dismiss
    @ViewState private var name = "Apple Заметки"
    @ViewState private var query = ""
    @ViewState private var picker = AppleNotesFolderSelection()
    @ViewState private var discovery: Task<Void, Never>?
    @ViewState private var saving = false
    @ViewState private var error: String?
    @ViewState private var warnings: [String] = []

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label("Подключить Apple Заметки", systemImage: "note.text").font(.title2.weight(.semibold))
            Text("В память попадёт текст заметок только из выбранных папок. Оригиналы не изменяются.")
                .font(.callout).foregroundStyle(.secondary)
            TextField("Название", text: $name).textFieldStyle(.roundedBorder)
            VStack(alignment: .leading, spacing: 8) {
                Text("Откройте Apple Заметки, затем загрузите папки. macOS может запросить разрешение Second Brain на управление Заметками — оно используется только для чтения.")
                Text("Защищённые заметки и вложения пропускаются. Для синхронизации оставляйте Заметки открытыми.")
            }.font(.caption).foregroundStyle(.secondary)
            HStack {
                Button("Загрузить папки") { loadFolders() }.disabled(picker.isLoading)
                if picker.isLoading { ProgressView().controlSize(.small) }
                Spacer()
                TextField("Поиск папки или аккаунта", text: $query).textFieldStyle(.roundedBorder).frame(maxWidth: 310)
            }
            folderList
            Text("Выбрано: \(picker.selectedIds.count) из 50. Вложенные папки выбираются отдельно.")
                .font(.caption).foregroundStyle(.secondary)
            if let warning = warnings.first { Text(warning).font(.caption).foregroundStyle(.orange) }
            if let error { Text(error).font(.callout).foregroundStyle(.orange).textSelection(.enabled) }
            HStack {
                if saving { ProgressView().controlSize(.small) }
                Spacer()
                Button("Отмена") { dismiss() }.keyboardShortcut(.cancelAction).disabled(saving)
                Button("Добавить источник") { Task { await save() } }
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(saving || picker.isLoading || picker.selectedIds.isEmpty || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(24).frame(width: 660, height: 680)
        .disabled(saving).interactiveDismissDisabled(saving)
        .onDisappear { discovery?.cancel(); picker.invalidate() }
    }

    private var folderList: some View {
        Group {
            if picker.folders.isEmpty {
                VStack(spacing: 10) {
                    Text(picker.didLoad ? "Папки не найдены" : "Папки ещё не загружены").font(.callout.weight(.medium))
                    Text("Доступ запрашивается только после нажатия «Загрузить папки». Если вы отклонили запрос, разрешите его в Настройках системы → Конфиденциальность и безопасность → Автоматизация.")
                        .font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center)
                }.padding(20).frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List(picker.filtered(query)) { folder in
                    Toggle(isOn: Binding(get: { picker.selectedIds.contains(folder.id) }, set: { _ in picker.toggle(folder.id) })) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(folder.name).lineLimit(2)
                            Text(folder.account + " · " + folder.path).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                            if let count = folder.noteCount { Text("\(count) заметок").font(.caption).foregroundStyle(.secondary) }
                        }
                    }
                    .toggleStyle(.checkbox)
                    .disabled(!picker.selectedIds.contains(folder.id) && picker.selectedIds.count >= 50)
                    .accessibilityLabel("Импортировать папку \(folder.account), \(folder.path)")
                    .padding(.vertical, 3)
                }.listStyle(.bordered)
            }
        }.frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func loadFolders() {
        guard !saving, let request = picker.begin() else { return }
        error = nil; warnings = []
        discovery = Task {
            do {
                let result = try await store.appleNotesFolders()
                guard !Task.isCancelled else { return }
                if picker.accept(result, request: request) { warnings = result.warnings ?? [] }
            } catch is CancellationError { _ = picker.fail(request) }
            catch { if picker.fail(request) { self.error = error.localizedDescription } }
        }
    }

    private func save() async {
        guard !saving, !picker.selectedIds.isEmpty else { return }
        saving = true; error = nil
        defer { saving = false }
        let request = AddSourceRequest(kind: "apple_notes", name: name.trimmingCharacters(in: .whitespacesAndNewlines), notesFolderIds: picker.selectedIds.sorted())
        if let message = await store.addSource(request) { error = message }
        else { dismiss() }
    }
}
