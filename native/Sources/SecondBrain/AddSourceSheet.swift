import SwiftUI
import AppKit
import SecondBrainCore

struct AddSourceSheet: View {
    @ObservedObject var store: AppStore
    let kind: String
    @Environment(\.dismiss) private var dismiss
    @ViewState private var name = ""
    @ViewState private var path = ""
    @ViewState private var host = ""
    @ViewState private var username = ""
    @ViewState private var password = ""
    @ViewState private var saving = false
    @ViewState private var error: String?
    private var mail: Bool { kind == "imap" }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 12) {
                Image(systemName: SourceFormatting.symbol(kind)).font(.title).foregroundStyle(.green)
                VStack(alignment: .leading, spacing: 4) { Text("Добавить \(SourceFormatting.label(kind))").font(.title2.weight(.semibold)); Text(mail ? "Безопасное подключение IMAP по TLS" : "Выберите папку, к которой разрешаете доступ").foregroundStyle(.secondary).font(.callout) }
            }.padding(24)
            Form {
                TextField("Название", text: $name, prompt: Text(mail ? "Рабочая почта" : SourceFormatting.label(kind)))
                if mail {
                    TextField("IMAP-сервер", text: $host, prompt: Text("imap.example.com"))
                    TextField("Логин или email", text: $username, prompt: Text("name@example.com"))
                    SecureField("Пароль приложения", text: $password)
                    LabeledContent("Подключение", value: "TLS · порт 993 · INBOX")
                    Text("Используйте отдельный пароль приложения, если это требуется вашей почтой. Он будет сохранён в Связке ключей macOS.").font(.caption).foregroundStyle(.secondary)
                } else {
                    LabeledContent("Папка") {
                        VStack(alignment: .trailing, spacing: 8) {
                            Text(path.isEmpty ? "Не выбрана" : path).font(.caption.monospaced()).foregroundStyle(path.isEmpty ? Color.secondary : Color.primary).textSelection(.enabled).lineLimit(3)
                            Button("Выбрать папку…") { chooseFolder() }
                        }
                    }
                    if let suggestion { Text("Обычно: \(suggestion). Выберите её явно через диалог.").font(.caption).foregroundStyle(.secondary) }
                    Text(kind == "folder" ? "Проверка найдёт файлы Markdown, TXT и JSONL." : "Проверка найдёт файлы сессий JSONL.").font(.caption).foregroundStyle(.secondary)
                }
                Label("Сохранение подключения не запускает чтение содержимого. Проверка доступа выполняется отдельной кнопкой.", systemImage: "hand.raised").font(.caption).foregroundStyle(.secondary)
                if let error { Text(error).foregroundStyle(.orange).font(.callout) }
            }.formStyle(.grouped).disabled(saving)
            HStack {
                if saving { ProgressView().controlSize(.small) }
                Spacer()
                Button("Отмена") { password = ""; dismiss() }.keyboardShortcut(.cancelAction).disabled(saving)
                Button("Добавить источник") { Task { await save() } }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(saving || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || (mail ? host.isEmpty || username.isEmpty || password.isEmpty : path.isEmpty))
            }.padding(20)
        }
        .frame(width: 570, height: mail ? 525 : 465)
        .onAppear { name = SourceFormatting.label(kind) }
    }

    private var suggestion: String? {
        switch kind { case "codex": "~/.codex/sessions"; case "claude": "~/.claude/projects"; default: nil }
    }
    private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.title = "Выберите папку \(SourceFormatting.label(kind))"
        panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = false
        panel.showsHiddenFiles = true; panel.prompt = "Выбрать папку"
        if let suggestion { panel.directoryURL = URL(fileURLWithPath: NSString(string: suggestion).expandingTildeInPath) }
        panel.begin { response in if response == .OK, let url = panel.url { path = url.path; error = nil } }
    }
    private func save() async {
        saving = true; error = nil
        defer { saving = false }
        let request = AddSourceRequest(kind: kind, name: name.trimmingCharacters(in: .whitespacesAndNewlines), path: mail ? nil : path, host: mail ? host.trimmingCharacters(in: .whitespacesAndNewlines) : nil, username: mail ? username.trimmingCharacters(in: .whitespacesAndNewlines) : nil, password: mail ? password : nil)
        if let message = await store.addSource(request) { error = message }
        else { password = ""; dismiss() }
    }
}
