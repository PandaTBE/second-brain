import SwiftUI
import AppKit
import SecondBrainCore

extension Binding where Value == BackupPassphraseValue {
    var backupPassphraseText: Binding<String> {
        Binding<String>(get: { wrappedValue.text }, set: {
            let updated = BackupPassphraseValue($0)
            if updated != wrappedValue { wrappedValue = updated }
        })
    }
}

struct BackupSettingsView: View {
    @ObservedObject var store: AppStore
    @ObservedObject private var backups: BackupViewModel
    @ViewState private var exportPath = ""
    @ViewState private var exportPassword = BackupPassphraseValue()
    @ViewState private var confirmation = BackupPassphraseValue()
    @ViewState private var inspectPath = ""
    @ViewState private var inspectPassword = BackupPassphraseValue()
    @ViewState private var deleteCandidate: ManagedBackup?
    @ViewState private var confirmingRestore = false
    init(store: AppStore) { self.store = store; self.backups = store.backups }
    private var canExport: Bool {
        BackupPassphrase.exportRequest(path: exportPath, passphrase: exportPassword, confirmation: confirmation) != nil
    }
    var body: some View {
        Form {
            Section {
                Text("Согласованный снимок памяти включает разрешения и настройки. Секреты Связки ключей не входят в копию.")
                Text("Копии могут хранить материалы, позже удалённые из рабочей памяти. Удаляйте старые копии явно, когда они больше не нужны.")
                    .foregroundStyle(.secondary)
                if let limit = backups.status?.limits.maxArchiveBytes {
                    Text("Предел архива: \(ByteCountFormatter.string(fromByteCount: Int64(limit), countStyle: .binary)). Превышение размера останавливает операцию.").font(.caption).foregroundStyle(.secondary)
                }
            } header: { Text("Резервные копии и перенос") }
            localSettings
            inventory
            encryptedExport
            restoreSection
            if let error = backups.error { Text(error).foregroundStyle(.orange).textSelection(.enabled) }
            if let notice = backups.notice { Text(notice).foregroundStyle(.green).textSelection(.enabled) }
            if backups.busy { HStack { ProgressView().controlSize(.small); Text(backups.action) }.accessibilityAddTraits(.updatesFrequently) }
        }
        .formStyle(.grouped)
        .disabled(backups.busy)
        .toolbar { Button("Обновить") { Task { await backups.refresh() } }.disabled(backups.busy || backups.loading) }
        .task { await backups.refresh() }
        .onDisappear { exportPassword = BackupPassphraseValue(); confirmation = BackupPassphraseValue(); inspectPassword = BackupPassphraseValue(); backups.invalidateInspection() }
        .alert("Удалить выбранную локальную копию?", isPresented: Binding(get: { deleteCandidate != nil }, set: { if !$0 { deleteCandidate = nil } })) {
            Button("Отмена", role: .cancel) { deleteCandidate = nil }
            Button("Удалить копию", role: .destructive) {
                if let copy = deleteCandidate { Task { await backups.delete(copy) } }; deleteCandidate = nil
            }
        } message: { Text("Рабочая память и исходные материалы останутся на месте. Эта резервная копия будет удалена.") }
        .alert("Восстановить проверенный архив?", isPresented: $confirmingRestore) {
            Button("Отмена", role: .cancel) { }
            Button("Восстановить память", role: .destructive) { Task { await backups.restore() } }
        } message: { Text("Рабочая память и настройки будут заменены проверенным снимком. Перед заменой сервис сохранит текущее состояние для отката; синхронизация и модельные задания останутся приостановлены.") }
    }

    private var localSettings: some View {
        Section {
            Label("Локальные копии не зашифрованы и могут содержать личные материалы.", systemImage: "lock.open").foregroundStyle(.orange)
            HStack {
                VStack(alignment: .leading) {
                    Text("Папка копий")
                    Text(backups.draft.directory ?? "Папка резервных копий внутри данных приложения").font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                }
                Spacer()
                Button("Выбрать папку…") {
                    let panel = NSOpenPanel(); panel.canChooseFiles = false; panel.canChooseDirectories = true
                    panel.canCreateDirectories = true; panel.allowsMultipleSelection = false
                    if panel.runModal() == .OK { backups.draft.directory = panel.url?.path }
                }
            }
            Toggle("Автоматически создавать локальные копии", isOn: $backups.draft.enabled)
            Stepper("Интервал: \(backups.draft.intervalHours) ч", value: $backups.draft.intervalHours, in: 1...168)
            Stepper("Хранить не больше \(backups.draft.maxCopies) копий", value: $backups.draft.maxCopies, in: 1...50)
            Stepper("Общий размер: \(backups.draft.maxTotalBytes / 1_048_576) МиБ", value: Binding(get: { backups.draft.maxTotalBytes / 1_048_576 }, set: { backups.draft.maxTotalBytes = $0 * 1_048_576 }), in: 1...10240)
            HStack {
                Button("Сохранить настройки копий") { Task { await backups.saveSettings() } }
                Spacer()
                Button("Создать локальную копию сейчас") { Task { await backups.createLocal() } }.buttonStyle(.borderedProminent)
            }
            Text("Расписание работает, пока запущено приложение. Ограничения удаляют только управляемые локальные копии; экспортированные вами архивы удаляются отдельно.").font(.caption).foregroundStyle(.secondary)
        } header: { Text("Локальные копии") }
    }

