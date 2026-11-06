import SwiftUI
import AppKit
import SecondBrainCore

struct QuickShortcutSettings: View {
    @ObservedObject var controller: QuickSearchController
    @ViewState private var candidate: QuickSearchShortcut
    @ViewState private var message: String?
    init(controller: QuickSearchController) {
        self.controller = controller
        _candidate = ViewState(initialValue: controller.shortcut)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Сочетание быстрого поиска").font(.title2.weight(.semibold))
            Text("Работает в других приложениях, пока Second Brain запущен. По умолчанию — Command, Option, Shift, Пробел. Разрешения на управление компьютером и отслеживание ввода не требуются.")
                .font(.callout)
            Text("Текущее: \(controller.shortcut.spokenLabel)").font(.callout.weight(.medium))
            QuickShortcutRecorder(candidate: candidate) { result in
                switch result {
                case .success(let shortcut): candidate = shortcut; message = nil
                case .failure(let error): message = error.localizedDescription
                }
            }.frame(height: 38)
            Text("Нажмите кнопку, затем новое сочетание с минимум двумя модификаторами, включая Command или Control. Комбинации Control+Option оставлены VoiceOver. Esc отменяет запись; Tab переходит дальше. Системные сочетания могут отличаться на вашем Mac. Если новое сочетание занято, прежнее продолжит работать; пункт меню доступен всегда.")
                .font(.caption).foregroundStyle(.secondary)
            if let error = message ?? controller.registrationError { Text(error).font(.caption).foregroundStyle(.orange) }
            Spacer()
            HStack {
                Button("По умолчанию") { candidate = .standard; message = nil }
                Spacer()
                Button("Закрыть") { controller.closeSettings() }
                Button("Сохранить сочетание") {
                    do { try controller.saveShortcut(candidate); message = "Сочетание сохранено." }
                    catch { message = error.localizedDescription }
                }.buttonStyle(.borderedProminent)
            }
        }.padding(24).frame(width: 570, height: 360)
    }
}

private struct QuickShortcutRecorder: NSViewRepresentable {
    let candidate: QuickSearchShortcut
    let recorded: (Result<QuickSearchShortcut, Error>) -> Void
    func makeNSView(context: Context) -> RecorderButton {
        let button = RecorderButton()
        button.bezelStyle = .rounded; button.target = button; button.action = #selector(RecorderButton.beginRecording)
        button.setAccessibilityLabel("Записать сочетание быстрого поиска")
        return button
    }
    func updateNSView(_ button: RecorderButton, context: Context) {
        button.recorded = recorded
        if !button.recording { button.showStatus("Записать сочетание: \(candidate.spokenLabel)", announce: false) }
    }
    final class RecorderButton: NSButton {
        var recorded: ((Result<QuickSearchShortcut, Error>) -> Void)?
        var recording = false
        override var acceptsFirstResponder: Bool { true }
        @objc func beginRecording() {
            recording = true; showStatus("Нажмите новое сочетание. Esc отменяет запись; Tab переходит дальше."); window?.makeFirstResponder(self)
        }
        override func resignFirstResponder() -> Bool {
            if recording { recording = false; showStatus("Запись отменена") }
            return super.resignFirstResponder()
        }
        func showStatus(_ text: String, announce: Bool = true) {
            title = text; setAccessibilityValue(text)
            setAccessibilityHelp("Запись сочетания работает только в этой кнопке, пока она в фокусе.")
            NSAccessibility.post(element: self, notification: .valueChanged)
            if announce {
                NSAccessibility.post(element: self, notification: .announcementRequested,
                    userInfo: [.announcement: text, .priority: NSAccessibilityPriorityLevel.medium.rawValue])
            }
        }
        override func keyDown(with event: NSEvent) {
            guard recording else { super.keyDown(with: event); return }
            if event.keyCode == 53 { recording = false; showStatus("Запись отменена"); return }
            if event.keyCode == 48 {
                recording = false; showStatus("Запись отменена")
                if event.modifierFlags.contains(.shift) { window?.selectPreviousKeyView(self) }
                else { window?.selectNextKeyView(self) }
                return
            }
            let modifiers = QuickShortcutModifiers(eventFlags: event.modifierFlags)
            let label = event.keyCode == 49 ? "Пробел" : (event.charactersIgnoringModifiers?.uppercased() ?? "")
            let result = Result { try QuickSearchShortcut(keyCode: UInt32(event.keyCode), modifiers: modifiers, keyLabel: label) }
            recording = false
            switch result {
            case .success(let value): showStatus(value.spokenLabel)
            case .failure(let error): showStatus(error.localizedDescription)
            }
            recorded?(result)
        }
    }
}
