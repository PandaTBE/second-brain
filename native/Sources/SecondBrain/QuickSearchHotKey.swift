import AppKit
import Carbon
import SecondBrainCore

extension QuickShortcutModifiers {
    init(eventFlags: NSEvent.ModifierFlags) {
        var result: Self = []
        if eventFlags.contains(.command) { result.insert(.command) }
        if eventFlags.contains(.option) { result.insert(.option) }
        if eventFlags.contains(.shift) { result.insert(.shift) }
        if eventFlags.contains(.control) { result.insert(.control) }
        self = result
    }
    var carbonFlags: UInt32 {
        var result: UInt32 = 0
        if contains(.command) { result |= UInt32(cmdKey) }
        if contains(.option) { result |= UInt32(optionKey) }
        if contains(.shift) { result |= UInt32(shiftKey) }
        if contains(.control) { result |= UInt32(controlKey) }
        return result
    }
}

/// Carbon hotkeys do not monitor global key events or require Accessibility permissions.
@MainActor
final class QuickSearchHotKey {
    private struct Registration { let reference: EventHotKeyRef; let id: UInt32 }
    private var binding: QuickShortcutBinding<Registration>?
    private var handler: EventHandlerRef?
    private var nextId: UInt32 = 1
    private let signature: OSType = 0x53425153 // SBQS
    var pressed: (() -> Void)?
    init() {}
    func install(_ shortcut: QuickSearchShortcut) throws {
        if binding?.shortcut == shortcut { return }
        try ensureHandler()
        let register: (QuickSearchShortcut) throws -> Registration = { [unowned self] candidate in
            var reference: EventHotKeyRef?
            let id = nextId; nextId &+= 1
            let status = RegisterEventHotKey(candidate.keyCode, candidate.modifiers.carbonFlags,
                                            EventHotKeyID(signature: signature, id: id), GetApplicationEventTarget(),
                                            OptionBits(kEventHotKeyExclusive), &reference)
            guard status == noErr, let reference else {
                throw ServiceError("Не удалось зарегистрировать сочетание (macOS: \(status)). Оно может быть занято. Прежнее сочетание сохранено; поиск доступен в меню.")
            }
            return Registration(reference: reference, id: id)
        }
        if let binding {
            self.binding = try binding.replacing(with: shortcut, register: register, unregister: { UnregisterEventHotKey($0.reference) })
        } else { binding = QuickShortcutBinding(shortcut: shortcut, registration: try register(shortcut)) }
    }
    func stop() {
        if let binding { UnregisterEventHotKey(binding.registration.reference) }
        if let handler { RemoveEventHandler(handler) }
        binding = nil; handler = nil
    }
    private func ensureHandler() throws {
        guard handler == nil else { return }
        var event = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let callback: EventHandlerUPP = { _, event, userData in
            guard let event, let userData else { return OSStatus(eventNotHandledErr) }
            var identifier = EventHotKeyID()
            let status = GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID), nil,
                                           MemoryLayout<EventHotKeyID>.size, nil, &identifier)
            guard status == noErr else { return status }
            // Carbon dispatches this application event handler on the main thread.
            return MainActor.assumeIsolated {
                let owner = Unmanaged<QuickSearchHotKey>.fromOpaque(userData).takeUnretainedValue()
                guard identifier.signature == owner.signature, identifier.id == owner.binding?.registration.id else {
                    return OSStatus(eventNotHandledErr)
                }
                owner.pressed?(); return noErr
            }
        }
        let status = InstallEventHandler(GetApplicationEventTarget(), callback, 1, &event,
                                        Unmanaged.passUnretained(self).toOpaque(), &handler)
        guard status == noErr else { throw ServiceError("Не удалось подключить сочетание клавиш (macOS: \(status)). Поиск доступен в меню.") }
    }
}
