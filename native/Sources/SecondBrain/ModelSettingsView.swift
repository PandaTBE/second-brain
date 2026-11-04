import SwiftUI
import SecondBrainCore

struct ModelSettingsView: View {
    @ObservedObject var store: AppStore
    @ViewState private var revealToken = false
    @ViewState private var confirmDelete = false

    private var tokenBinding: Binding<String> { Binding(get: { store.tokenDraft }, set: { store.changeToken($0) }) }
    private var modelSelection: Binding<String?> {
        Binding(get: { store.draft.modelId.isEmpty ? nil : store.draft.modelId }, set: { id in
            if let model = store.models.first(where: { $0.id == id }) { store.selectModel(model) }
        })
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 5) {
                    Text("Модель для ваших карточек").font(.title2.weight(.semibold))
                    Text("Выберите недорогую модель. Провайдера можно изменить в любой момент.").font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                Label(store.isSaved ? "Сохранено" : "Есть изменения", systemImage: store.isSaved ? "checkmark.circle.fill" : "pencil.circle")
                    .font(.caption).foregroundStyle(store.isSaved ? Color.green : Color.secondary)
            }.padding(.horizontal, 24).padding(.top, 22).padding(.bottom, 8)

            Form {
                Section {
                    Picker("Провайдер", selection: Binding(get: { store.draft.provider }, set: { store.changeProvider($0); revealToken = false })) {
                        ForEach(store.providers) { provider in Text(provider.label).tag(provider.id) }
                    }.pickerStyle(.menu)
                    if let provider = store.selectedProvider { Text(provider.description).font(.caption).foregroundStyle(.secondary) }
                    if store.draft.provider == "custom" {
                        TextField("Адрес API", text: Binding(get: { store.draft.baseUrl }, set: { store.changeBaseURL($0); revealToken = false }), prompt: Text("https://api.example.com/v1"))
                            .textFieldStyle(.roundedBorder)
                        Text("Совместимый с OpenAI HTTPS API. Укажите базовый адрес, без /chat/completions.").font(.caption).foregroundStyle(.secondary)
                    } else {
                        LabeledContent("Адрес API") { Text(store.draft.baseUrl).font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled) }
                    }
                } header: { Label("Провайдер", systemImage: "network") }

                Section {
                    HStack {
                        Group {
                            if revealToken { TextField("Новый API-токен", text: tokenBinding) }
                            else { SecureField(store.tokenSaved ? "Токен сохранён — можно оставить пустым" : "Вставьте API-токен", text: tokenBinding) }
                        }.textFieldStyle(.roundedBorder)
                        Button { revealToken.toggle() } label: { Image(systemName: revealToken ? "eye.slash" : "eye") }
                            .help(revealToken ? "Скрыть введённый токен" : "Показать введённый токен")
                            .accessibilityLabel(revealToken ? "Скрыть введённый токен" : "Показать введённый токен")
                    }
                    HStack {
                        Label(store.tokenSaved ? "Ключ сохранён в Связке ключей" : "Ключ не добавлен", systemImage: store.tokenSaved ? "lock.fill" : "key")
                            .font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        if let key = store.selectedProvider?.keyUrl, let url = URL(string: key), url.scheme == "https" { Link("Получить API-токен ↗", destination: url).font(.caption) }
                        if store.tokenSaved { Button("Удалить ключ", role: .destructive) { confirmDelete = true }.font(.caption) }
                    }
                    if let warning = store.storageWarning { Label(warning, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.orange) }
                    Text("Сохранённый токен не считывается обратно в интерфейс. Новый токен остаётся в этом поле до успешного сохранения.").font(.caption).foregroundStyle(.secondary)
                } header: { Label("API-токен", systemImage: "key.horizontal") }

