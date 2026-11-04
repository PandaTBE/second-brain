import Foundation

public struct MailImportToggleRequest: Encodable {
    public let enabled: Bool
    public init(enabled: Bool) { self.enabled = enabled }
}

public enum MailProvider: String, CaseIterable, Identifiable {
    case gmail, yandex
    public var id: String { rawValue }
    public var title: String { self == .gmail ? "Gmail" : "Яндекс Почта" }
    public var exampleEmail: String { self == .gmail ? "name@gmail.com" : "name@yandex.ru" }
    public var helpURL: URL? {
        URL(string: self == .gmail
            ? "https://support.google.com/accounts/answer/185833?hl=ru"
            : "https://yandex.ru/support/yandex-360/customers/mail/ru/mail-clients/others")
    }
    public var passwordURL: URL? {
        URL(string: self == .gmail
            ? "https://myaccount.google.com/apppasswords"
            : "https://passport.yandex.ru/security/app-passwords")
    }
    public static func detect(raw: String?, host: String?) -> MailProvider? {
        if let raw, let provider = MailProvider(rawValue: raw) { return provider }
        switch host?.lowercased() {
        case "imap.gmail.com": return .gmail
        case "imap.yandex.ru", "imap.yandex.com": return .yandex
        default: return nil
        }
    }
    public func canSave(email: String, password: String) -> Bool {
        let address = email.trimmingCharacters(in: .whitespacesAndNewlines)
        let parts = address.split(separator: "@", omittingEmptySubsequences: false)
        return address.count <= 320 && parts.count == 2 && !parts[0].isEmpty
            && parts[1].contains(".") && !parts[1].hasPrefix(".") && !parts[1].hasSuffix(".")
            && address.unicodeScalars.allSatisfy { $0.value > 32 && $0.value < 127 }
            && !password.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && password.count <= 4096
            && password.unicodeScalars.allSatisfy { $0.value >= 32 && $0.value < 127 }
    }
}

public enum MailImportPeriod: Int, CaseIterable, Identifiable {
    case last30Days = 30, last90Days = 90, lastYear = 365, allHistory = 0
    public var id: Int { rawValue }
    public var title: String {
        switch self {
        case .last30Days: return "За последние 30 дней"
        case .last90Days: return "За последние 90 дней"
        case .lastYear: return "За последний год"
        case .allHistory: return "Вся история входящих"
        }
    }
}
