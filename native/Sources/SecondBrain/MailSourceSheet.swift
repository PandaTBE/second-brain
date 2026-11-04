import SwiftUI
import SecondBrainCore

struct MailSourceSheet: View {
    @ObservedObject var store: AppStore
    let provider: MailProvider
    @Environment(\.dismiss) private var dismiss
    @ViewState private var name = ""
    @ViewState private var email = ""
    @ViewState private var password = ""
    @ViewState private var importEnabled = true
    @ViewState private var period = MailImportPeriod.last30Days
    @ViewState private var saving = false
    @ViewState private var error: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 12) {
                Image(systemName: "envelope.fill").font(.title).foregroundStyle(.green)
                VStack(alignment: .leading, spacing: 4) {
                    Text("Подключение: \(provider.title)").font(.title2.weight(.semibold))
                    Text("Сохраните важные письма в общей памяти").font(.callout).foregroundStyle(.secondary)
                }
            }.padding(24)

            Form {
                Section("1. Подготовьте почту") {
                    if provider == .gmail {
                        Text("Включите двухэтапную аутентификацию в аккаунте Google, затем создайте пароль приложения для Second Brain.")
                        Text("Если пароли приложений недоступны, проверьте правила рабочего аккаунта или расширенную защиту Google.")
                            .font(.caption).foregroundStyle(.secondary)
                    } else {
                        Text("В настройках Яндекс Почты разрешите доступ почтовым программам по IMAP и вход по паролям приложений. Затем создайте пароль приложения типа «Почта».")
                        Text("По инструкции Яндекса активация нового пароля может занять 2–3 часа. Если вход сразу отклонён, повторите проверку позже.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    HStack(spacing: 16) {
                        if let url = provider.passwordURL { Link("Создать пароль приложения…", destination: url) }
                        if let url = provider.helpURL { Link("Как подключить почту", destination: url) }
                    }
                }
                Section("2. Добавьте аккаунт") {
                    TextField("Название", text: $name, prompt: Text("Личная почта"))
                    TextField("Адрес почты", text: $email, prompt: Text(provider.exampleEmail))
                        .textContentType(.emailAddress).autocorrectionDisabled()
                    SecureField("Пароль приложения", text: $password)
                    Text("Это отдельный пароль для Second Brain. Обычный пароль от аккаунта сюда вводить не нужно. Секрет сохранится в Связке ключей macOS; сервер и защищённое подключение настроятся автоматически.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section("3. Выберите письма") {
                    Toggle("Импортировать входящие в память", isOn: $importEnabled)
                    Picker("Период писем", selection: $period) {
                        ForEach(MailImportPeriod.allCases) { value in Text(value.title).tag(value) }
                    }.disabled(!importEnabled)
                    Text("Загружается текст писем из «Входящих» без вложений и внешних изображений. Письма не удаляются и не помечаются прочитанными.")
                        .font(.caption).foregroundStyle(.secondary)
                    Text("Письма больше 1 МБ пропускаются с предупреждением, в том числе с большими вложениями.")
                        .font(.caption).foregroundStyle(.secondary)
                    Text("Импорт можно запустить в источниках; дальнейшие обновления идут по расписанию памяти. Сохранение этого подключения само по себе не читает письма.")
                        .font(.caption).foregroundStyle(.secondary)
                    Text("Если создание карточек через API включено, импортированные письма могут передаваться выбранному провайдеру модели.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if let error { Text(error).font(.callout).foregroundStyle(.orange).textSelection(.enabled) }
            }.formStyle(.grouped).disabled(saving)

            HStack {
                if saving { ProgressView().controlSize(.small) }
                Spacer()
                Button("Отмена") { password = ""; dismiss() }
                    .keyboardShortcut(.cancelAction).disabled(saving)
                Button("Добавить источник") { Task { await save() } }
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(saving || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || !provider.canSave(email: email, password: password))
            }.padding(20)
        }
        .frame(width: 650, height: 750)
        .onAppear { name = provider.title }
        .onDisappear { password = "" }
    }

    private func save() async {
        saving = true; error = nil
        defer { saving = false }
        let request = AddSourceRequest(
            kind: "imap", name: name.trimmingCharacters(in: .whitespacesAndNewlines),
            username: email.trimmingCharacters(in: .whitespacesAndNewlines), password: password,
            mailProvider: provider.rawValue, mailImportEnabled: importEnabled,
            mailLookbackDays: period.rawValue
        )
        if let message = await store.addSource(request) { error = message }
        else { password = ""; dismiss() }
    }
}
