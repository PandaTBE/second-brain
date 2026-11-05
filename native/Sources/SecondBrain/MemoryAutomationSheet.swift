import SwiftUI
import SecondBrainCore

struct MemoryAutomationSheet: View {
    @ObservedObject var memory: MemoryViewModel
    @Environment(\.dismiss) private var dismiss
    @ViewState private var confirmPaid = false
    @ViewState private var concurrencyText: String?

    private func flag(_ path: WritableKeyPath<MemoryScheduleRequest, Bool>) -> Binding<Bool> {
        Binding(get: { memory.scheduleDraft[keyPath: path] }, set: { value in var draft = memory.scheduleDraft; draft[keyPath: path] = value; memory.editSchedule(draft) })
    }
    private var batch: Binding<Int> {
        Binding(get: { memory.scheduleDraft.batchLimit }, set: { value in var draft = memory.scheduleDraft; draft.batchLimit = MemoryProcessingLimit.clamped(value); memory.editSchedule(draft) })
    }

    private var concurrency: Binding<String> {
        Binding(get: { concurrencyText ?? String(memory.scheduleDraft.cardConcurrency) }, set: { text in
            concurrencyText = text
            guard let value = MemoryCardConcurrency.parse(text) else { return }
            var draft = memory.scheduleDraft
            draft.cardConcurrency = value
            memory.editSchedule(draft)
        })
    }
    private var localProcessing: Bool { memory.status?.providerBackoff?.isLocalExecution == true }

    private var concurrencyIsValid: Bool {
        MemoryCardConcurrency.parse(concurrencyText ?? String(memory.scheduleDraft.cardConcurrency)) != nil
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 6) { Text("Автоматизация памяти").font(.title2.weight(.semibold)); Text(localProcessing ? "Локальный импорт и обработка через Ollama настраиваются отдельно." : "Локальный импорт и платная обработка настраиваются отдельно.").foregroundStyle(.secondary).font(.callout) }.padding(24)
            Form {
                if memory.synchronizationPaused {
                    Section("Синхронизация остановлена") {
                        Text("Настройки ниже сохранены. Автоимпорт и обработка возобновятся кнопкой «Возобновить синхронизацию» в памяти или строке меню. Поиск через MCP доступен.").font(.caption).foregroundStyle(.secondary)
                    }
                }
                Section("Локальный импорт") {
                    Toggle("Автоимпорт сессий Codex, Claude и Qwen Code", isOn: flag(\.autoImportEnabled))
                    Text("Проверка изменений каждые \(memory.status?.scheduler.pollSeconds ?? 30) секунд. Импорт после события завершения хода или паузы в записи около \(memory.status?.scheduler.quietSeconds ?? 90) секунд. Последующие изменения будут синхронизированы заново.").font(.caption).foregroundStyle(.secondary)
                    Toggle("Полная сверка локальных источников каждые 15 минут", isOn: flag(\.enabled))
                    Text("Полная сверка включает обычные папки. Ручной импорт не запускает модель, даже если автоматические карточки включены.").font(.caption).foregroundStyle(.secondary)
                }
                Section(localProcessing ? "Карточки через Ollama — на этом Mac" : "Карточки через API — платно") {
                    Toggle("Автоматически создавать карточки", isOn: flag(\.processCards))
                    Stepper("Не более \(memory.scheduleDraft.batchLimit) материалов за запуск", value: batch, in: MemoryProcessingLimit.range)
                    LabeledContent("Одновременные запросы") {
                        TextField("10", text: concurrency)
                            .textFieldStyle(.roundedBorder)
                            .multilineTextAlignment(.trailing)
                            .frame(width: 110)
                            .accessibilityLabel("Число параллельных запросов")
                    }
                    if !concurrencyIsValid {
                        Text("Введите целое число не меньше 1.").font(.caption).foregroundStyle(.orange)
                    }
                    Text("По умолчанию — 10. Можно задать любое положительное целое число. Одновременно обрабатывается не больше материалов, чем в текущей партии. Настройка применяется к следующему запуску, включая ручную обработку.").font(.caption).foregroundStyle(.secondary)
                    Text("Если включено, модель обрабатывает ожидающие материалы после автоимпорта и периодической сверки. Готовые неизменённые карточки не обрабатываются повторно.").font(.caption).foregroundStyle(.secondary)
                    Label(localProcessing ? "Локальная модель использует память и процессор Mac. Выполняется один запрос за раз; заданная параллельность сохранится для облачных моделей." : "Это лимит размера пакета, а не денежный бюджет. Каждый автоматический запуск может расходовать API-кредиты по тарифам выбранной модели.", systemImage: localProcessing ? "desktopcomputer" : "creditcard").font(.caption).foregroundStyle(.orange)
                }
                if memory.scheduleEdited { Text("Есть несохранённые изменения.").font(.caption).foregroundStyle(.secondary) }
                if let notice = memory.notice { Text(notice).font(.callout).foregroundStyle(memory.noticeIsError ? Color.orange : Color.green) }
            }.formStyle(.grouped).disabled(!memory.action.isEmpty)
            HStack {
                if !memory.action.isEmpty { ProgressView().controlSize(.small) }
                Spacer()
                Button("Готово") { dismiss() }.keyboardShortcut(.cancelAction).disabled(!memory.action.isEmpty)
                Button("Сохранить автоматизацию") {
                    if memory.scheduleDraft.processCards { confirmPaid = true }
                    else { Task { await memory.applySchedule() } }
                }.buttonStyle(.borderedProminent).disabled(memory.status == nil || !memory.scheduleEdited || !memory.action.isEmpty || !concurrencyIsValid)
            }.padding(20)
        }.frame(width: 590, height: 590)
        .alert(localProcessing ? "Разрешить автоматическую локальную обработку?" : "Разрешить автоматические платные запросы?", isPresented: $confirmPaid) {
            Button("Отмена", role: .cancel) {}
            Button("Разрешить и сохранить") { Task { await memory.applySchedule() } }
        } message: { Text("После автоматического импорта или сверки выбранная модель сможет обработать до \(memory.scheduleDraft.batchLimit) материалов за запуск. \(localProcessing ? "Ollama будет обрабатывать текст на этом Mac, без API-тарифа, используя его ресурсы." : "Это повторяющиеся API-расходы; денежный лимит этим параметром не задаётся.") Отключить обработку можно здесь в любой момент.") }
    }
}