                Section {
                    HStack {
                        HStack { Image(systemName: "magnifyingglass").foregroundStyle(.secondary); TextField("Название или ID модели", text: $store.query).textFieldStyle(.plain) }
                            .padding(7).background(.quaternary, in: RoundedRectangle(cornerRadius: 6))
                        Toggle("Сначала дешевле", isOn: $store.sortByPrice).toggleStyle(.checkbox)
                        Button { Task { await store.loadCatalog() } } label: { Label("Обновить", systemImage: "arrow.clockwise") }
                            .disabled(store.catalogLoading)
                    }
                    if store.catalogLoading { HStack { ProgressView().controlSize(.small); Text("Загружаем каталог…").foregroundStyle(.secondary) } }
                    if let error = store.catalogError { Label(error, systemImage: "exclamationmark.circle").font(.callout).foregroundStyle(.orange) }
                    if !store.models.isEmpty {
                        Table(store.filteredModels, selection: modelSelection) {
                            TableColumn("Модель") { model in
                                VStack(alignment: .leading, spacing: 3) { Text(model.name).lineLimit(1); Text(model.id).font(.caption).foregroundStyle(.secondary).lineLimit(1) }.padding(.vertical, 3)
                            }.width(min: 180, ideal: 300)
                            TableColumn("Вход / 1 млн") { model in Text(ModelFormatting.price(model.inputPrice, currency: store.currency)).monospacedDigit() }.width(min: 90, ideal: 110, max: 150)
                            TableColumn("Выход / 1 млн") { model in Text(ModelFormatting.price(model.outputPrice, currency: store.currency)).monospacedDigit() }.width(min: 90, ideal: 110, max: 150)
                        }.frame(minHeight: 190, idealHeight: 230, maxHeight: 270)
                    } else if !store.catalogLoading {
                        ContentUnavailableView("Каталог ещё не загружен", systemImage: "cpu", description: Text("Загрузите список у провайдера или укажите ID вручную.")).frame(height: 130)
                    }
                    HStack {
                        Text("\(store.filteredModels.count) моделей · цены в \(store.currency)")
                        Spacer()
                        Text("Сортировка по входу, затем выходу").foregroundStyle(.secondary)
                    }.font(.caption)
                    TextField("ID модели", text: Binding(get: { store.draft.modelId }, set: { store.enterModelID($0) }), prompt: Text("Точный ID модели из каталога или документации"))
                        .textFieldStyle(.roundedBorder)
                    Text("Ручной ID подходит и для API, которые не отдают каталог. Неизвестная цена не означает бесплатную модель.").font(.caption).foregroundStyle(.secondary)
                } header: { Label("Модель", systemImage: "cpu") }

                Section {
                    LabeledContent("Проверка API", value: store.connection == nil ? "Не проверено" : store.connection?.status == "ok" ? "API отвечает" : "Есть замечание")
                    Text("Проверка обращается к API, но не запускает генерацию. Доступ к генерации и её качество проверяются отдельно.").font(.caption).foregroundStyle(.secondary)
                    if let notice = store.notice { Label(notice, systemImage: store.noticeIsError ? "exclamationmark.circle" : "checkmark.circle").foregroundStyle(store.noticeIsError ? Color.orange : Color.green).textSelection(.enabled) }
                }
            }.formStyle(.grouped)

            HStack {
                Label("Секреты — в Связке ключей macOS", systemImage: "lock.shield").font(.caption).foregroundStyle(.secondary)
                Spacer()
                if !store.action.isEmpty { ProgressView().controlSize(.small) }
                Button("Проверить подключение") { Task { await store.testConnection() } }.disabled(!store.action.isEmpty)
                Button("Сохранить настройки") { Task { await store.save(); if store.isSaved { revealToken = false } } }
                    .buttonStyle(.borderedProminent).disabled(!store.action.isEmpty).keyboardShortcut("s", modifiers: .command)
            }.padding(16).background(.bar)
        }
        .alert("Удалить API-токен?", isPresented: $confirmDelete) {
            Button("Отмена", role: .cancel) {}
            Button("Удалить ключ", role: .destructive) { Task { await store.deleteToken(); revealToken = false } }
        } message: { Text("Будет удалён только ключ выбранного API. Настройки модели сохранятся.") }
    }
}