    private var inventory: some View {
        Section {
            if backups.loading && backups.status == nil { ProgressView() }
            else if backups.status?.backups.isEmpty != false { Text("Управляемых локальных копий пока нет.").foregroundStyle(.secondary) }
            ForEach(backups.status?.backups ?? []) { backup in
                HStack {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(backup.name).textSelection(.enabled)
                        Text("\(backup.createdAt) · \(ByteCountFormatter.string(fromByteCount: Int64(backup.sizeBytes), countStyle: .binary))").font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Удалить…", role: .destructive) { deleteCandidate = backup }.accessibilityLabel("Удалить копию \(backup.name)")
                }
            }
            if let issue = backups.status?.lastError { Text(issue).foregroundStyle(.orange).textSelection(.enabled) }
        } header: { Text("Сохранённые локальные копии") }
    }

    private var encryptedExport: some View {
        Section {
            HStack {
                Text(exportPath.isEmpty ? "Архив ещё не выбран" : exportPath).font(.caption).textSelection(.enabled)
                Spacer()
                Button("Выбрать файл…") {
                    let panel = NSSavePanel(); panel.nameFieldStringValue = "SecondBrain.zip.age"
                    panel.canCreateDirectories = true
                    if panel.runModal() == .OK { exportPath = panel.url?.path ?? "" }
                }
            }
            SecureField("Пароль архива: от 8 символов", text: $exportPassword.backupPassphraseText).textContentType(.newPassword)
            SecureField("Повторите пароль", text: $confirmation.backupPassphraseText).textContentType(.newPassword)
            Text("Для переноса используется шифрованный архив age. Пароль не сохраняется в настройках или Связке ключей; сохраните его самостоятельно. Существующий файл не перезаписывается.").font(.caption).foregroundStyle(.secondary)
            Button("Создать зашифрованный архив") {
                guard let request = BackupPassphrase.exportRequest(path: exportPath, passphrase: exportPassword, confirmation: confirmation) else { return }
                exportPassword = BackupPassphraseValue(); confirmation = BackupPassphraseValue()
                Task { await backups.export(path: request.path, passphrase: request.passphrase) }
            }.disabled(!canExport)
        } header: { Text("Зашифрованный архив для переноса") }
    }

    private var restoreSection: some View {
        Section {
            HStack {
                Text(inspectPath.isEmpty ? "Выберите локальную копию или архив age" : inspectPath).font(.caption).textSelection(.enabled)
                Spacer()
                Button("Выбрать архив…") {
                    let panel = NSOpenPanel(); panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
                    if panel.runModal() == .OK { backups.invalidateInspection(); inspectPath = panel.url?.path ?? ""; inspectPassword = BackupPassphraseValue() }
                }
            }
            SecureField("Пароль для age; для локальной копии оставьте пустым", text: Binding(get: { inspectPassword.text }, set: {
                if backups.changeInspectionPassword(from: inspectPassword.text, to: $0) { inspectPassword = BackupPassphraseValue($0) }
            }))
            Button("Проверить и показать содержимое") {
                let password = inspectPassword.text; inspectPassword = BackupPassphraseValue()
                Task { await backups.inspect(path: inspectPath, passphrase: password.isEmpty ? nil : password) }
            }.disabled(inspectPath.isEmpty)
            if let preview = backups.inspection.inspection {
                LabeledContent("Создан", value: preview.createdAt)
                LabeledContent("Версия приложения", value: preview.appVersion)
                Text("Материалов: \(preview.documents) · источников: \(preview.sources)")
                if preview.foreignInstallation { Text("Архив из другой установки. Старые пути и подключения не будут активированы автоматически.").foregroundStyle(.orange) }
                ForEach(Array(preview.warnings.enumerated()), id: \.offset) { _, warning in Text(warning).font(.caption).foregroundStyle(.secondary) }
                Text("Потребуют нового выбора пути/папок: \(preview.requiresRelocation.count). Потребуют входа: \(preview.requiresReauthentication.count).").font(.caption).foregroundStyle(.secondary)
                TimelineView(.periodic(from: Date(), by: 1)) { context in
                    Button("Восстановить этот проверенный архив…", role: .destructive) { confirmingRestore = true }
                        .disabled(backups.inspection.restoreRequest(now: context.date) == nil)
                }
                Text("Предварительный просмотр действителен до \(preview.expiresAt). После этого проверьте архив заново.").font(.caption).foregroundStyle(.secondary)
            }
        } header: { Text("Восстановление") }
    }
}
