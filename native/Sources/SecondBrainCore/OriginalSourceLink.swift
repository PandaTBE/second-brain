import Foundation

public enum LocalOriginalAccess { case currentDevice, requiresRelocation }
public enum OriginalSourceTarget: Equatable {
    case web(URL), revealFile(URL)
    public var label: String {
        switch self {
        case .web: "Открыть оригинал в браузере"
        case .revealFile: "Показать файл в Finder"
        }
    }
}

public enum OriginalSourceLink {
    /// A portable restore can deny local destinations until paths are explicitly relocated.
    /// File destinations are revealed in Finder; this API never executes an original file.
    public static func target(uri: String, sourceKind: String, localFiles: LocalOriginalAccess) -> OriginalSourceTarget? {
        guard sourceKind != "apple_notes", uri.unicodeScalars.count <= 8192,
              uri.rangeOfCharacter(from: .whitespacesAndNewlines) == nil,
              uri.rangeOfCharacter(from: .controlCharacters) == nil,
              let decoded = uri.removingPercentEncoding,
              decoded.rangeOfCharacter(from: .controlCharacters) == nil,
              let parts = URLComponents(string: uri), parts.user == nil, parts.password == nil,
              let url = parts.url else { return nil }
        if parts.scheme?.lowercased() == "https", let host = parts.host, !host.isEmpty,
           parts.port == nil || parts.port == 443 { return .web(url) }
        guard localFiles == .currentDevice, ["folder", "codex", "claude", "qwen"].contains(sourceKind),
              parts.scheme?.lowercased() == "file", parts.host?.isEmpty != false,
              parts.port == nil, parts.query == nil,
              url.isFileURL, url.path.hasPrefix("/"),
              !url.path.split(separator: "/").contains("..") else { return nil }
        if let fragment = parts.fragment,
           fragment.range(of: #"^L[1-9][0-9]{0,8}(?:-L[1-9][0-9]{0,8})?$"#, options: .regularExpression) == nil { return nil }
        return .revealFile(URL(fileURLWithPath: url.path))
    }
}
