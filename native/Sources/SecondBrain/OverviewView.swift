import SwiftUI
import SecondBrainCore

struct OverviewView: View {
    @ObservedObject var store: AppStore
    @ObservedObject private var memory: MemoryViewModel
    init(store: AppStore) { self.store = store; self.memory = store.memory }
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                VStack(alignment: .leading, spacing: 8) {
                    Image(systemName: "square.stack.3d.up.fill").font(.system(size: 38)).foregroundStyle(.green)
                    Text("Ваш второй мозг.\nНа вашем Mac.").font(.largeTitle.weight(.semibold))
                    Text("Подключите модель и выберите, откуда собирать знания.").foregroundStyle(.secondary)
                }.padding(.bottom, 6)
                HStack(alignment: .top, spacing: 16) {
                    readinessCard(title: "Модель", symbol: "cpu", value: store.hasSavedModel ? store.savedSettings.modelName : "Не выбрана", subtitle: store.hasSavedModel ? "Настройки сохранены" : "Выберите провайдера и добавьте API-токен", action: { store.section = .model })
                    readinessCard(title: "Источники", symbol: "tray.full", value: store.sources.isEmpty ? "Не добавлены" : "\(store.sources.count) подключений", subtitle: "Codex, Claude, Notion, GitHub, Gmail и Яндекс Почта", action: { store.section = .sources })
                }
                GroupBox {
                    VStack(alignment: .leading, spacing: 14) {
                        Label("Приложение работает в фоне", systemImage: "menubar.rectangle").font(.headline)
                        Text("Закройте окно, чтобы освободить рабочее место. Second Brain останется в Dock и строке меню. Открыть настройки можно оттуда в любой момент.")
                            .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        Divider()
                        Label("«Завершить Second Brain» остановит и локальный сервис.", systemImage: "power").font(.callout)
                    }.frame(maxWidth: .infinity, alignment: .leading).padding(12)
                }
                GroupBox {
                    VStack(alignment: .leading, spacing: 10) {
                        Label("Память", systemImage: "brain.head.profile").font(.headline)
                        if let status = memory.status {
                            Text("\(status.records) материалов · \(status.cardsReady) карточек · \(status.cardsPending) ожидают обработки").font(.title3.weight(.medium))
                            Text(status.scheduler.paused == true ? "Синхронизация остановлена. Поиск через MCP и материалы доступны." : status.scheduler.autoImportEnabled == false ? "Автоимпорт сессий выключен в настройках автоматизации." : "Codex и Claude импортируются после события завершения хода или паузы в записи. Приложение продолжает работу в строке меню.").font(.callout).foregroundStyle(.secondary)
                            if let job = status.jobs.first(where: { $0.active }) { MemoryJobView(job: job) }
                        } else { Text(memory.statusError ?? "Получаем состояние памяти…").foregroundStyle(.secondary) }
                        HStack { Button("Открыть память") { store.section = .memory }; Button("Подключить MCP") { store.section = .mcp } }
                        Text("Локальный поиск не запускает модель. Создание карточек через API включается отдельно и может расходовать кредиты.").font(.caption).foregroundStyle(.secondary)
                    }.frame(maxWidth: .infinity, alignment: .leading).padding(12)
                }
                Text("API-токены и пароли приложений сохраняются в Связке ключей macOS. Материалы для обработки будут передаваться выбранному провайдеру.")
                    .font(.caption).foregroundStyle(.secondary)
            }.padding(32).frame(maxWidth: 950, alignment: .leading)
        }
    }
    private func readinessCard(title: String, symbol: String, value: String, subtitle: String, action: @escaping () -> Void) -> some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 12) {
                Label(title, systemImage: symbol).foregroundStyle(.secondary)
                Text(value).font(.title3.weight(.semibold)).lineLimit(2)
                Text(subtitle).font(.callout).foregroundStyle(.secondary)
                Button("Настроить", action: action).padding(.top, 4)
            }.frame(maxWidth: .infinity, alignment: .leading).padding(12)
        }.frame(maxWidth: .infinity)
    }
}
