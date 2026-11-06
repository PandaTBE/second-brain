import AppKit
import SwiftUI
import SecondBrainCore

/// SwiftUI SecureField's String bridge can discard canonically equal byte changes.
struct BackupSecureField: NSViewRepresentable {
    let title: String
    @Binding var value: BackupPassphraseValue
    let newPassword: Bool
    let changed: ((BackupPassphraseValue, BackupPassphraseValue) -> Void)?
    @Environment(\.isEnabled) private var isEnabled

    init(_ title: String, value: Binding<BackupPassphraseValue>, newPassword: Bool = false,
         changed: ((BackupPassphraseValue, BackupPassphraseValue) -> Void)? = nil) {
        self.title = title; _value = value; self.newPassword = newPassword; self.changed = changed
    }

    func makeCoordinator() -> Coordinator { Coordinator(self) }
    func makeNSView(context: Context) -> NSSecureTextField {
        let field = context.coordinator.makeField()
        context.coordinator.synchronize(field, enabled: isEnabled)
        return field
    }
    func updateNSView(_ field: NSSecureTextField, context: Context) {
        context.coordinator.parent = self
        context.coordinator.synchronize(field, enabled: isEnabled)
    }
    static func dismantleNSView(_ field: NSSecureTextField, coordinator: Coordinator) {
        field.delegate = nil
        field.currentEditor()?.string = ""
        field.stringValue = ""
    }

    final class Coordinator: NSObject, NSTextFieldDelegate {
        var parent: BackupSecureField
        init(_ parent: BackupSecureField) { self.parent = parent }

        func makeField() -> NSSecureTextField {
            let field = NSSecureTextField()
            field.placeholderString = parent.title
            field.setAccessibilityLabel(parent.title)
            field.setAccessibilityHelp(parent.title)
            field.toolTip = parent.title
            field.contentType = parent.newPassword ? .newPassword : nil
            field.isEditable = true; field.isSelectable = true
            field.bezelStyle = .roundedBezel
            field.font = .systemFont(ofSize: NSFont.systemFontSize)
            field.setContentHuggingPriority(.defaultLow, for: .horizontal)
            field.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
            field.delegate = self
            synchronize(field, enabled: true)
            return field
        }

        func synchronize(_ field: NSSecureTextField, enabled: Bool) {
            field.isEnabled = enabled
            let editor = field.currentEditor() as? NSTextView
            let current = BackupPassphraseValue(editor?.string ?? field.stringValue)
            guard current != parent.value else { return }
            let text = parent.value.text
            // Normal typing never enters this branch; do not disturb marked IME text.
            if let editor, editor.hasMarkedText(), !text.isEmpty { return }
            field.stringValue = text
            if let editor, BackupPassphraseValue(editor.string) != parent.value {
                editor.string = text
                editor.setSelectedRange(NSRange(location: text.utf16.count, length: 0))
            }
        }

        func controlTextDidChange(_ notification: Notification) {
            guard let field = notification.object as? NSSecureTextField, field.isEnabled else { return }
            let updated = BackupPassphraseValue(field.currentEditor()?.string ?? field.stringValue)
            let previous = parent.value
            guard updated != previous else { return }
            parent.value = updated
            parent.changed?(previous, updated)
        }
    }
}
