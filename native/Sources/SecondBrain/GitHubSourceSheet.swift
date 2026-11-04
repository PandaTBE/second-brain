import SwiftUI
import SecondBrainCore

struct GitHubSourceSheet: View {
    @ObservedObject var store: AppStore
    @Environment(\.dismiss) private var dismiss
    @ViewState private var name = "GitHub"
    @ViewState private var token = ""
    @ViewState private var query = ""
    @ViewState private var picker = GitHubRepositorySelection()
    @ViewState private var content: Set<GitHubImportContent> = Set(GitHubImportContent.allCases)
    @ViewState private var discovery: Task<Void, Never>?
    @ViewState private var saving = false
    @ViewState private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Подключить GitHub", systemImage: "chevron.left.forwardslash.chevron.right")
                .font(.title2.weight(.semibold))
            Text("Выберите репозитории и материалы для общей памяти. Подключение только читает данные GitHub.")
                .font(.callout).foregroundStyle(.secondary)
            Form {
                TextField("Название", text: $name)
                SecureField("Personal access token", text: $token, prompt: Text("github_pat_…"))
                Link("Создать fine-grained токен…", destination: URL(string: "https://github.com/settings/personal-access-tokens/new")!)
                Text(permissionsDescription).font(.caption).foregroundStyle(.secondary)
                LabeledContent("Материалы") {
                    HStack(spacing: 14) {
                        ForEach(GitHubImportContent.allCases, id: \.rawValue) { type in
                            Toggle(type.title, isOn: Binding(
                                get: { content.contains(type) },
                                set: { include in
                                    content = include ? content.union([type]) : content.subtracting([type])
                                }
                            )).toggleStyle(.checkbox)
                        }
                    }
                }
            }.formStyle(.grouped).fixedSize(horizontal: false, vertical: true)
            HStack {
                TextField("Фильтр загруженных репозиториев", text: $query)
                    .textFieldStyle(.roundedBorder)
                Button(picker.didLoad ? "Обновить список" : "Загрузить репозитории") { loadRepositories() }
                    .disabled(token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || picker.isLoading)
            }
            repositoryList
            Text("Выбрано: \(picker.selectedIds.count) из 20. Фильтр не сбрасывает выбор. Новые репозитории автоматически не подключаются.")
                .font(.caption).foregroundStyle(.secondary)
            Text("Документация — Markdown из основной ветки; Issues и PR — заголовки и описания. Комментарии, исходный код и diff не загружаются.")
                .font(.caption).foregroundStyle(.secondary)
            if let error { Text(error).font(.callout).foregroundStyle(.orange).textSelection(.enabled) }
            HStack {
                if saving { ProgressView().controlSize(.small) }
                Spacer()
                Button("Отмена") { token = ""; dismiss() }
                    .keyboardShortcut(.cancelAction).disabled(saving)
                Button("Добавить источник") { Task { await save() } }
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(saving || picker.isLoading || picker.selectedIds.isEmpty || content.isEmpty || token.isEmpty || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(24).frame(width: 740, height: 780)
        .disabled(saving)
        .onChange(of: token) { _, _ in invalidateDiscovery() }
        .onDisappear { discovery?.cancel(); picker.invalidate(); token = "" }
    }

    private var permissionsDescription: String {
        let requested = ["Metadata"] + GitHubImportContent.allCases.compactMap { type -> String? in
            guard content.contains(type) else { return nil }
            switch type {
            case .docs: return "Contents"
            case .issues: return "Issues"
            case .pullRequests: return "Pull requests"
            }
        }
        return "Разрешите токену доступ только к нужным репозиториям и права Read-only: \(requested.joined(separator: ", ")). Для организаций может понадобиться одобрение. Токен сохраняется в Связке ключей macOS."
    }

    private var repositoryList: some View {
        let visible = picker.filteredRepositories(query: query)
        return VStack(alignment: .leading, spacing: 8) {
            if visible.isEmpty {
                VStack(spacing: 10) {
                    if picker.isLoading { ProgressView().controlSize(.small) }
                    Text(!query.isEmpty ? "Нет совпадений среди загруженных репозиториев" : picker.didLoad ? "Доступные репозитории не найдены" : "Введите токен и загрузите репозитории")
                        .font(.callout.weight(.medium)).multilineTextAlignment(.center)
                    Text("Проверьте доступ токена к репозиториям. Если список ещё не полный, загрузите следующую страницу.")
                        .font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center)
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List(visible) { repository in
                    Toggle(isOn: Binding(
                        get: { picker.selectedIds.contains(repository.id) },
                        set: { _ in picker.toggle(repository.id) }
                    )) {
                        VStack(alignment: .leading, spacing: 3) {
                            HStack(spacing: 6) {
                                Text(repository.fullName).fontWeight(.medium).lineLimit(1)
                                if repository.private { Image(systemName: "lock.fill").foregroundStyle(.secondary).help("Приватный репозиторий") }
                            }
                            if let description = repository.description, !description.isEmpty {
                                Text(description).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                            }
                        }
                    }
                    .toggleStyle(.checkbox)
                    .disabled(!picker.selectedIds.contains(repository.id) && picker.selectedIds.count >= 20)
                    .accessibilityLabel("Импортировать репозиторий \(repository.fullName)")
                    .padding(.vertical, 3)
                }.listStyle(.bordered)
            }
            if picker.nextPage != nil {
                HStack {
                    Button("Загрузить ещё") { loadRepositories(continuation: true) }.disabled(picker.isLoading)
                    if picker.isLoading { ProgressView().controlSize(.small) }
                }
            }
        }.frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func invalidateDiscovery() {
        discovery?.cancel(); discovery = nil; picker.invalidate(); query = ""; error = nil
    }

    private func loadRepositories(continuation: Bool = false) {
        guard !saving, !token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              let request = picker.begin(continuation: continuation) else { return }
        error = nil
        let candidate = token.trimmingCharacters(in: .whitespacesAndNewlines)
        discovery = Task {
            do {
                let result = try await store.githubRepositories(token: candidate, page: request.page)
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
        guard !picker.selectedNames.isEmpty, !content.isEmpty else { return }
        saving = true; error = nil
        defer { saving = false }
        let request = AddSourceRequest(
            kind: "github", name: name.trimmingCharacters(in: .whitespacesAndNewlines),
            token: token.trimmingCharacters(in: .whitespacesAndNewlines),
            githubRepositories: picker.selectedNames,
            githubContent: content.map(\.rawValue).sorted()
        )
        if let message = await store.addSource(request) { error = message }
        else { token = ""; dismiss() }
    }
}
