import Foundation

public struct QuickShortcutModifiers: OptionSet, Codable, Hashable {
    public let rawValue: UInt32
    public init(rawValue: UInt32) { self.rawValue = rawValue }
    public static let command = Self(rawValue: 1)
    public static let option = Self(rawValue: 2)
    public static let shift = Self(rawValue: 4)
    public static let control = Self(rawValue: 8)
}

public struct QuickSearchShortcut: Codable, Equatable {
    public let keyCode: UInt32
    public let modifiers: QuickShortcutModifiers
    public let keyLabel: String
    public static let standard = try! Self(keyCode: 49, modifiers: [.command, .option, .shift], keyLabel: "Пробел")
    public init(keyCode: UInt32, modifiers: QuickShortcutModifiers, keyLabel: String) throws {
        guard keyCode <= 127, modifiers.rawValue.nonzeroBitCount >= 2, modifiers.rawValue & ~15 == 0,
              modifiers.contains(.command) || modifiers.contains(.control),
              !keyLabel.isEmpty, keyLabel.unicodeScalars.count <= 32,
              keyLabel.rangeOfCharacter(from: .controlCharacters) == nil else {
            throw ServiceError("Выберите клавишу с Command или Control и дополнительными модификаторами.")
        }
        let reservedSpace: [QuickShortcutModifiers] = [[.control, .option], [.control], [.command], [.command, .option], [.command, .control], [.command, .shift]]
        guard !(keyCode == 49 && reservedSpace.contains(modifiers)),
              ![36, 48, 51, 53, 117, 123, 124, 125, 126].contains(keyCode),
              !(modifiers.contains(.control) && modifiers.contains(.option)) else {
            throw ServiceError("Это сочетание используется macOS или VoiceOver. Выберите другое.")
        }
        self.keyCode = keyCode; self.modifiers = modifiers; self.keyLabel = keyLabel
    }
    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        try self.init(keyCode: values.decode(UInt32.self, forKey: .keyCode),
                      modifiers: values.decode(QuickShortcutModifiers.self, forKey: .modifiers),
                      keyLabel: values.decode(String.self, forKey: .keyLabel))
    }
    public var displayLabel: String {
        [(QuickShortcutModifiers.control, "⌃"), (.option, "⌥"), (.shift, "⇧"), (.command, "⌘")]
            .filter { modifiers.contains($0.0) }.map(\.1).joined() + keyLabel
    }
    public var spokenLabel: String {
        [(QuickShortcutModifiers.control, "Control"), (.option, "Option"), (.shift, "Shift"), (.command, "Command")]
            .filter { modifiers.contains($0.0) }.map(\.1).joined(separator: ", ") + ", " + keyLabel
    }
}

/// New registration must succeed before releasing the previous working binding.
public struct QuickShortcutBinding<Registration> {
    public let shortcut: QuickSearchShortcut
    public let registration: Registration
    public init(shortcut: QuickSearchShortcut, registration: Registration) { self.shortcut = shortcut; self.registration = registration }
    public func replacing(with candidate: QuickSearchShortcut, register: (QuickSearchShortcut) throws -> Registration,
                          unregister: (Registration) -> Void) throws -> Self {
        let next = try register(candidate)
        unregister(registration)
        return Self(shortcut: candidate, registration: next)
    }
}

public enum QuickPanelKeyCommand: Equatable {
    case moveDown, moveUp, openSelected, copyContext, close
    public static func resolve(keyCode: UInt16, modifiers: QuickShortcutModifiers, hasMarkedText: Bool) -> Self? {
        guard !hasMarkedText else { return nil }
        if keyCode == 8 && modifiers == [.command, .shift] { return .copyContext }
        guard modifiers.isEmpty else { return nil }
        switch keyCode {
        case 125: return .moveDown
        case 126: return .moveUp
        case 36, 76: return .openSelected
        case 53: return .close
        default: return nil
        }
    }
}
