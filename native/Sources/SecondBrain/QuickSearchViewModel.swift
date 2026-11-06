import SwiftUI
import AppKit
import SecondBrainCore

@MainActor
final class QuickSearchViewModel: ObservableObject {
    @Published private(set) var state = QuickSearchState()
    @Published private(set) var query = ""
    @Published private(set) var mode: MemorySearchMode = .lexical
    @Published private(set) var notice: String?
    private let memory: MemoryViewModel
    private var searchTask: Task<Void, Never>?
    private var actionTask: Task<Void, Never>?
    var openMaterial: ((MemoryRecord) -> Void)?
    var closePanel: (() -> Void)?
    var announce: ((String) -> Void)?
    init(memory: MemoryViewModel) { self.memory = memory }
    var canPresent: Bool { !memory.isRestoring }
    var canAct: Bool { canPresent && state.selectedRecord != nil && !state.isLoading && !state.isActing }
    func present() { guard canPresent else { dismiss(); return }; cancel(); query = ""; notice = nil; state.present() }
    func dismiss() { cancel(); state.dismiss(); notice = nil }
    private func cancel() { searchTask?.cancel(); actionTask?.cancel() }
    func changeQuery(_ value: String) {
        query = String(String.UnicodeScalarView(value.unicodeScalars.prefix(512)))
        search()
    }
    func changeMode(_ value: MemorySearchMode) { mode = value; search() }
    private func search() {
        cancel(); notice = nil
        guard let request = state.begin(query: query, mode: mode) else { return }
        searchTask = Task {
            do {
                try await Task.sleep(nanoseconds: 300_000_000)
                guard !Task.isCancelled, state.isCurrent(request) else { return }
                let response = try await memory.quickSearch(query: request.query, mode: request.mode)
                guard !Task.isCancelled, try state.accept(response, request: request) else { return }
                announce?(state.results.isEmpty ? "Ничего не найдено." : "Найдено материалов: \(state.results.count).")
            } catch {
                guard !Task.isCancelled, state.fail(error.localizedDescription, request: request) else { return }
                announce?("Поиск недоступен. Проверьте состояние локального сервиса.")
            }
        }
    }
    func select(_ id: String?) { actionTask?.cancel(); state.select(id); notice = nil }
    func command(_ command: QuickPanelKeyCommand) {
        switch command {
        case .moveDown: moveSelection(1)
        case .moveUp: moveSelection(-1)
        case .openSelected: perform(copy: false)
        case .copyContext: perform(copy: true)
        case .close: closePanel?()
        }
    }
    private func moveSelection(_ offset: Int) {
        let previous = state.selectedId
        actionTask?.cancel(); state.moveSelection(by: offset); notice = nil
        if previous != state.selectedId, let record = state.selectedRecord {
            let title = String(String.UnicodeScalarView(record.title.unicodeScalars.prefix(160)))
            announce?("Выбран материал: \(title).")
        }
    }
    func perform(copy: Bool) {
        guard canPresent, let action = state.beginAction() else { return }
        actionTask?.cancel(); notice = "Проверяем актуальные разрешения и ревизию…"
        actionTask = Task {
            defer { state.finishAction(action) }
            do {
                let record = try await memory.currentFactRecord(action.record.id)
                guard current(action) else { return }
                guard MemorySourceReference(record: action.record).matches(record) else {
                    throw ServiceError("Материал изменился. Повторите поиск и выберите его заново.")
                }
                if copy {
                    let parameters = try action.evidenceRequest()
                    let bundle = try await memory.evidenceBundle(parameters)
                    guard current(action) else { return }
                    var evidence = EvidenceState()
                    let request = evidence.begin(parameters)
                    guard try evidence.accept(bundle, request: request) else { return }
                    let text = try bundle.copySelectedContext(record: record)
                    guard current(action) else { return }
                    NSPasteboard.general.clearContents()
                    guard NSPasteboard.general.setString(text, forType: .string) else {
                        throw ServiceError("Не удалось скопировать контекст.")
                    }
                    notice = "Контекст выбранного материала скопирован: \(text.unicodeScalars.count) из 6000 символов."
                    announce?("Контекст выбранного материала скопирован.")
                } else {
                    guard current(action) else { return }
                    openMaterial?(record)
                }
            } catch {
                guard current(action) else { return }
                state.fail(error.localizedDescription, request: action.request); notice = nil
                announce?("Действие отменено. Материал изменился или недоступен.")
            }
        }
    }
    private func current(_ action: QuickSearchState.Action) -> Bool {
        canPresent && !Task.isCancelled && state.isCurrentAction(action)
    }
}
