import SwiftUI
import AppKit
import SecondBrainCore

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    var store: AppStore?
    var showMainWindow: (() -> Void)?
    private var quitting = false
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showMainWindow?()
        return true
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard !quitting else { return .terminateLater }
        quitting = true
        store?.quickSearch.stop()
        Task {
            await store?.service.stop()
            sender.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }
}

@main
struct SecondBrainApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var store = AppStore()

    var body: some Scene {
        Window("Second Brain", id: "main") {
            MainView(store: store, delegate: delegate)
                .frame(minWidth: 900, minHeight: 640)
                .tint(.green)
        }
        .defaultSize(width: 1120, height: 780)
        .commands {
            CommandMenu("Память") {
                Button("Быстрый поиск…") { store.quickSearch.show() }
                Button("Сочетание быстрого поиска…") { store.quickSearch.showSettings() }
            }
            CommandGroup(replacing: .appSettings) {
                Button("Настройки…") { store.section = .model; delegate.showMainWindow?() }
                    .keyboardShortcut(",", modifiers: .command)
            }
        }
        MenuBarExtra {
            StatusMenu(store: store)
        } label: {
            SyncStatusBarLabel(memory: store.memory, service: store.service)
        }
        .menuBarExtraStyle(.menu)
    }
}

struct StatusMenu: View {
    @ObservedObject var store: AppStore
    @ObservedObject private var service: BackendService
    @ObservedObject private var memory: MemoryViewModel
    @Environment(\.openWindow) private var openWindow
    init(store: AppStore) { self.store = store; self.service = store.service; self.memory = store.memory }
    var body: some View {
        let indicator = MemorySyncIndicator.resolve(serviceRunning: service.isRunning, status: memory.status, statusError: memory.statusError)
        Label(indicator.title, systemImage: indicator.symbol)
        if indicator == .active, let job = memory.status?.jobs.first(where: { $0.active }) {
            Text("\(job.title) · \(job.progressLabel)")
        }
        if let status = memory.status {
            Text("Материалы: \(status.records) · карточки: \(status.cardsReady)")
            if (status.cardsRetryPending ?? 0) > 0 { Text("Автоповтора ожидают: \(status.cardsRetryPending ?? 0)") }
            if (status.cardsWaitingUser ?? 0) > 0 { Text("Ожидают вашего действия: \(status.cardsWaitingUser ?? 0)") }
        }
        if let error = memory.statusError { Text(error) }
        if memory.synchronizationPaused { Text("Синхронизация остановлена · поиск через MCP доступен") }
        else if service.isRunning && memory.status != nil && memory.statusError == nil {
            Text("Поиск через MCP доступен")
        }
        Button(memory.synchronizationPaused ? "Возобновить синхронизацию" : "Остановить синхронизацию") {
            Task { await memory.setSynchronizationPaused(!memory.synchronizationPaused) }
        }
        .disabled(memory.status == nil || !memory.action.isEmpty || !service.isRunning)
        .help("Останавливает автоимпорт и обработку карточек. Уже начатые запросы модели могут завершиться; поиск через MCP останется доступен.")
        Divider()
        Button("Быстрый поиск…") { store.quickSearch.show() }
        Button("Сочетание быстрого поиска…") { store.quickSearch.showSettings() }
        Button("Открыть Second Brain") { openWindow(id: "main"); NSApp.activate(ignoringOtherApps: true) }
            .keyboardShortcut("o")
        Button("Модель и API…") { store.section = .model; openWindow(id: "main"); NSApp.activate(ignoringOtherApps: true) }
        Button("Память…") { store.section = .memory; openWindow(id: "main"); NSApp.activate(ignoringOtherApps: true) }
        Button("Источники…") { store.section = .sources; openWindow(id: "main"); NSApp.activate(ignoringOtherApps: true) }
        Divider()
        Text("При закрытии окна сервис остаётся в фоне")
        Button("Завершить Second Brain") { NSApp.terminate(nil) }.keyboardShortcut("q")
    }
}

struct MainView: View {
    @ObservedObject var store: AppStore
    @ObservedObject private var service: BackendService
    let delegate: AppDelegate
    @Environment(\.openWindow) private var openWindow
    init(store: AppStore, delegate: AppDelegate) { self.store = store; self.service = store.service; self.delegate = delegate }

    var body: some View {
        NavigationSplitView {
            List(selection: $store.section) {
                Section("Second Brain") {
                    ForEach(AppSection.allCases) { section in Label(section.rawValue, systemImage: section.symbol).tag(section) }
                }
            }
            .listStyle(.sidebar)
            .navigationSplitViewColumnWidth(min: 190, ideal: 215, max: 260)
            .safeAreaInset(edge: .bottom) {
                VStack(alignment: .leading, spacing: 7) {
                    Label(service.isRunning ? "Работает локально" : "Сервис не запущен", systemImage: service.isRunning ? "circle.fill" : "circle")
                        .font(.caption).foregroundStyle(service.isRunning ? Color.green : Color.secondary)
                    Text("Окно можно закрыть — приложение останется в строке меню.").font(.caption2).foregroundStyle(.secondary)
                }.padding(16)
            }
        } detail: {
            Group {
                if store.starting {
                    VStack(spacing: 14) { ProgressView(); Text("Запускаем Second Brain…").font(.headline); Text("Локальный сервис подготавливает настройки.").foregroundStyle(.secondary) }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let error = store.startupError {
                    ContentUnavailableView {
                        Label("Не удалось запустить сервис", systemImage: "exclamationmark.triangle")
                    } description: { Text(error) } actions: { Button("Повторить") { Task { await store.start() } }.buttonStyle(.borderedProminent) }
                } else if !service.isRunning && store.ready {
                    ContentUnavailableView {
                        Label("Локальный сервис остановлен", systemImage: "pause.circle")
                    } description: { Text("Настройки сохранены. Можно запустить сервис снова.") } actions: { Button("Запустить") { Task { await store.start() } } }
                } else {
                    switch store.section {
                    case .overview: OverviewView(store: store)
                    case .memory: MemoryView(store: store)
                    case .model: ModelSettingsView(store: store)
                    case .sources: SourcesView(store: store)
                    case .mcp: MCPView(memory: store.memory)
                    }
                }
            }
            .navigationTitle(store.section.rawValue)
        }
        .onAppear {
            delegate.store = store
            delegate.showMainWindow = { openWindow(id: "main"); NSApp.activate(ignoringOtherApps: true) }
            store.quickSearch.configure { record in
                store.section = .memory
                openWindow(id: "main"); NSApp.activate(ignoringOtherApps: true)
                Task { await store.memory.selectRecord(record.id) }
            }
            if !store.ready && !store.starting { Task { await store.start() } }
        }
    }
}
