import SwiftUI
import AppKit

struct MCPView: View {
    @ObservedObject var memory: MemoryViewModel
    @ViewState private var client = "codex"
    @ViewState private var copied = false
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            VStack(alignment: .leading, spacing: 7) {
                Label("Подключить память к ИИ", systemImage: "point.3.connected.trianglepath.dotted").font(.title2.weight(.semibold))
                Text("MCP даёт клиенту доступ к локальному поиску и исходным материалам.").foregroundStyle(.secondary)
            }
            if memory.mcpLoading { ProgressView("Готовим конфигурацию…") }
            if let error = memory.mcpError { Label(error, systemImage: "exclamationmark.circle").foregroundStyle(.orange) }
            if let info = memory.mcp {
                Picker("Клиент", selection: $client) { Text("Codex").tag("codex"); Text("Claude Desktop").tag("claude") }.pickerStyle(.segmented).frame(maxWidth: 420)
                GroupBox(client == "codex" ? "Codex · TOML" : "Claude Desktop · JSON") {
                    ScrollView([.horizontal, .vertical]) { Text(client == "codex" ? info.codexToml : info.claudeJson).font(.system(.body, design: .monospaced)).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(14) }.frame(minHeight: 190)
                }
                HStack {
                    Button { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(client == "codex" ? info.codexToml : info.claudeJson, forType: .string); copied = true } label: { Label(copied ? "Скопировано" : "Скопировать конфигурацию", systemImage: copied ? "checkmark" : "doc.on.doc") }.buttonStyle(.borderedProminent)
                    Spacer()
                    Button("Обновить") { Task { await memory.loadMCP() } }
                }
                GroupBox {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("После добавления конфигурации перезапустите MCP в клиенте.").font(.headline)
                        Text(info.instructions).textSelection(.enabled)
                        Text("Этот экран показывает настройки, но не проверяет подключение другого приложения. Успешное соединение подтверждается в самом ИИ-клиенте.").font(.callout).foregroundStyle(.secondary)
                    }.frame(maxWidth: .infinity, alignment: .leading).padding(12)
                }
                Text("Команда сервера: \(info.command)").font(.caption.monospaced()).foregroundStyle(.secondary).textSelection(.enabled)
            } else if !memory.mcpLoading {
                Button("Загрузить конфигурацию") { Task { await memory.loadMCP() } }
            }
            Spacer(minLength: 0)
        }.padding(28).onChange(of: client) { _, _ in copied = false }.task { if memory.mcp == nil { await memory.loadMCP() } }
    }
}
