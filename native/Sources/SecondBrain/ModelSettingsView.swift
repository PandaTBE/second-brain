import SwiftUI
import SecondBrainCore

struct ModelSettingsView: View {
    @ObservedObject var store: AppStore
    @ViewState private var revealToken = false
    @ViewState private var confirmDelete = false
    @ViewState private var showEmbeddingSettings = false

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
                    Text(store.draft.isLocal ? "Выберите загруженную модель Ollama для обработки на этом Mac." : "Выберите недорогую модель. Провайдера можно изменить в любой момент.").font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                Label(store.isSaved ? "Сохранено" : "Есть изменения", systemImage: store.isSaved ? "checkmark.circle.fill" : "pencil.circle")
                    .font(.caption).foregroundStyle(store.isSaved ? Color.green : Color.secondary)
            }.padding(.horizontal, 24).padding(.top, 22).padding(.bottom, 8)

            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    settingsGroup("Провайдер", symbol: "network") {
                        Picker("Провайдер", selection: Binding(get: { store.draft.provider }, set: { store.changeProvider($0); revealToken = false })) {
                            ForEach(store.providers) { provider in Text(provider.label).tag(provider.id) }
                        }.pickerStyle(.menu)
                        if let provider = store.selectedProvider { Text(provider.description).font(.caption).foregroundStyle(.secondary) }
                        if store.draft.provider == "custom" {
                            TextField("Адрес API", text: Binding(get: { store.draft.baseUrl }, set: { store.changeBaseURL($0); revealToken = false }), prompt: Text("https://api.example.com/v1"))
                                .textFieldStyle(.roundedBorder)
                            Toggle("Разрешить внутреннюю сеть для этого API", isOn: Binding(get: { store.draft.privateNetworkAccessAllowed }, set: { store.changePrivateNetworkAccess($0) }))
                                .toggleStyle(.checkbox)
                            Text("Для доверенного корпоративного API, в том числе через VPN. Разрешение относится к этому адресу и сбрасывается при его изменении. HTTPS, порт 443 и проверка сертификата обязательны; localhost недоступен.").font(.caption).foregroundStyle(.secondary)
                            TextField("Точный ID модели", text: Binding(get: { store.draft.modelId }, set: { store.enterModelID($0) }), prompt: Text("ID из документации вашего API"))
                                .textFieldStyle(.roundedBorder)
                            Text("Совместимый с OpenAI HTTPS API. Укажите базовый адрес, без /chat/completions. Каталог необязателен: можно сохранить адрес, токен и ID из документации вашего API.").font(.caption).foregroundStyle(.secondary)
                        } else {
                            LabeledContent("Адрес API") { Text(store.draft.baseUrl).font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled) }
                        }
                    }

                    if store.draft.isLocal {
                        settingsGroup("Qwen на этом Mac", symbol: "desktopcomputer") {
                            Label("API-токен не требуется", systemImage: "desktopcomputer")
                            Text("Запустите Ollama и загрузите Qwen. В каталоге появятся только загруженные локальные модели; облачные модели Ollama не поддерживаются.").font(.caption).foregroundStyle(.secondary)
                            Link("Установить Ollama ↗", destination: URL(string: "https://ollama.com/download/mac")!).font(.caption)
                            Text("ollama pull qwen3:8b").font(.caption.monospaced()).textSelection(.enabled)
                            Text("Команда загрузит модель размером около 5,2 ГБ. После загрузки нажмите «Обновить» и выберите точный ID. Локальное создание карточек использует память и процессор Mac.").font(.caption).foregroundStyle(.secondary)
                        }
                    } else {
                        settingsGroup("API-токен", symbol: "key.horizontal") {
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
                        }
                    }

                    settingsGroup("Модель", symbol: "cpu") {
                        HStack {
                            HStack { Image(systemName: "magnifyingglass").foregroundStyle(.secondary); TextField("Название или ID модели", text: $store.query).textFieldStyle(.plain) }
                                .padding(7).background(.quaternary, in: RoundedRectangle(cornerRadius: 6))
                            if !store.draft.isLocal { Toggle("Сначала дешевле", isOn: $store.sortByPrice).toggleStyle(.checkbox) }
                            Button { Task { await store.loadCatalog() } } label: { Label("Обновить", systemImage: "arrow.clockwise") }
                                .disabled(store.catalogLoading)
                        }
                        if store.catalogLoading { HStack { ProgressView().controlSize(.small); Text("Загружаем каталог…").foregroundStyle(.secondary) } }
                        if let error = store.catalogError { Label(error, systemImage: "exclamationmark.circle").font(.callout).foregroundStyle(.orange) }
                        if let warning = store.catalogWarning { Label(warning, systemImage: "info.circle").font(.caption).foregroundStyle(.orange) }
                        if !store.models.isEmpty {
                            Table(store.filteredModels, selection: modelSelection) {
                                TableColumn("Модель") { model in
                                    VStack(alignment: .leading, spacing: 3) { Text(model.name).lineLimit(1); Text(model.id).font(.caption).foregroundStyle(.secondary).lineLimit(1) }.padding(.vertical, 3)
                                }.width(min: 180, ideal: 300)
                                TableColumn("Вход / 1 млн") { model in Text(ModelFormatting.price(model.inputPrice, currency: store.currency)).monospacedDigit() }.width(min: 90, ideal: 110, max: 150)
                                TableColumn("Выход / 1 млн") { model in Text(ModelFormatting.price(model.outputPrice, currency: store.currency)).monospacedDigit() }.width(min: 90, ideal: 110, max: 150)
                            }.frame(height: 250)
                        } else if !store.catalogLoading {
                            ContentUnavailableView("Каталог ещё не загружен", systemImage: "cpu", description: Text("Загрузите список у провайдера или укажите ID вручную.")).frame(height: 130)
                        }
                        HStack {
                            Text(store.draft.isLocal ? "\(store.filteredModels.count) локальных моделей · без API-тарифа" : store.draft.provider == "qwen" ? "\(store.filteredModels.count) моделей · валюта не указана" : "\(store.filteredModels.count) моделей · цены в \(store.currency)")
                            Spacer()
                            if !store.draft.isLocal { Text("Сортировка по входу, затем выходу").foregroundStyle(.secondary) }
                        }.font(.caption)
                        if store.draft.provider == "qwen" {
                            Text("Цены и валюта в каталоге Qwen не указаны. Проверьте тариф в кабинете.").font(.caption).foregroundStyle(.secondary)
                        }
                        if store.draft.provider != "custom" {
                            TextField("ID модели", text: Binding(get: { store.draft.modelId }, set: { store.enterModelID($0) }), prompt: Text("Точный ID модели из каталога или документации"))
                                .textFieldStyle(.roundedBorder)
                        }
                        Text(store.draft.isLocal ? "Для Ollama нужен точный ID загруженной локальной модели. Облачные модели и автоматическая загрузка не используются." : "Ручной ID подходит и для API, которые не отдают каталог. Неизвестная цена не означает бесплатную модель.").font(.caption).foregroundStyle(.secondary)
                    }

                    settingsGroup("Поиск по смыслу", symbol: "sparkle.magnifyingglass") {
                        Text("Для поиска используется отдельная локальная embedding-модель. Настройки модели карточек не меняются.").font(.caption).foregroundStyle(.secondary)
                        Button("Настроить поиск по смыслу…") { showEmbeddingSettings = true }
                    }

                    settingsGroup("Проверка подключения", symbol: "checkmark.shield") {
                        LabeledContent("Проверка API", value: store.connection == nil ? "Не проверено" : store.connection?.status == "ok" ? "API отвечает" : "Есть замечание")
                        Text("Проверка обращается к API, но не запускает генерацию. Доступ к генерации и её качество проверяются отдельно.").font(.caption).foregroundStyle(.secondary)
                        if let notice = store.notice { Label(notice, systemImage: store.noticeIsError ? "exclamationmark.circle" : "checkmark.circle").foregroundStyle(store.noticeIsError ? Color.orange : Color.green).textSelection(.enabled) }
                    }
                }.padding(.horizontal, 24).padding(.vertical, 16)
            }

            HStack {
                Label(store.draft.isLocal ? "Обработка на этом Mac · без API-токена" : "Секреты — в Связке ключей macOS", systemImage: store.draft.isLocal ? "desktopcomputer" : "lock.shield").font(.caption).foregroundStyle(.secondary)
                Spacer()
                if !store.action.isEmpty { ProgressView().controlSize(.small) }
                Button("Проверить подключение") { Task { await store.testConnection() } }.disabled(!store.action.isEmpty)
                Button("Сохранить настройки") { Task { await store.save(); if store.isSaved { revealToken = false } } }
                    .buttonStyle(.borderedProminent).disabled(!store.action.isEmpty).keyboardShortcut("s", modifiers: .command)
            }.padding(16).background(.bar)
        }
        .sheet(isPresented: $showEmbeddingSettings) { EmbeddingSettingsSheet(memory: store.memory) }
        .alert("Удалить API-токен?", isPresented: $confirmDelete) {
            Button("Отмена", role: .cancel) {}
            Button("Удалить ключ", role: .destructive) { Task { await store.deleteToken(); revealToken = false } }
        } message: { Text("Будет удалён только ключ выбранного API. Настройки модели сохранятся.") }
    }

    private func settingsGroup<Content: View>(_ title: String, symbol: String, @ViewBuilder content: () -> Content) -> some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 12, content: content)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(10)
        } label: { Label(title, systemImage: symbol).font(.headline) }
    }
}
