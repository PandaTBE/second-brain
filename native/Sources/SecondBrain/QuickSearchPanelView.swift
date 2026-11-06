import SwiftUI
import SecondBrainCore

struct QuickSearchPanelView: View {
    @ObservedObject var model: QuickSearchViewModel
    let settings: () -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("Быстрый поиск памяти", systemImage: "magnifyingglass").font(.title3.weight(.semibold))
                Spacer()
                Button("Настройки сочетания", action: settings).font(.caption)
            }
            QuickSearchField(text: model.query, changed: model.changeQuery, command: model.command).frame(height: 28)
            HStack {
                Picker("Режим поиска", selection: Binding(get: { model.mode }, set: model.changeMode)) {
                    ForEach(MemorySearchMode.allCases) { Text($0.title).tag($0) }
                }.frame(width: 230)
                Spacer()
                Text("До 8 материалов").font(.caption).foregroundStyle(.secondary)
            }
            Group {
                if model.state.isLoading {
                    ProgressView("Поиск…").frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let error = model.state.error {
                    ContentUnavailableView("Поиск или действие недоступны", systemImage: "exclamationmark.circle", description: Text(error))
                } else if model.state.results.isEmpty {
                    ContentUnavailableView(model.query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "Введите запрос" : "Ничего не найдено",
                                           systemImage: "magnifyingglass", description: Text("Поиск использует уже импортированную память. Платная модель не запускается."))
                } else {
                    List(selection: Binding(get: { model.state.selectedId }, set: model.select)) {
                        ForEach(model.state.results) { record in
                            row(record).tag(record.id)
                        }
                    }.listStyle(.inset).accessibilityLabel("Результаты быстрого поиска")
                }
            }.frame(maxWidth: .infinity, maxHeight: .infinity)
            if let diagnostics = model.state.diagnostics {
                Text(diagnostics.message).font(.caption2).foregroundStyle(.secondary)
                if let message = diagnostics.fallbackMessage { Text(message).font(.caption2).foregroundStyle(.orange) }
            }
            if let notice = model.notice { Text(notice).font(.caption).foregroundStyle(.secondary) }
            Divider()
            Text("Контекст содержит цитаты, источники и предупреждение о недоверенных данных. Копирование — только по вашему действию; бюджет 6000 символов.")
                .font(.caption2).foregroundStyle(.secondary)
            HStack {
                Button("Закрыть быстрый поиск") { model.command(.close) }
                Spacer()
                Button("Скопировать контекст выбранного материала") { model.perform(copy: true) }
                    .keyboardShortcut("c", modifiers: [.command, .shift]).disabled(!model.canAct)
                Button("Открыть выбранный материал") { model.perform(copy: false) }
                    .buttonStyle(.borderedProminent).disabled(!model.canAct)
            }.controlSize(.small)
            Text("↑ ↓ — выбор · Return — открыть · Esc — закрыть · ⇧⌘C — скопировать контекст")
                .font(.caption2).foregroundStyle(.secondary)
        }.padding(20).frame(minWidth: 760, minHeight: 580)
    }
    private func row(_ record: MemoryRecord) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(verbatim: record.title.isEmpty ? "Без названия" : record.title).font(.headline).lineLimit(1)
            HStack {
                Text(SourceFormatting.label(record.sourceKind ?? ""))
                if let stamp = record.provenanceTimestamp {
                    Text("\(stamp.label): \(stamp.date.formatted(date: .abbreviated, time: .shortened))")
                } else { Text("Дата источника не указана") }
                if let revision = record.revision { Text("Ревизия \(revision)") }
            }.font(.caption2).foregroundStyle(.secondary)
            if !record.displaySnippet.isEmpty {
                Text(verbatim: record.displaySnippet).font(.caption).lineLimit(2).foregroundStyle(.secondary)
            }
        }.padding(.vertical, 5)
    }
}
