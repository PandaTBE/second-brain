import Foundation

public extension ModelSettings {
    var isLocal: Bool { provider == "ollama" }
    var requiresCredential: Bool { !isLocal }

    func canUseCredential(tokenSaved: Bool, tokenDraft: String) -> Bool {
        !requiresCredential || tokenSaved || !tokenDraft.isEmpty
    }

    func matchesSavedConnection(_ saved: ModelSettings, tokenSaved: Bool, tokenDraft: String) -> Bool {
        self == saved && !modelId.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && tokenDraft.isEmpty && (!requiresCredential || tokenSaved)
    }
}

public extension ProviderInfo {
    var needsToken: Bool { requiresToken ?? (id != "ollama") }
}
