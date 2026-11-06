import SwiftUI
import AppKit
import SecondBrainCore

/// Local field-editor commands preserve IME composition and VoiceOver modifiers.
struct QuickSearchField: NSViewRepresentable {
    let text: String
    let changed: (String) -> Void
    let command: (QuickPanelKeyCommand) -> Void
    func makeCoordinator() -> Coordinator { Coordinator(self) }
    func makeNSView(context: Context) -> NSSearchField {
        let field = NSSearchField()
        field.placeholderString = "Поиск в памяти"
        field.setAccessibilityLabel("Поиск в памяти")
        field.delegate = context.coordinator
        field.sendsSearchStringImmediately = true
        return field
    }
    func updateNSView(_ field: NSSearchField, context: Context) {
        context.coordinator.parent = self
        if field.stringValue != text { field.stringValue = text }
    }
    final class Coordinator: NSObject, NSSearchFieldDelegate {
        var parent: QuickSearchField
        init(_ parent: QuickSearchField) { self.parent = parent }
        func controlTextDidChange(_ notification: Notification) {
            guard let field = notification.object as? NSSearchField else { return }
            parent.changed(field.stringValue)
        }
        func control(_ control: NSControl, textView: NSTextView, doCommandBy commandSelector: Selector) -> Bool {
            guard let event = NSApp.currentEvent,
                  let action = QuickPanelKeyCommand.resolve(keyCode: event.keyCode,
                    modifiers: QuickShortcutModifiers(eventFlags: event.modifierFlags), hasMarkedText: textView.hasMarkedText()) else { return false }
            parent.command(action); return true
        }
    }
}
