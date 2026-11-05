import Foundation

public struct ModelSettings: Codable, Equatable {
    public var provider: String
    public var baseUrl: String
    public var modelId: String
    public var modelName: String
    public init(provider: String = "openrouter", baseUrl: String = "https://openrouter.ai/api/v1", modelId: String = "", modelName: String = "") {
        self.provider = provider; self.baseUrl = baseUrl; self.modelId = modelId; self.modelName = modelName
    }
}

public struct ProviderInfo: Codable, Identifiable {
    public let id: String
    public let label: String
    public let baseUrl: String
    public let description: String
    public let keyUrl: String
    public let requiresToken: Bool?
}

public struct ModelInfo: Codable, Identifiable, Hashable {
    public let id: String
    public let name: String
    public let inputPrice: Double?
    public let outputPrice: Double?
    public let contextLength: Int?
    public init(id: String, name: String, inputPrice: Double?, outputPrice: Double?, contextLength: Int? = nil) {
        self.id = id; self.name = name; self.inputPrice = inputPrice; self.outputPrice = outputPrice; self.contextLength = contextLength
    }
}

public struct SecureStorage: Decodable { public let available: Bool; public let label: String }
public struct BootstrapResponse: Decodable {
    public let csrfToken: String
    public let providers: [ProviderInfo]
    public let settings: ModelSettings
    public let tokenSaved: Bool
    public let storage: SecureStorage
    public let warning: String?
}
public struct TokenStatus: Decodable { public let tokenSaved: Bool }
public struct SavedSettings: Decodable { public let settings: ModelSettings; public let tokenSaved: Bool }
public struct CatalogResponse: Decodable {
    public let models: [ModelInfo]
    public let fetchedAt: String
    public let currency: String?
    public let priceUnit: String?
    public let truncated: Bool?
    public let message: String?
}
public struct ConnectionResponse: Decodable {
    public let status: String
    public let message: String
    public let checkedAt: String
    public let modelAvailable: Bool?
}
public struct HealthResponse: Decodable { public let appId: String; public let instanceId: String? }
public struct EmptyBody: Encodable { public init() {} }
public struct ScopeRequest: Encodable {
    public let provider: String; public let baseUrl: String
    public init(_ settings: ModelSettings) { provider = settings.provider; baseUrl = settings.baseUrl }
}
public struct ModelRequest: Encodable {
    public let provider: String; public let baseUrl: String; public let token: String?
    public init(_ settings: ModelSettings, token: String?) { provider = settings.provider; baseUrl = settings.baseUrl; self.token = token }
}
public struct SaveRequest: Encodable {
    public let provider: String; public let baseUrl: String; public let modelId: String; public let modelName: String; public let token: String?
    public init(_ settings: ModelSettings, token: String?) {
        provider = settings.provider; baseUrl = settings.baseUrl; modelId = settings.modelId; modelName = settings.modelName; self.token = token
    }
}
public struct ConnectionRequest: Encodable {
    public let provider: String; public let baseUrl: String; public let modelId: String; public let token: String?
    public init(_ settings: ModelSettings, token: String?) { provider = settings.provider; baseUrl = settings.baseUrl; modelId = settings.modelId; self.token = token }
}

