import SwiftUI
import AppKit
import SecondBrainCore

struct FactMaterialSnapshot: Identifiable {
    let id = UUID()
    let record: MemoryRecord
    let historicalRevision: Int?
}

struct FactCurrentMaterialSheet: View {
    @ObservedObject var memory: MemoryViewModel
    let snapshot: FactMaterialSnapshot
    let localOriginalAccess: LocalOriginalAccess
    @Environment(\.dismiss) private var dismiss
    @ViewState private var record: MemoryRecord?
    @ViewState private var isActive = false
    @ViewState private var busy = false
    @ViewState private var request = UUID()
    @ViewState private var task: Task<Void, Never>?
    @ViewState private var error: String?
    init(memory: MemoryViewModel, snapshot: FactMaterialSnapshot, localOriginalAccess: LocalOriginalAccess) {
        self.memory = memory; self.snapshot = snapshot; self.localOriginalAccess = localOriginalAccess
        _record = ViewState(initialValue: snapshot.record)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Текущий материал").font(.title2.weight(.semibold))
            if let historical = snapshot.historicalRevision {
                Text("Историческое подтверждение сохранено как снимок ревизии \(historical). Ниже показан доступный сейчас материал, его текст и адрес могут отличаться.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if let record {
                Text(verbatim: record.title).font(.headline).textSelection(.enabled)
                Text("Текущая ревизия: \(record.revision.map(String.init) ?? "не указана")").font(.caption).foregroundStyle(.secondary)
                if let uri = record.sourceUri { Text(verbatim: uri).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
                ScrollView {
                    Text(verbatim: record.text ?? "Исходный текст недоступен.")
                        .font(.body).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                }
            } else {
                ContentUnavailableView("Текущий материал недоступен", systemImage: "doc.questionmark", description: Text("Исторический снимок остаётся отдельным наблюдением. Доступ к оригиналу проверяется по текущим разрешениям."))
            }
            if let error { Text(error).font(.caption).foregroundStyle(.orange).textSelection(.enabled) }
            HStack {
                if busy { ProgressView().controlSize(.small); Text("Проверяем текущие разрешения…").font(.caption) }
                Spacer()
                Button("Закрыть") { close() }.keyboardShortcut(.cancelAction)
                if let record, let uri = record.sourceUri, let kind = record.sourceKind,
                   let target = OriginalSourceLink.target(uri: uri, sourceKind: kind, localFiles: localOriginalAccess) {
                    Button(target.label) { open(record) }.buttonStyle(.borderedProminent).disabled(busy)
                }
            }
        }.padding(24).frame(width: 760, height: 650)
        .onAppear { isActive = true }
        .onDisappear { invalidate() }
    }
    private func invalidate() { isActive = false; task?.cancel(); request = UUID(); busy = false }
    private func close() { invalidate(); dismiss() }
    private func current(_ token: UUID) -> Bool { isActive && request == token && !Task.isCancelled }
    private func open(_ displayed: MemoryRecord) {
        guard isActive, !busy else { return }
        request = UUID(); let token = request
        let reference = MemorySourceReference(record: displayed)
        busy = true; error = nil
        task = Task {
            defer { if current(token) { busy = false } }
            do {
                let fresh = try await memory.currentFactRecord(displayed.id)
                guard current(token) else { return }
                guard reference.matches(fresh) else {
                    record = fresh; error = "Материал или адрес изменились. Просмотрите новое состояние и выберите действие заново."
                    return
                }
                guard let uri = fresh.sourceUri, let kind = fresh.sourceKind,
                      let target = OriginalSourceLink.target(uri: uri, sourceKind: kind, localFiles: localOriginalAccess) else {
                    throw ServiceError("Оригинал недоступен для безопасного открытия.")
                }
                switch target {
                case .web(let url):
                    guard current(token) else { return }
                    if !NSWorkspace.shared.open(url) { throw ServiceError("Не удалось открыть браузер.") }
                case .revealFile(let url):
                    guard FileManager.default.fileExists(atPath: url.path) else { throw ServiceError("Файл не найден. Возможно, путь изменился.") }
                    guard current(token) else { return }
                    NSWorkspace.shared.activateFileViewerSelecting([url])
                }
            } catch {
                guard current(token) else { return }
                record = nil; self.error = error.localizedDescription
            }
        }
    }
}
