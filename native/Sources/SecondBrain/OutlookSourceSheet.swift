import SwiftUI
import AppKit
import OSLog
import SecondBrainCore

struct OutlookSourceSheet: View {
    @ObservedObject var store: AppStore
    @Environment(\.dismiss) private var dismiss
    @ViewState private var name = "Outlook"
    @ViewState private var clientID = ""
    @ViewState private var importEnabled = true
    @ViewState private var period: MailImportPeriod = .last30Days
    @ViewState private var signIn = OutlookSignInState()
    @ViewState private var operation: Task<Void, Never>?
    @ViewState private var saving = false
    @ViewState private var error: String?
    private static let logger = Logger(subsystem: "local.secondbrain.desktop", category: "outlook-login")

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label("Подключить Outlook", systemImage: "envelope.badge").font(.title2.weight(.semibold))
            Text("Outlook.com и Microsoft 365. Вход проходит на странице Microsoft; пароль почты в приложение вводить не нужно.")
                .font(.callout).foregroundStyle(.secondary)
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    TextField("Название", text: $name).textFieldStyle(.roundedBorder)
                    VStack(alignment: .leading, spacing: 7) {
                        TextField("Client ID приложения", text: $clientID, prompt: Text("Application (client) ID из Microsoft Entra"))
                            .textFieldStyle(.roundedBorder)
                        Text("Это публичный идентификатор регистрации приложения, а не пароль или Client secret.")
                            .font(.caption).foregroundStyle(.secondary)
                        registrationHelp
                    }
                    VStack(alignment: .leading, spacing: 10) {
                        Toggle("Импортировать входящие письма в память", isOn: $importEnabled).toggleStyle(.checkbox)
                        Picker("Период первого импорта", selection: $period) {
                            ForEach(MailImportPeriod.allCases) { value in Text(value.title).tag(value) }
                        }.disabled(!importEnabled)
                        Text("Только «Входящие»: текст без вложений. Письма не отправляются, не удаляются и не помечаются прочитанными.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    signInPanel
                }.padding(.vertical, 4)
            }
            if let error { Text(error).font(.callout).foregroundStyle(.orange).textSelection(.enabled) }
            HStack {
                if saving { ProgressView().controlSize(.small) }
                Spacer()
                Button("Отмена") { cancelSignIn(); dismiss() }.keyboardShortcut(.cancelAction).disabled(saving)
                Button("Добавить источник") { Task { await save() } }
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(saving || !signIn.isAuthorized || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(24).frame(width: 660, height: 740)
        .disabled(saving).interactiveDismissDisabled(saving)
        .onChange(of: clientID) { _, _ in cancelSignIn(); error = nil }
        .onDisappear { cancelSignIn() }
    }

    private var registrationHelp: some View {
        DisclosureGroup("Как получить Client ID") {
            VStack(alignment: .leading, spacing: 8) {
                Text("1. В Microsoft Entra откройте App registrations → New registration. Для личных и рабочих ящиков выберите аккаунты организаций и личные Microsoft accounts.")
                Text("2. В Authentication разрешите public client flows. Device code не требует Redirect URI или Client secret.")
                Text("3. Добавьте Microsoft Graph → Delegated permissions: Mail.Read и User.Read. Приложение также запросит offline_access для обновления входа.")
                Text("4. Скопируйте Application (client) ID из Overview. Если политика компании запрещает регистрацию или согласие, обратитесь в IT-отдел.")
                Link("Инструкция Microsoft по регистрации приложения ↗", destination: URL(string: "https://learn.microsoft.com/en-us/entra/identity-platform/scenario-desktop-app-registration")!)
            }.font(.caption).foregroundStyle(.secondary).padding(.top, 7)
        }
    }

    private var signInPanel: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let account = signIn.account, signIn.isAuthorized {
                Label("Аккаунт подтверждён Microsoft", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                Text(account.displayName.isEmpty ? account.username : account.displayName)
                Text(account.username).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                Text("Нажмите «Добавить источник», чтобы сохранить подключение в Связке ключей macOS.")
                    .font(.caption).foregroundStyle(.secondary)
                Button("Войти в другой аккаунт") { cancelSignIn() }
            } else if let code = signIn.userCode, let url = signIn.verificationURL {
                Text("Откройте страницу Microsoft и введите код:").font(.callout)
                HStack {
                    Text(code).font(.title.monospaced().weight(.semibold)).textSelection(.enabled)
                    Button("Скопировать код") { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(code, forType: .string) }
                }
                Link("Открыть страницу Microsoft ↗", destination: url)
                HStack { ProgressView().controlSize(.small); Text("Ожидаем завершения входа…").font(.caption).foregroundStyle(.secondary) }
                Button("Отменить вход") { cancelSignIn() }
            } else {
                Button("Войти через Microsoft") { beginSignIn() }
                    .buttonStyle(.borderedProminent)
                    .disabled(!OutlookSignInState.validClientID(clientID) || signIn.isStarting)
                if signIn.isStarting { ProgressView().controlSize(.small) }
            }
        }.frame(maxWidth: .infinity, alignment: .leading).padding(16).background(.quaternary, in: RoundedRectangle(cornerRadius: 10))
    }

    private func beginSignIn() {
        cancelSignIn(); error = nil
        guard let request = signIn.begin(clientID: clientID) else { return }
        let candidate = clientID.trimmingCharacters(in: .whitespacesAndNewlines)
        operation = Task {
            do {
                let response = try await store.startOutlook(clientID: candidate)
                guard !Task.isCancelled, signIn.accept(response, request: request) else {
                    await cancelFlow(response.flowId)
                    if signIn.isCurrent(request) { throw ServiceError("Microsoft вернул некорректные данные входа. Повторите подключение.") }
                    return
                }
                while signIn.isWaiting {
                    guard let delay = signIn.pollDelay() else { throw ServiceError("Срок действия кода истёк. Начните вход заново.") }
                    try await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
                    try Task.checkCancellation()
                    guard signIn.isCurrent(request), let flowID = signIn.flowId else { return }
                    guard signIn.pollDelay() != nil else { throw ServiceError("Срок действия кода истёк. Начните вход заново.") }
                    let result = try await store.pollOutlook(flowID: flowID)
                    guard !Task.isCancelled, signIn.isCurrent(request) else { await cancelFlow(flowID); return }
                    guard signIn.accept(result, request: request) else { throw ServiceError("Не удалось завершить вход. Начните подключение заново.") }
                }
            } catch is CancellationError {}
            catch {
                guard signIn.isCurrent(request) else { return }
                let message = error.localizedDescription
                cancelSignIn(); self.error = message
            }
        }
    }

    private func cancelSignIn() {
        operation?.cancel(); operation = nil
        if let flowID = signIn.invalidate() { Task { await cancelFlow(flowID) } }
    }
    private func cancelFlow(_ flowID: String) async {
        do { try await store.cancelOutlook(flowID: flowID) }
        catch { Self.logger.warning("Could not cancel a temporary Outlook login; it will expire.") }
    }
    private func save() async {
        guard !saving, let authID = signIn.authId, signIn.isAuthorized else { return }
        saving = true; error = nil
        defer { saving = false }
        let request = AddSourceRequest(kind: "outlook", name: name.trimmingCharacters(in: .whitespacesAndNewlines), mailImportEnabled: importEnabled, mailLookbackDays: period.rawValue, outlookAuthId: authID)
        if let message = await store.addSource(request) { error = message }
        else { dismiss() }
    }
}
