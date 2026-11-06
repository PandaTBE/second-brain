import SwiftUI
import AppKit
import OSLog
import SecondBrainCore

/// Restored identities remain stable; only explicit choices repair their local access.
struct SourceRecoverySheet: View {
    @ObservedObject var store: AppStore
    let source: MemorySource
    @Environment(\.dismiss) private var dismiss
    @ViewState private var path = ""
    @ViewState private var token = ""
    @ViewState private var appPassword = ""
    @ViewState private var clientID = ""
    @ViewState private var folders = AppleNotesFolderSelection()
    @ViewState private var signIn = OutlookSignInState()
    @ViewState private var task: Task<Void, Never>?
    @ViewState private var saving = false
    @ViewState private var error: String?
    private static let logger = Logger(subsystem: "local.secondbrain.desktop", category: "source-recovery")
    private var canSave: Bool {
        if source.requiresRelocation { return source.kind == "apple_notes" ? !folders.selectedIds.isEmpty : !path.isEmpty }
        if source.kind == "outlook" { return signIn.isAuthorized }
        return source.kind == "imap" ? !appPassword.isEmpty : !token.isEmpty
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Label("Восстановить подключение", systemImage: "arrow.uturn.backward.circle").font(.title2.weight(.semibold))
            Text(source.name).font(.headline)
            Text("Сохранённые материалы и ID источника останутся на месте. Старые пути и совпавшие секреты Связки ключей не активируют подключение автоматически.").font(.callout).foregroundStyle(.secondary)
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if source.requiresRelocation {
                        if source.kind == "apple_notes" { notesPicker }
                        else { pathPicker }
                    } else if source.kind == "outlook" { outlookLogin }
                    else {
                        Text(source.location).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                        if source.kind == "imap" { SecureField("Новый пароль приложения", text: $appPassword) }
                        else { SecureField("Новый API-токен", text: $token) }
                        Text("Секрет сохранится в Связке ключей только после нажатия кнопки. В резервную копию он не попадёт.").font(.caption).foregroundStyle(.secondary)
                    }
                }.padding(.vertical, 4)
            }
            Text("После проверки подключения разрешение импорта задаётся отдельно в настройках источника.").font(.caption).foregroundStyle(.secondary)
            if let error { Text(error).foregroundStyle(.orange).textSelection(.enabled) }
            HStack {
                if saving { ProgressView().controlSize(.small) }
                Spacer()
                Button("Отмена") { dismiss() }.keyboardShortcut(.cancelAction).disabled(saving)
                Button(source.requiresRelocation ? "Подтвердить выбранное расположение" : "Сохранить новый вход") { Task { await save() } }
                    .buttonStyle(.borderedProminent).disabled(!canSave || saving || folders.isLoading)
            }
        }
        .padding(24).frame(width: 650, height: 600)
        .disabled(saving).interactiveDismissDisabled(saving)
        .onAppear { clientID = source.outlookClientId ?? "" }
        .onDisappear { token = ""; appPassword = ""; folders.invalidate(); cancelLogin() }
    }

    private var pathPicker: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Выберите папку на этом Mac. Старый путь остаётся историческим происхождением материалов.")
            Text(source.path ?? "Старый путь неизвестен").font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
            HStack {
                Text(path.isEmpty ? "Новая папка не выбрана" : path).font(.caption).textSelection(.enabled)
                Spacer()
                Button("Выбрать папку…") {
                    let panel = NSOpenPanel(); panel.canChooseFiles = false; panel.canChooseDirectories = true; panel.allowsMultipleSelection = false
                    if panel.runModal() == .OK { path = panel.url?.path ?? "" }
                }
            }
        }
    }

    private var notesPicker: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Откройте Apple Заметки и выберите существующие папки заново. Старые ID папок не выбираются автоматически.").font(.callout)
            Button("Загрузить папки Заметок") { loadFolders() }.disabled(folders.isLoading)
            if folders.isLoading { ProgressView().controlSize(.small) }
            ForEach(folders.folders) { folder in
                Toggle(isOn: Binding(get: { folders.selectedIds.contains(folder.id) }, set: { _ in folders.toggle(folder.id) })) {
                    VStack(alignment: .leading) {
                        Text(folder.name)
                        Text("\(folder.account) · \(folder.path)").font(.caption).foregroundStyle(.secondary)
                    }
                }.toggleStyle(.checkbox).disabled(!folders.selectedIds.contains(folder.id) && folders.selectedIds.count >= 50)
            }
        }
    }

    private var outlookLogin: some View {
        VStack(alignment: .leading, spacing: 12) {
            TextField("Microsoft Application Client ID", text: $clientID).textFieldStyle(.roundedBorder)
                .onChange(of: clientID) { _, _ in cancelLogin() }
            Text("Вход выполняется на странице Microsoft. Пароль почты в приложение вводить не нужно.").font(.caption).foregroundStyle(.secondary)
            if signIn.isAuthorized, let account = signIn.account {
                Label("Свежий вход подтверждён", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                Text(account.username).textSelection(.enabled)
            } else if let code = signIn.userCode, let url = signIn.verificationURL {
                Text("Код Microsoft: \(code)").font(.title3.monospaced()).textSelection(.enabled)
                Link("Открыть Microsoft ↗", destination: url)
                HStack { ProgressView().controlSize(.small); Text("Ожидаем вход…") }
                Button("Отменить вход") { cancelLogin() }
            } else {
                Button("Войти через Microsoft заново") { beginLogin() }
                    .disabled(!OutlookSignInState.validClientID(clientID) || signIn.isStarting)
            }
        }
    }

    private func loadFolders() {
        guard let request = folders.begin() else { return }
        error = nil; task?.cancel()
        task = Task {
            do {
                let response = try await store.appleNotesFolders()
                guard !Task.isCancelled else { return }
                _ = folders.accept(response, request: request)
            } catch { if folders.fail(request) { self.error = error.localizedDescription } }
        }
    }

    private func beginLogin() {
        cancelLogin(); error = nil
        guard let request = signIn.begin(clientID: clientID) else { return }
        let candidate = clientID
        task = Task {
            do {
                let response = try await store.startOutlook(clientID: candidate)
                guard !Task.isCancelled, signIn.accept(response, request: request) else {
                    await cancelFlow(response.flowId); return
                }
                while signIn.isWaiting {
                    guard let delay = signIn.pollDelay() else { throw ServiceError("Срок кода истёк. Начните вход заново.") }
                    try await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
                    try Task.checkCancellation()
                    guard signIn.isCurrent(request), let flow = signIn.flowId else { return }
                    let response = try await store.pollOutlook(flowID: flow)
                    guard !Task.isCancelled, signIn.isCurrent(request) else { await cancelFlow(flow); return }
                    guard signIn.accept(response, request: request) else { throw ServiceError("Microsoft не подтвердил новый вход.") }
                }
            } catch is CancellationError { }
            catch { if signIn.isCurrent(request) { let message = error.localizedDescription; cancelLogin(); self.error = message } }
        }
    }
    private func cancelLogin() {
        task?.cancel(); task = nil
        if let flow = signIn.invalidate() { Task { await cancelFlow(flow) } }
    }
    private func cancelFlow(_ flow: String) async {
        do { try await store.cancelOutlook(flowID: flow) }
        catch { Self.logger.warning("Temporary recovery sign-in could not be cancelled; it will expire.") }
    }
    private func save() async {
        guard canSave, !saving else { return }
        saving = true; error = nil
        let request = SourceReauthenticationRequest(token: token.isEmpty ? nil : token,
            appPassword: appPassword.isEmpty ? nil : appPassword, outlookAuthId: signIn.authId)
        token = ""; appPassword = ""
        defer { saving = false }
        do {
            if source.requiresRelocation {
                try await store.relocateSource(source, request: SourceRelocationRequest(
                    path: path.isEmpty ? nil : path, notesFolderIds: folders.selectedIds.sorted()))
            } else { try await store.reauthenticateSource(source, request: request) }
            dismiss()
        } catch { self.error = error.localizedDescription }
    }
}
