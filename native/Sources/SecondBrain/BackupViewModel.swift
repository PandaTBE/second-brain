import SwiftUI
import SecondBrainCore

@MainActor
final class BackupViewModel: ObservableObject {
    @Published var status: BackupStatus?
    @Published var draft = BackupSettings()
    @Published var inspection = BackupInspectionState()
    @Published var loading = false
    @Published var action = ""
    @Published var error: String?
    @Published var notice: String?
    var willRestore: (() -> UUID?)?
    var restored: ((UUID) async throws -> Void)?
    private var operations: BackupOperations?
    private var statusRevision = 0
    var busy: Bool { !action.isEmpty }

    func configure(_ client: APIClient) {
        operations = BackupOperations(client: client)
        statusRevision += 1; status = nil; inspection.invalidate()
    }
    func invalidateInspection() { inspection.invalidate() }

    func refresh(resetDraft: Bool = false) async {
        guard let operations else { return }
        statusRevision += 1; let revision = statusRevision
        loading = true
        defer { if revision == statusRevision { loading = false } }
        do {
            let response = try await operations.status()
            guard !Task.isCancelled, revision == statusRevision else { return }
            if status == nil || resetDraft { draft = response.settings }
            status = response; error = nil
        } catch { if revision == statusRevision { self.error = error.localizedDescription } }
    }

    func saveSettings() async {
        guard let operations, !busy else { return }
        action = "Сохраняем расписание…"; error = nil; notice = nil; statusRevision += 1
        defer { action = "" }
        do {
            status = try await operations.save(draft)
            if let status { draft = status.settings }
            notice = "Настройки локальных копий сохранены."
        } catch { self.error = error.localizedDescription }
    }

    func createLocal() async {
        guard let operations, !busy else { return }
        action = "Создаём согласованную локальную копию…"; error = nil; notice = nil
        defer { action = "" }
        do {
            let response = try await operations.createLocal()
            notice = "Локальная копия создана: \(response.backup.name). Она не зашифрована."
            await refresh()
        } catch { self.error = error.localizedDescription }
    }

    func export(path: String, passphrase: String) async {
        guard let operations, !busy else { return }
        action = "Создаём зашифрованный архив…"; error = nil; notice = nil
        defer { action = "" }
        do {
            let result = try await operations.export(BackupExportRequest(path: path, passphrase: passphrase))
            guard result.encrypted, result.format == "age" else { throw ServiceError("Сервис не подтвердил шифрование архива.") }
            notice = "Зашифрованный архив сохранён. Для восстановления на другом Mac понадобится ваш пароль."
        } catch { self.error = error.localizedDescription }
    }

    func inspect(path: String, passphrase: String?) async {
        guard let operations, !busy else { return }
        let request = inspection.begin(path: path)
        action = "Проверяем архив…"; error = nil; notice = nil
        defer { action = "" }
        do {
            let response = try await operations.inspect(BackupInspectRequest(path: path, passphrase: passphrase))
            guard !Task.isCancelled else { return }
            if !inspection.accept(response, request: request) {
                throw ServiceError("Предварительный просмотр устарел. Проверьте архив заново.")
            }
        } catch {
            if inspection.fail(request) { self.error = error.localizedDescription }
        }
    }

    func restore() async {
        guard let operations, !busy, let request = inspection.restoreRequest() else {
            error = "Предварительный просмотр истёк. Проверьте архив заново."; return
        }
        guard let willRestore, let restored, let lifetime = willRestore() else {
            error = "Не удалось безопасно подготовить приложение к восстановлению. Перезапустите Second Brain."; return
        }
        // Revoke old actions before the POST can replace the database, including on timeout.
        inspection.invalidate(); statusRevision += 1; status = nil
        action = "Восстанавливаем память…"; error = nil; notice = nil
        defer { action = "" }
        do {
            let response = try await operations.restore(request)
            guard response.restored, response.paused else { throw ServiceError("Сервис не подтвердил безопасное завершение восстановления.") }
            try await restored(lifetime)
            await refresh(resetDraft: true)
            notice = "Память восстановлена. Синхронизация и модельные задания приостановлены; проверьте источники и подключения."
        } catch {
            self.error = "\(error.localizedDescription) Доступ к памяти оставлен закрытым. Перезапустите Second Brain для безопасного подключения к текущему состоянию."
        }
    }

    func delete(_ backup: ManagedBackup) async {
        guard let operations, !busy else { return }
        action = "Удаляем выбранную копию…"; error = nil; notice = nil
        defer { action = "" }
        do {
            let response = try await operations.delete(backup.id)
            guard response.deleted else { throw ServiceError("Копия не была удалена.") }
            await refresh(); notice = "Выбранная локальная копия удалена."
        } catch { self.error = error.localizedDescription }
    }
}
