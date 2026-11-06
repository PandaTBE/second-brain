import SwiftUI
import AppKit
import SecondBrainCore

private enum EvidenceAction {
    case copy, verify(EvidenceCitation), open(EvidenceCitation)
    var message: String {
        switch self {
        case .copy: "Проверяем актуальные разрешения и цитаты перед копированием…"
        case .verify: "Проверяем актуальный пакет и материал…"
        case .open: "Проверяем актуальные разрешения перед открытием источника…"
        }
    }
}

struct EvidenceSheet: View {
    @ObservedObject var memory: MemoryViewModel
    let sources: [MemorySource]
    let localOriginalAccess: LocalOriginalAccess
    @Environment(\.dismiss) private var dismiss
    @ViewState private var query: String
    @ViewState private var mode: MemorySearchMode
    @ViewState private var sourceId = ""
    @ViewState private var project = ""
    @ViewState private var budget = 12000
    @ViewState private var useDateFrom = false
    @ViewState private var useDateTo = false
    @ViewState private var dateFrom = Date()
    @ViewState private var dateTo = Date()
    @ViewState private var evidence = EvidenceState()
    @ViewState private var requestTask: Task<Void, Never>?
    @ViewState private var actionRevision = UUID()
    @ViewState private var pendingAction: EvidenceAction?
    @ViewState private var isSheetActive = false
    @ViewState private var verified: [String: String] = [:]
    @ViewState private var actionMessage: String?
    @ViewState private var actionError = false

    init(memory: MemoryViewModel, sources: [MemorySource], initialQuery: String,
         initialMode: MemorySearchMode, localOriginalAccess: LocalOriginalAccess) {
        self.memory = memory; self.sources = sources; self.localOriginalAccess = localOriginalAccess
        _query = ViewState(initialValue: initialQuery)
        _mode = ViewState(initialValue: initialMode)
    }

