import Foundation

public extension ModelSettings {
    var isLocal: Bool { provider == "ollama" }
    var isCLI: Bool { provider == "gigacode_cli" }
    var requiresCredential: Bool { !isLocal && !isCLI }
    var privateNetworkAccessAllowed: Bool { provider == "custom" && allowPrivateNetwork }
    var connectionScopeKey: String {
        "\(provider)|\(baseUrl.trimmingCharacters(in: .whitespacesAndNewlines))|\(privateNetworkAccessAllowed)" + (isCLI ? "|\(cliPath.trimmingCharacters(in: .whitespacesAndNewlines))|\(gigacodeAutoCards)" : "")
    }

    func replacingBaseURL(_ value: String) -> ModelSettings {
        let sameEndpoint = baseUrl.trimmingCharacters(in: .whitespacesAndNewlines)
            == value.trimmingCharacters(in: .whitespacesAndNewlines)
        return ModelSettings(provider: provider, baseUrl: value, modelId: modelId, modelName: modelName,
                             allowPrivateNetwork: sameEndpoint && privateNetworkAccessAllowed, cliPath: cliPath, gigacodeAutoCards: gigacodeAutoCards)
    }

    func settingPrivateNetworkAccess(_ allowed: Bool) -> ModelSettings {
        ModelSettings(provider: provider, baseUrl: baseUrl, modelId: modelId, modelName: modelName,
                      allowPrivateNetwork: allowed, cliPath: cliPath, gigacodeAutoCards: gigacodeAutoCards)
    }

    func replacingCLIPath(_ value: String) -> ModelSettings {
        ModelSettings(provider: provider, baseUrl: baseUrl, modelId: modelId, modelName: modelName,
                      allowPrivateNetwork: privateNetworkAccessAllowed, cliPath: value,
                      gigacodeAutoCards: value == cliPath && gigacodeAutoCards)
    }

    func replacingModel(id: String, name: String) -> ModelSettings {
        ModelSettings(provider: provider, baseUrl: baseUrl, modelId: id, modelName: name,
                      allowPrivateNetwork: privateNetworkAccessAllowed, cliPath: cliPath,
                      gigacodeAutoCards: id == modelId && gigacodeAutoCards)
    }

    func settingGigaCodeAutoCards(_ enabled: Bool) -> ModelSettings {
        ModelSettings(provider: provider, baseUrl: baseUrl, modelId: modelId, modelName: modelName,
                      allowPrivateNetwork: privateNetworkAccessAllowed, cliPath: cliPath, gigacodeAutoCards: enabled)
    }

    var customEndpointValidationMessage: String? {
        guard provider == "custom" else { return nil }
        guard let url = URLComponents(string: baseUrl.trimmingCharacters(in: .whitespacesAndNewlines)),
              url.scheme?.lowercased() == "https", let hostname = url.host, !hostname.isEmpty,
              url.port == nil || url.port == 443, url.user == nil, url.password == nil,
              url.query == nil, url.fragment == nil else {
            return "Укажите HTTPS-адрес API без логина, параметров и нестандартного порта."
        }
        let host = hostname.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "[]"))
        let octets = host.split(separator: ".", omittingEmptySubsequences: false)
        let loopbackIPv4 = octets.count == 4 && octets[0] == "127" && octets.allSatisfy { UInt8($0) != nil }
        if host == "localhost" || host.hasSuffix(".localhost") || host == "::1" || loopbackIPv4 {
            return "Локальные адреса недоступны для своего API. Для Ollama выберите отдельное локальное подключение."
        }
        if !privateNetworkAccessAllowed && [".local", ".internal", ".lan"].contains(where: host.hasSuffix) {
            return "Для корпоративного API включите «Разрешить внутреннюю сеть для этого API»."
        }
        return nil
    }

    func canUseCredential(tokenSaved: Bool, tokenDraft: String) -> Bool {
        !requiresCredential || tokenSaved || !tokenDraft.isEmpty
    }

    func matchesSavedConnection(_ saved: ModelSettings, tokenSaved: Bool, tokenDraft: String) -> Bool {
        self == saved && !modelId.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && tokenDraft.isEmpty && (!requiresCredential || tokenSaved)
    }
}

public extension ProviderInfo {
    var needsToken: Bool { requiresToken ?? !["ollama", "gigacode_cli"].contains(id) }
}
