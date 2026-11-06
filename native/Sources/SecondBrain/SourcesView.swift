import SwiftUI
import SecondBrainCore

struct SourcesView: View {
    @ObservedObject var store: AppStore
    @ObservedObject private var memory: MemoryViewModel
    @ViewState private var addKind: String?
    @ViewState private var removeCandidate: MemorySource?
    @ViewState private var selectedID: String?
    init(store: AppStore) { self.store = store; self.memory = store.memory }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Источники знаний").font(.title2.weight(.semibold))
                    Text("Подключите историю ИИ, Apple Заметки, Notion, GitHub и почту.").font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                Menu {
                    Button("Codex", systemImage: "terminal") { addKind = "codex" }
                    Button("Claude Code", systemImage: "sparkle") { addKind = "claude" }
                    Button("Qwen Code", systemImage: "terminal") { addKind = "qwen" }
                    Button("Папка с заметками", systemImage: "folder") { addKind = "folder" }
                    Button("Apple Заметки", systemImage: "note.text") { addKind = "apple_notes" }
                    Button("Notion", systemImage: "doc.text") { addKind = "notion" }
                    Button("GitHub", systemImage: "chevron.left.forwardslash.chevron.right") { addKind = "github" }
                    Divider()
                    Button("Outlook / Microsoft 365", systemImage: "envelope.badge") { addKind = "outlook" }
                    Button("Gmail", systemImage: "envelope") { addKind = "gmail" }
                    Button("Яндекс Почта", systemImage: "envelope") { addKind = "yandex" }
                } label: { Label("Добавить источник", systemImage: "plus") }.menuStyle(.borderlessButton).fixedSize()
            }.padding(24)

            if store.sources.isEmpty {
                ContentUnavailableView {
                    Label("Источники пока не добавлены", systemImage: "tray")
                } description: { Text("Выберите историю ИИ, Apple Заметки, Notion, GitHub, папку заметок или почту.") } actions: {
                    Button("Добавить папку Codex") { addKind = "codex" }.buttonStyle(.borderedProminent)
                    Button("Подключить Gmail") { addKind = "gmail" }
                    Button("Подключить Яндекс Почту") { addKind = "yandex" }
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                Table(store.sources, selection: $selectedID) {
                    TableColumn("Источник") { source in
                        HStack(spacing: 10) {
                            Image(systemName: SourceFormatting.symbol(source.kind)).foregroundStyle(.green).frame(width: 22)
                            VStack(alignment: .leading, spacing: 4) {
                                Text(source.name).fontWeight(.medium)
                                Text(source.displayKind).font(.caption).foregroundStyle(.secondary)
                                if source.isMailSource && !source.canImportMemory {
                                    Text("Импорт писем выключен").font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }.padding(.vertical, 5)
                    }.width(min: 130, ideal: 170)
                    TableColumn("Папка или аккаунт") { source in Text(source.location).font(.caption).foregroundStyle(.secondary).lineLimit(2).help(source.location) }.width(min: 180, ideal: 300)
                    TableColumn("Проверка") { source in
                        VStack(alignment: .leading, spacing: 4) {
                            Label(source.status == "ok" ? "Доступ есть" : source.status == "error" ? "Ошибка доступа" : "Не проверено", systemImage: source.status == "ok" ? "checkmark.circle.fill" : source.status == "error" ? "exclamationmark.circle" : "circle.dotted")
                                .foregroundStyle(source.status == "ok" ? Color.green : source.status == "error" ? Color.orange : Color.secondary)
                            if source.status == "ok" { Text(SourceFormatting.count(kind: source.kind, count: source.itemCount)).font(.caption).foregroundStyle(.secondary) }
                        }.help(source.message ?? "")
                    }.width(min: 120, ideal: 150)
                    TableColumn("") { source in
                        HStack(spacing: 6) {
                            if store.busySources.contains(source.id) { ProgressView().controlSize(.small) }
                            if source.canImportMemory {
                                Button { Task { await memory.startImport(sourceId: source.id); store.section = .memory } } label: { Image(systemName: "square.and.arrow.down") }
                                    .help("Импортировать в память").accessibilityLabel("Импортировать \(source.name)").disabled(memory.busy || store.busySources.contains(source.id))
                            }
                            if source.isMailSource {
                                Button { Task { await store.setMailImport(source, enabled: !source.canImportMemory) } } label: {
                                    Image(systemName: source.canImportMemory ? "pause.circle" : "play.circle")
                                }
                                .help(source.canImportMemory ? "Приостановить импорт писем" : "Включить импорт писем")
                                .accessibilityLabel("\(source.canImportMemory ? "Приостановить импорт писем" : "Включить импорт писем") \(source.name)")
                                .disabled(store.busySources.contains(source.id))
                            }
                            Button { Task { await store.checkSource(source) } } label: { Image(systemName: "arrow.clockwise") }
                                .help("Проверить доступ").accessibilityLabel("Проверить \(source.name)").disabled(store.busySources.contains(source.id))
                            Button(role: .destructive) { removeCandidate = source } label: { Image(systemName: "trash") }
                                .help("Удалить подключение").accessibilityLabel("Удалить \(source.name)")
                        }.buttonStyle(.borderless)
                    }.width(136)
                }.padding(.horizontal, 12)
            }
            if let notice = store.notice { Text(notice).font(.callout).foregroundStyle(store.noticeIsError ? Color.orange : Color.green).padding(.horizontal, 24).padding(.bottom, 12) }
            VStack(alignment: .leading, spacing: 8) {
                Label(memory.status?.scheduler.autoImportEnabled == false ? "Автоимпорт сессий приостановлен" : "Codex, Claude и Qwen Code автоматически попадают в память", systemImage: "arrow.triangle.2.circlepath").font(.callout.weight(.medium))
                Text("Сессии импортируются после завершения хода или паузы в записи. Выбранные папки Apple Заметок, страницы Notion, материалы GitHub и входящие Outlook/Gmail/Яндекс Почты с включённым импортом обновляются по расписанию памяти; их также можно импортировать кнопкой. Проверка почты считает входящие без чтения тел. Письма не помечаются прочитанными и не удаляются.").font(.caption).foregroundStyle(.secondary)
            }.frame(maxWidth: .infinity, alignment: .leading).padding(20).background(.bar)
        }
        .toolbar { Button { Task { await store.refreshSources() } } label: { Label("Обновить", systemImage: "arrow.clockwise") }.disabled(store.sourcesLoading) }
        .sheet(isPresented: Binding(get: { addKind != nil }, set: { if !$0 { addKind = nil } })) {
            if let kind = addKind, let provider = MailProvider(rawValue: kind) {
                MailSourceSheet(store: store, provider: provider)
            }
            else if addKind == "apple_notes" { AppleNotesSourceSheet(store: store) }
            else if addKind == "outlook" { OutlookSourceSheet(store: store) }
            else if addKind == "notion" { NotionSourceSheet(store: store) }
            else if addKind == "github" { GitHubSourceSheet(store: store) }
            else if let kind = addKind { AddSourceSheet(store: store, kind: kind) }
        }
        .alert("Удалить подключение?", isPresented: Binding(get: { removeCandidate != nil }, set: { if !$0 { removeCandidate = nil } })) {
            Button("Отмена", role: .cancel) { removeCandidate = nil }
            Button("Удалить подключение", role: .destructive) { if let source = removeCandidate { Task { await store.removeSource(source) } }; removeCandidate = nil }
        } message: { Text("Исходные файлы, Apple Заметки, страницы Notion, репозитории GitHub и письма останутся на месте. Будут удалены настройки подключения и сохранённый секрет этого источника.") }
    }
}