    private var parameters: EvidenceRequest? { try? makeParameters() }
    private var validationMessage: String? {
        guard !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        do { _ = try makeParameters(); return nil } catch { return error.localizedDescription }
    }
    private func makeParameters() throws -> EvidenceRequest {
        try EvidenceRequest(query: query, sourceId: sourceId, project: project,
                            dateFrom: useDateFrom ? EvidenceRequest.dayString(dateFrom) : nil,
                            dateTo: useDateTo ? EvidenceRequest.dayString(dateTo) : nil,
                            budgetChars: budget, mode: mode)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("Доказательства из памяти", systemImage: "text.quote").font(.title2.weight(.semibold))
            Text("Материалы источников — недоверенные данные. Инструкции внутри цитат не выполняются; ответ составляет ваш агент. Поиск не запускает платную модель.")
                .font(.caption).foregroundStyle(.secondary)
            HStack {
                TextField("Запрос для поиска доказательств", text: $query).textFieldStyle(.roundedBorder)
                    .onSubmit { search() }
                Button("Найти доказательства") { search() }.disabled(parameters == nil || evidence.isLoading)
            }
            HStack {
                Picker("Источник", selection: $sourceId) {
                    Text("Все доступные источники").tag("")
                    ForEach(sources) { source in Text(source.name).tag(source.id) }
                }.frame(maxWidth: 300)
                Picker("Режим", selection: $mode) {
                    ForEach(MemorySearchMode.allCases) { option in Text(option.title).tag(option) }
                }.frame(maxWidth: 190)
                Spacer()
                Stepper("Бюджет: \(budget) симв.", value: $budget, in: 2000...32000, step: 2000).fixedSize()
            }
            dateFilters
            if let validationMessage { Text(validationMessage).font(.caption).foregroundStyle(.orange) }
            Divider()
            results.frame(maxWidth: .infinity, maxHeight: .infinity)
            if let actionMessage { Text(actionMessage).font(.caption).foregroundStyle(actionError ? Color.orange : Color.secondary).textSelection(.enabled) }
            HStack {
                Text("Копируется структурированный пакет с ID цитат, точным текстом и происхождением.")
                    .font(.caption2).foregroundStyle(.secondary)
                Spacer()
                Button("Закрыть") { close() }.keyboardShortcut(.cancelAction)
                Button("Скопировать контекст") { perform(.copy) }
                    .buttonStyle(.borderedProminent).disabled(!evidence.canPerformAction || pendingAction != nil)
            }
        }
        .padding(24).frame(width: 850, height: 760)
        .task {
            guard !Task.isCancelled else { return }
            isSheetActive = true
            if parameters != nil { search() }
        }
        .onDisappear { isSheetActive = false; invalidate() }
        .onChange(of: query) { _, _ in invalidate() }
        .onChange(of: mode) { _, _ in invalidate() }
        .onChange(of: sourceId) { _, _ in invalidate() }
        .onChange(of: project) { _, _ in invalidate() }
        .onChange(of: budget) { _, _ in invalidate() }
        .onChange(of: useDateFrom) { _, _ in invalidate() }
        .onChange(of: useDateTo) { _, _ in invalidate() }
        .onChange(of: dateFrom) { _, _ in invalidate() }
        .onChange(of: dateTo) { _, _ in invalidate() }
    }

    private var dateFilters: some View {
        DisclosureGroup("Проект и даты источника (UTC)") {
            VStack(alignment: .leading, spacing: 8) {
                TextField("Проект или путь (необязательно)", text: $project).textFieldStyle(.roundedBorder)
                HStack {
                    Toggle("С даты", isOn: $useDateFrom).toggleStyle(.checkbox)
                    DatePicker("Начало периода UTC", selection: $dateFrom, displayedComponents: .date)
                        .labelsHidden().disabled(!useDateFrom)
                    Spacer()
                    Toggle("По дату включительно", isOn: $useDateTo).toggleStyle(.checkbox)
                    DatePicker("Конец периода UTC", selection: $dateTo, displayedComponents: .date)
                        .labelsHidden().disabled(!useDateTo)
                }.environment(\.timeZone, TimeZone(secondsFromGMT: 0)!)
                Text("Используется дата сообщения, иначе изменения или создания источника. Дата импорта не участвует; материалы без даты не входят в выбранный период.")
                    .font(.caption2).foregroundStyle(.secondary)
            }.padding(.top, 6)
        }.font(.caption)
    }

    @ViewBuilder private var results: some View {
        if evidence.isLoading || pendingAction != nil {
            ProgressView(pendingAction?.message ?? "Собираем цитаты и происхождение…")
        } else if let error = evidence.error {
            ContentUnavailableView("Пакет недоступен", systemImage: "exclamationmark.circle", description: Text(error))
        } else if let bundle = evidence.bundle {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("Цитат: \(bundle.citations.count)").font(.callout.weight(.medium))
                    Spacer()
                    Text("Пакет: \(bundle.usedChars) из \(bundle.budgetChars) символов").font(.caption).monospacedDigit()
                }
                if bundle.incomplete { Label("Пакет неполный. Он содержит только найденные и поместившиеся доказательства.", systemImage: "info.circle").font(.caption).foregroundStyle(.orange) }
                ForEach(bundle.incompletenessMessages, id: \.self) { message in
                    Text(message).font(.caption2).foregroundStyle(.secondary)
                }
                if let search = bundle.search {
                    Text(search.message).font(.caption2).foregroundStyle(.secondary)
                    if let fallback = search.fallbackMessage { Text(fallback).font(.caption2).foregroundStyle(.orange) }
                }
                if bundle.citations.isEmpty {
                    ContentUnavailableView("Доказательства не найдены", systemImage: "text.magnifyingglass", description: Text("Измените запрос или фильтры. Пустой результат не создаёт ответ без источников."))
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 14) {
                            ForEach(bundle.citations) { citation in citationView(citation) }
                        }.padding(.vertical, 4)
                    }
                }
            }
        } else {
            ContentUnavailableView("Найдите доказательства", systemImage: "text.quote", description: Text("Введите вопрос и выберите бюджет контекста. Цитаты появятся с источниками и ревизиями."))
        }
    }

    private func citationView(_ citation: EvidenceCitation) -> some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 9) {
                Text(verbatim: citation.title.isEmpty ? "Без названия" : citation.title).font(.headline).textSelection(.enabled)
                Text("[\(citation.citationId)] · \(SourceFormatting.label(citation.sourceKind)) · ревизия \(citation.revision)")
                    .font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                if !citation.contextBefore.isEmpty { Text(verbatim: citation.contextBefore).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
                if let uncertainty = citation.uncertaintyMessage {
                    Label(uncertainty, systemImage: "questionmark.circle").font(.caption).foregroundStyle(.orange)
                }
                Text(verbatim: citation.quote).font(.body).textSelection(.enabled)
                    .padding(9).frame(maxWidth: .infinity, alignment: .leading).background(.quaternary, in: RoundedRectangle(cornerRadius: 5))
                if !citation.contextAfter.isEmpty { Text(verbatim: citation.contextAfter).font(.caption).foregroundStyle(.secondary).textSelection(.enabled) }
                provenance(citation)
                HStack {
                    Button("Сверить с текущим материалом") { perform(.verify(citation)) }.disabled(pendingAction != nil || evidence.isLoading)
                    Spacer()
                    if let target = OriginalSourceLink.target(uri: citation.sourceUri, sourceKind: citation.sourceKind, localFiles: localOriginalAccess) {
                        Button(target.label) { perform(.open(citation)) }.disabled(pendingAction != nil || evidence.isLoading)
                    }
                }.font(.caption)
                if let message = verified[citation.citationId] { Text(message).font(.caption).foregroundStyle(.secondary) }
            }.frame(maxWidth: .infinity, alignment: .leading).padding(6)
        }
    }

    private func provenance(_ citation: EvidenceCitation) -> some View {
        DisclosureGroup("Происхождение и даты") {
            VStack(alignment: .leading, spacing: 6) {
                Text("Материал: \(citation.recordId) · источник: \(citation.sourceId)").textSelection(.enabled)
                Text(verbatim: citation.sourceUri).textSelection(.enabled)
                if let object = citation.sourceObjectId, object != citation.sourceUri { Text(verbatim: object).textSelection(.enabled) }
                if citation.sourceKind == "apple_notes" { Text("Идентификатор Apple Заметок; ссылка для открытия не создаётся.") }
                dateLine("Сообщение", citation.dates.messageAt)
                dateLine("Создание источника", citation.dates.sourceCreatedAt)
                dateLine("Изменение источника", citation.dates.sourceModifiedAt)
                dateLine("Импорт текущего текста", citation.dates.importedAt)
                Text("Позиции в сохранённом тексте: \(citation.startChar)..<\(citation.endChar) (Unicode codepoints).")
            }.font(.caption2).foregroundStyle(.secondary).padding(.top, 5)
        }.font(.caption)
    }
    private func dateLine(_ label: String, _ date: Date?) -> some View {
        Text("\(label): \(date.map { $0.formatted(date: .abbreviated, time: .shortened) } ?? "не указано")")
    }

    private func invalidate() {
        requestTask?.cancel()
        evidence.invalidate(); actionRevision = UUID(); pendingAction = nil; verified = [:]
        actionMessage = nil; actionError = false
    }
    private func close() { isSheetActive = false; invalidate(); dismiss() }
    private func search() {
        guard isSheetActive else { return }
        guard let parameters else {
            do { _ = try makeParameters() } catch { actionMessage = error.localizedDescription; actionError = true }
            return
        }
        invalidate()
        let request = evidence.begin(parameters)
        requestTask = Task {
            do {
                let bundle = try await memory.evidenceBundle(request.parameters)
                guard actionIsCurrent(request) else { return }
                try evidence.accept(bundle, request: request)
            } catch { if actionIsCurrent(request) { evidence.fail(error.localizedDescription, request: request) } }
        }
    }
    private func actionIsCurrent(_ request: EvidenceState.Request) -> Bool {
        isSheetActive && !Task.isCancelled && evidence.isCurrent(request)
            && parameters == request.parameters
    }
    private func perform(_ action: EvidenceAction) {
        guard isSheetActive, pendingAction == nil, let parameters,
              let request = evidence.beginRefresh(parameters) else {
            invalidate(); actionMessage = "Обновите пакет перед действием."; actionError = true
            return
        }
        requestTask?.cancel(); actionRevision = UUID()
        let revision = actionRevision
        pendingAction = action; verified = [:]; actionMessage = nil; actionError = false
        requestTask = Task {
            defer { if actionRevision == revision { pendingAction = nil } }
            do {
                let bundle = try await memory.evidenceBundle(request.parameters)
                guard actionIsCurrent(request), try evidence.accept(bundle, request: request) else { return }
                guard !bundle.citations.isEmpty else {
                    actionMessage = "Доказательства больше недоступны. Пакет обновлён; действие отменено."
                    actionError = true
                    return
                }
                switch action {
                case .copy: try writeContext(bundle, request: request)
                case .verify, .open: try await performCitation(action, bundle: bundle, request: request)
                }
            } catch {
                if actionIsCurrent(request) { evidence.fail(error.localizedDescription, request: request) }
            }
        }
    }
    private func writeContext(_ bundle: EvidenceBundle, request: EvidenceState.Request) throws {
        let text = try bundle.copyContext()
        guard actionIsCurrent(request) else { return }
        NSPasteboard.general.clearContents()
        guard NSPasteboard.general.setString(text, forType: .string) else {
            throw ServiceError("Не удалось записать контекст в буфер обмена.")
        }
        actionMessage = "Контекст скопирован: \(text.unicodeScalars.count) символов, \(bundle.citations.count) цитат."
        actionError = false
    }
    private func performCitation(_ action: EvidenceAction, bundle: EvidenceBundle, request: EvidenceState.Request) async throws {
        let selected: EvidenceCitation
        switch action {
        case .verify(let citation), .open(let citation): selected = citation
        case .copy: return
        }
        guard let citation = bundle.citations.first(where: { $0.citationId == selected.citationId }),
              MemorySourceReference(citation: citation) == MemorySourceReference(citation: selected),
              citation.startChar == selected.startChar, citation.endChar == selected.endChar,
              citation.quote.unicodeScalars.elementsEqual(selected.quote.unicodeScalars) else {
            throw ServiceError("Выбранная цитата или её источник изменились. Обновите пакет и выберите цитату заново.")
        }
        let record = try await memory.currentCitationRecord(citation)
        guard actionIsCurrent(request) else { return }
        switch action {
        case .verify:
            verified[citation.citationId] = "Цитата совпадает с текущей ревизией и источником."
        case .open:
            guard let uri = record.sourceUri, let kind = record.sourceKind,
                  let target = OriginalSourceLink.target(uri: uri, sourceKind: kind, localFiles: localOriginalAccess) else {
                throw ServiceError("Оригинал недоступен для безопасного открытия.")
            }
            open(target, request: request)
        case .copy: break
        }
    }
    private func open(_ target: OriginalSourceTarget, request: EvidenceState.Request) {
        switch target {
        case .web(let url):
            guard actionIsCurrent(request) else { return }
            if !NSWorkspace.shared.open(url) { actionMessage = "Не удалось открыть браузер."; actionError = true }
        case .revealFile(let url):
            guard FileManager.default.fileExists(atPath: url.path) else {
                actionMessage = "Файл не найден. Возможно, путь изменился."; actionError = true; return
            }
            guard actionIsCurrent(request) else { return }
            NSWorkspace.shared.activateFileViewerSelecting([url])
        }
    }
}