public struct MemorySource: Codable, Identifiable, Hashable {
    public let id: String
    public let name: String
    public let kind: String
    public let path: String?
    public let host: String?
    public let username: String?
    public let port: Int?
    public let mailbox: String?
    public let enabled: Bool
    public let credentialSaved: Bool
    public let lastCheckedAt: String?
    public let status: String
    public let message: String?
    public let itemCount: Int?
    public let notionPageIds: [String]?
    public let githubRepositories: [String]?
    public let githubContent: [String]?
    public let mailProvider: String?
    public let mailImportEnabled: Bool?
    public let mailLookbackDays: Int?
    public var displayKind: String {
        kind == "imap" ? (MailProvider.detect(raw: mailProvider, host: host)?.title ?? "Другая почта") : SourceFormatting.label(kind)
    }
    public var canImportMemory: Bool {
        ["codex", "claude", "folder", "notion", "github"].contains(kind) || (kind == "imap" && mailImportEnabled == true)
    }
    public var location: String {
        if kind == "notion" { return "Выбрано страниц: \(notionPageIds?.count ?? 0)" }
        if kind == "github" { return "Выбрано репозиториев: \(githubRepositories?.count ?? 0)" }
        if kind == "imap" { return username ?? host ?? "" }
        return path ?? [username, host].compactMap { $0 }.joined(separator: " · ")
    }
}
public struct SourcesResponse: Decodable { public let sources: [MemorySource] }
public struct SourceResponse: Decodable { public let source: MemorySource }
public struct DeletedResponse: Decodable { public let deleted: Bool }
public struct AddSourceRequest: Encodable {
    public let kind: String; public let name: String; public let path: String?
    public let host: String?; public let username: String?; public let password: String?
    public let token: String?; public let notionPageIds: [String]?
    public let githubRepositories: [String]?; public let githubContent: [String]?
    public let mailProvider: String?; public let mailImportEnabled: Bool?; public let mailLookbackDays: Int?
    public let port = 993; public let mailbox = "INBOX"
    public init(kind: String, name: String, path: String? = nil, host: String? = nil, username: String? = nil, password: String? = nil, token: String? = nil, notionPageIds: [String]? = nil, githubRepositories: [String]? = nil, githubContent: [String]? = nil, mailProvider: String? = nil, mailImportEnabled: Bool? = nil, mailLookbackDays: Int? = nil) {
        self.kind = kind; self.name = name; self.path = path; self.host = host; self.username = username; self.password = password
        self.token = token; self.notionPageIds = notionPageIds
        self.githubRepositories = githubRepositories; self.githubContent = githubContent
        self.mailProvider = mailProvider; self.mailImportEnabled = mailImportEnabled; self.mailLookbackDays = mailLookbackDays
    }
}

public enum ModelFormatting {
    public static func price(_ value: Double?, currency: String) -> String {
        guard let value, value.isFinite, value >= 0 else { return "Нет данных" }
        if value == 0 { return "Бесплатно" }
        let formatter = NumberFormatter()
        formatter.locale = Locale(identifier: currency == "RUB" ? "ru_RU" : "en_US")
        formatter.maximumFractionDigits = 4
        formatter.minimumFractionDigits = 0
        let number = formatter.string(from: NSNumber(value: value)) ?? "—"
        return currency == "RUB" ? "\(number) ₽" : "$\(number)"
    }
    public static func sorted(_ models: [ModelInfo]) -> [ModelInfo] {
        models.sorted {
            let left = ($0.inputPrice ?? .infinity, $0.outputPrice ?? .infinity)
            let right = ($1.inputPrice ?? .infinity, $1.outputPrice ?? .infinity)
            if left.0 != right.0 { return left.0 < right.0 }
            if left.1 != right.1 { return left.1 < right.1 }
            return $0.name.localizedStandardCompare($1.name) == .orderedAscending
        }
    }
}
public enum SourceFormatting {
    public static func count(kind: String, count: Int?) -> String {
        guard let count else { return "Не проверено" }
        return "\(count) \(kind == "imap" ? "писем" : kind == "notion" ? "страниц" : kind == "github" ? "репозиториев" : "файлов")"
    }
    public static func label(_ kind: String) -> String {
        ["codex": "Codex", "claude": "Claude Code", "folder": "Папка", "imap": "Другая почта", "notion": "Notion", "github": "GitHub"][kind] ?? kind
    }
    public static func symbol(_ kind: String) -> String {
        ["codex": "terminal", "claude": "sparkle", "folder": "folder", "imap": "envelope", "notion": "doc.text", "github": "chevron.left.forwardslash.chevron.right"][kind] ?? "doc"
    }
}
