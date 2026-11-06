import SwiftUI
import SecondBrainCore

struct FactProofView: View {
    let proof: MemoryFactProof
    let busy: Bool
    let actionTitle: String
    let showCurrent: (() -> Void)?
    init(proof: MemoryFactProof, busy: Bool, actionTitle: String = "Текущий материал…", showCurrent: (() -> Void)? = nil) {
        self.proof = proof; self.busy = busy; self.actionTitle = actionTitle; self.showCurrent = showCurrent
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(verbatim: proof.title).font(.callout.weight(.medium)).textSelection(.enabled)
            Text("\(SourceFormatting.label(proof.sourceKind)) · ревизия \(proof.revision) · [\(proof.citationId)]")
                .font(.caption2).foregroundStyle(.secondary).textSelection(.enabled)
            if let before = proof.contextBefore, !before.isEmpty { Text(verbatim: before).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
            Text(verbatim: proof.quote).font(.callout).textSelection(.enabled)
                .padding(8).frame(maxWidth: .infinity, alignment: .leading)
                .background(.quaternary, in: RoundedRectangle(cornerRadius: 5))
            if let after = proof.contextAfter, !after.isEmpty { Text(verbatim: after).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
            DisclosureGroup("Происхождение и даты") {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Материал: \(proof.recordId) · источник: \(proof.sourceId)")
                    Text(verbatim: proof.sourceUri)
                    if proof.sourceKind == "apple_notes" { Text("Идентификатор Apple Заметок; ссылка для открытия не создаётся.") }
                    if proof.active == false, let status = proof.supportStatus {
                        Text("Статус при наблюдении: \(status.title)")
                    }
                    date("Сообщение", proof.dates.messageAt)
                    date("Создание источника", proof.dates.sourceCreatedAt)
                    date("Изменение источника", proof.dates.sourceModifiedAt)
                    date("Импорт текста", proof.dates.importedAt)
                    Text("Позиции в сохранённом тексте: \(proof.start)..<\(proof.end) (Unicode codepoints).")
                }.font(.caption2).foregroundStyle(.secondary).textSelection(.enabled).padding(.top, 4)
            }.font(.caption)
            if let showCurrent { Button(actionTitle, action: showCurrent).font(.caption).disabled(busy) }
        }.frame(maxWidth: .infinity, alignment: .leading)
    }
    private func date(_ label: String, _ value: Date?) -> some View {
        Text("\(label): \(value.map { $0.formatted(date: .abbreviated, time: .shortened) } ?? "не указано")")
    }
}
