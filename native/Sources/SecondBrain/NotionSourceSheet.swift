import SwiftUI
import SecondBrainCore

struct NotionSourceSheet: View {
    @ObservedObject var store: AppStore
    @Environment(\.dismiss) private var dismiss
    @ViewState private var name = "Notion"
    @ViewState private var token = ""
    @ViewState private var query = ""
    @ViewState private var picker = NotionPageSelection()
    @ViewState private var discovery: Task<Void, Never>?
    @ViewState private var saving = false
    @ViewState private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label("Подключить Notion", systemImage: "doc.text")
                .font(.title2.weight(.semibold))
            Text("Импортируйте выбранные страницы в общую память. Подключение только читает данные Notion.")
                .font(.callout).foregroundStyle(.secondary)
            Form {
                TextField("Название", text: $name)
                SecureField("Токен интеграции", text: $token, prompt: Text("ntn_…"))
                HStack {
                    Link("Создать внутреннюю интеграцию…", destination: URL(string: "https://www.notion.so/profile/integrations")!)
                    Spacer()
                }
                Text("В Notion разрешите интеграции Read content, затем откройте нужные страницы → ••• → Connections и добавьте интеграцию. Токен сохраняется в Связке ключей macOS.")
                    .font(.caption).foregroundStyle(.secondary)
            }.formStyle(.grouped).fixedSize(horizontal: false, vertical: true)
            HStack {
                TextField("Поиск доступных страниц", text: $query)
                    .textFieldStyle(.roundedBorder).onSubmit { loadPages() }
                Button("Найти страницы") { loadPages() }
                    .disabled(token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || picker.isLoading)
            }
            pageList
            Text("Выбрано: \(picker.selectedIds.count) из 100. Импортируются только выбранные страницы; вложенные страницы нужно выбрать отдельно.")
                .font(.caption).foregroundStyle(.secondary)
            if let error { Text(error).font(.callout).foregroundStyle(.orange).textSelection(.enabled) }
            HStack {
                if saving { ProgressView().controlSize(.small) }
                Spacer()
                Button("Отмена") { token = ""; dismiss() }
                    .keyboardShortcut(.cancelAction).disabled(saving)
                Button("Добавить источник") { Task { await save() } }
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(saving || picker.isLoading || picker.selectedIds.isEmpty || token.isEmpty || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(24).frame(width: 660, height: 720)
        .disabled(saving)
        .onChange(of: token) { _, _ in invalidateDiscovery() }
        .onChange(of: query) { _, value in
            if value.count > 100 { query = String(value.prefix(100)) }
            invalidateDiscovery()
        }
        .onDisappear { discovery?.cancel(); picker.invalidate(); token = "" }
    }

    private var pageList: some View {
        VStack(alignment: .leading, spacing: 8) {
            if picker.pages.isEmpty {
                VStack(spacing: 10) {
                    if picker.isLoading { ProgressView().controlSize(.small) }
                    Text(picker.didLoad ? "Страницы не найдены" : "Введите токен и найдите страницы")
                        .font(.callout.weight(.medium))
                    Text("Если страница не найдена, проверьте доступ в Connections и повторите поиск.")
                        .font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center)
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List(picker.pages) { page in
                    Toggle(isOn: Binding(get: { picker.selectedIds.contains(page.id) }, set: { _ in picker.toggle(page.id) })) {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(page.title.isEmpty ? "Без названия" : page.title).lineLimit(2)
                            Text(page.url).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                        }
                    }
                    .toggleStyle(.checkbox)
                    .disabled(!picker.selectedIds.contains(page.id) && picker.selectedIds.count >= 100)
                    .accessibilityLabel("Импортировать страницу \(page.title)")
                    .padding(.vertical, 3)
                }.listStyle(.bordered)
            }
            if picker.nextCursor != nil {
                HStack {
                    Button("Загрузить ещё") { loadPages(continuation: true) }.disabled(picker.isLoading)
                    if picker.isLoading { ProgressView().controlSize(.small) }
                }
            }
        }.frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func invalidateDiscovery() {
        discovery?.cancel(); discovery = nil; picker.invalidate(); error = nil
    }

    private func loadPages(continuation: Bool = false) {
        guard !saving, !token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              let request = picker.begin(continuation: continuation) else { return }
        error = nil
        let candidate = token.trimmingCharacters(in: .whitespacesAndNewlines)
        let search = query.trimmingCharacters(in: .whitespacesAndNewlines)
        discovery = Task {
            do {
                let result = try await store.notionPages(token: candidate, query: search, cursor: request.cursor)
                guard !Task.isCancelled else { return }
                picker.accept(result, request: request)
            } catch is CancellationError {
                _ = picker.fail(request: request)
            } catch {
                if picker.fail(request: request) { self.error = error.localizedDescription }
            }
        }
    }

    private func save() async {
        guard !picker.selectedIds.isEmpty else { return }
        saving = true; error = nil
        defer { saving = false }
        let request = AddSourceRequest(kind: "notion", name: name.trimmingCharacters(in: .whitespacesAndNewlines), token: token.trimmingCharacters(in: .whitespacesAndNewlines), notionPageIds: picker.selectedIds.sorted())
        if let message = await store.addSource(request) { error = message }
        else { token = ""; dismiss() }
    }
}
