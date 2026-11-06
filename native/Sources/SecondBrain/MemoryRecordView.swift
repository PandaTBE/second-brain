import SwiftUI
import AppKit
import SecondBrainCore

struct MemoryRecordView: View {
    let record: MemoryRecord?
    let loading: Bool
    let error: String?
    let copySource: (MemoryRecord) async -> Bool
    @ViewState private var copying = false
    @ViewState private var copyTask: Task<Void, Never>?
    @ViewState private var tab = "card"
    @ViewState private var copied = false

    private func cancelCopy() { copyTask?.cancel(); copyTask = nil; copying = false; copied = false }

    var body: some View {
        if loading {
            VStack(spacing: 12) { ProgressView(); Text("Открываем материал…").foregroundStyle(.secondary) }.frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if let error {
            ContentUnavailableView("Не удалось открыть материал", systemImage: "exclamationmark.circle", description: Text(error))
        } else if let record {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    Text(record.card?.title ?? record.title).font(.title3.weight(.semibold)).textSelection(.enabled)
                    HStack { Label(SourceFormatting.label(record.sourceKind ?? ""), systemImage: SourceFormatting.symbol(record.sourceKind ?? "")); Spacer(); Text("Ревизия \(record.revision ?? 1)") }.font(.caption).foregroundStyle(.secondary)
                    if let uri = record.sourceUri {
                        VStack(alignment: .leading, spacing: 7) {
                            Text(uri).font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
                            HStack {
                                Button(copying ? "Проверяем разрешение…" : copied ? "Ссылка скопирована" : "Скопировать ссылку на источник") {
                                    copying = true; copied = false
                                    copyTask = Task {
                                        let success = await copySource(record)
                                        guard !Task.isCancelled else { return }
                                        copying = false; copied = success
                                    }
                                }.font(.caption).disabled(copying)
                                if copying { ProgressView().controlSize(.small) }
                            }
                        }
                    }
                    Picker("Содержимое", selection: $tab) { Text("Карточка").tag("card"); Text("Исходный текст").tag("text") }.pickerStyle(.segmented)
                    if tab == "text" {
                        Text(record.text ?? "Исходный текст отсутствует.").font(.system(.body, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                    } else if let card = record.card {
                        if let summary = card.summary { Text(summary).textSelection(.enabled) }
                        if let keywords = card.keywords, !keywords.isEmpty { LabeledContent("Ключевые слова") { Text(keywords.joined(separator: ", ")).foregroundStyle(.secondary).textSelection(.enabled) }.font(.callout) }
                        if let questions = card.questions, !questions.isEmpty {
                            VStack(alignment: .leading, spacing: 8) { Text("Вопросы для поиска").font(.headline); ForEach(Array(questions.enumerated()), id: \.offset) { _, question in Text("• \(question)").font(.callout).textSelection(.enabled) } }
                        }
                        if let atoms = card.atoms, !atoms.isEmpty {
                            VStack(alignment: .leading, spacing: 12) {
                                Text("Факты и подтверждения").font(.headline)
                                ForEach(Array(atoms.enumerated()), id: \.offset) { _, atom in
                                    GroupBox {
                                        VStack(alignment: .leading, spacing: 9) {
                                            if let kind = atom.kind { Text(kind).font(.caption.weight(.medium)).foregroundStyle(.secondary) }
                                            if let text = atom.text { Text(text).font(.callout).textSelection(.enabled) }
                                            if let evidence = atom.evidence { Divider(); Text(evidence).font(.callout).foregroundStyle(.secondary).textSelection(.enabled) }
                                            if let uri = atom.sourceUri { Text(uri).font(.caption2.monospaced()).foregroundStyle(.secondary).textSelection(.enabled) }
                                            if let revision = atom.revision { Text("Подтверждение из ревизии \(revision)").font(.caption2).foregroundStyle(.secondary) }
                                        }.frame(maxWidth: .infinity, alignment: .leading).padding(5)
                                    }
                                }
                            }
                        }
                        if let model = record.model { Text("Карточка: \(model)\(record.promptVersion.map { " · промпт \($0)" } ?? "")").font(.caption).foregroundStyle(.secondary) }
                    } else {
                        ContentUnavailableView(record.cardStatus == "error" ? "Карточка не создана" : "Карточка ожидает обработки", systemImage: record.cardStatus == "error" ? "exclamationmark.circle" : "rectangle.stack", description: Text(record.cardError ?? "Полный текст уже доступен для поиска. Создайте карточки отдельным пакетом через выбранную модель."))
                        if let retry = record.retryStatusText {
                            VStack(alignment: .leading, spacing: 8) {
                                Label(record.cardWaitingForUser == true ? "Нужен ручной повтор" : "Автоматический повтор", systemImage: record.cardWaitingForUser == true ? "hand.raised" : "clock.arrow.circlepath").font(.headline)
                                Text(retry).font(.callout).textSelection(.enabled)
                                if record.cardWaitingForUser != true, let date = record.retryDate {
                                    Text("Не раньше \(date.formatted(date: .abbreviated, time: .shortened))").font(.caption).foregroundStyle(.secondary)
                                }
                            }.padding(12).background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
                        }
                    }
                }.padding(20)
            }
            .onChange(of: record.id) { _, _ in cancelCopy() }
            .onChange(of: record.revision) { _, _ in cancelCopy() }
            .onChange(of: record.sourceUri) { _, _ in cancelCopy() }
            .onDisappear { cancelCopy() }
        } else {
            ContentUnavailableView("Выберите материал", systemImage: "doc.text", description: Text("Здесь появятся карточка, подтверждающие фрагменты и полный исходный текст."))
        }
    }
}
