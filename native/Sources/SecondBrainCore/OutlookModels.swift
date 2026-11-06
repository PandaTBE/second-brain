import Foundation

public struct OutlookStartRequest: Encodable {
    public let clientId: String
    public init(clientId: String) { self.clientId = clientId.trimmingCharacters(in: .whitespacesAndNewlines) }
}
public struct OutlookFlowRequest: Encodable {
    public let flowId: String
    public init(flowId: String) { self.flowId = flowId }
}
public struct OutlookCancelResponse: Decodable {}
public struct OutlookStartResponse: Decodable {
    public let flowId: String
    public let userCode: String
    public let verificationUri: String
    public let expiresIn: Int
    public let interval: Int
}
public struct OutlookAccount: Decodable, Equatable {
    public let id: String
    public let username: String
    public let displayName: String
}
public struct OutlookPollResponse: Decodable {
    public let status: String
    public let authId: String?
    public let account: OutlookAccount?
    public let interval: Int?
}

/// Login codes and tickets are temporary view state; no OAuth tokens reach this model.
public struct OutlookSignInState {
    public struct Request: Equatable { fileprivate let revision: Int }
    public private(set) var flowId: String?
    public private(set) var userCode: String?
    public private(set) var verificationURL: URL?
    public private(set) var expiresAt: Date?
    public private(set) var interval = 5
    public private(set) var authId: String?
    public private(set) var account: OutlookAccount?
    public private(set) var isStarting = false
    private var revision = 0
    public init() {}
    public var isWaiting: Bool { flowId != nil && authId == nil }
    public var isAuthorized: Bool { authId != nil && account != nil }

    public static func validClientID(_ text: String) -> Bool {
        let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return value.count == 36 && UUID(uuidString: value) != nil
    }
    public static func trustedVerificationURL(_ text: String) -> URL? {
        guard let value = URLComponents(string: text), value.scheme?.lowercased() == "https",
              let host = value.host?.lowercased(), ["microsoft.com", "www.microsoft.com"].contains(host),
              value.path == "/devicelogin",
              value.port == nil || value.port == 443,
              value.user == nil, value.password == nil, value.query == nil, value.fragment == nil else { return nil }
        return URL(string: "https://microsoft.com/devicelogin")
    }
    @discardableResult public mutating func invalidate() -> String? {
        let previous = flowId
        revision += 1; flowId = nil; userCode = nil; verificationURL = nil; expiresAt = nil
        interval = 5; authId = nil; account = nil; isStarting = false
        return previous
    }
    public mutating func begin(clientID: String) -> Request? {
        guard Self.validClientID(clientID) else { return nil }
        invalidate(); isStarting = true
        return Request(revision: revision)
    }
    public func isCurrent(_ request: Request) -> Bool { request.revision == revision }
    public func pollDelay(now: Date = Date()) -> TimeInterval? {
        guard isWaiting, let expiresAt, now < expiresAt else { return nil }
        return min(TimeInterval(interval), expiresAt.timeIntervalSince(now))
    }
    @discardableResult public mutating func accept(_ response: OutlookStartResponse, request: Request, now: Date = Date()) -> Bool {
        guard isCurrent(request), isStarting, !response.flowId.isEmpty, response.flowId.count <= 512,
              !response.userCode.isEmpty, response.userCode.count <= 64,
              response.userCode.unicodeScalars.allSatisfy({ $0.value >= 32 && $0.value < 127 }),
              (1...3600).contains(response.expiresIn), (1...60).contains(response.interval),
              let url = Self.trustedVerificationURL(response.verificationUri) else { return false }
        flowId = response.flowId; userCode = response.userCode; verificationURL = url
        expiresAt = now.addingTimeInterval(TimeInterval(response.expiresIn)); interval = response.interval
        isStarting = false
        return true
    }
    @discardableResult public mutating func accept(_ response: OutlookPollResponse, request: Request, now: Date = Date()) -> Bool {
        guard isCurrent(request), isWaiting, let expiresAt, now < expiresAt else { return false }
        if response.status == "pending" || response.status == "slow_down" {
            if let next = response.interval {
                guard (1...60).contains(next) else { return false }
                interval = max(interval, next)
            } else if response.status == "slow_down" { interval = min(60, interval + 5) }
            return true
        }
        guard response.status == "completed", let ticket = response.authId,
              !ticket.isEmpty, ticket.count <= 512, let profile = response.account,
              !profile.id.isEmpty, !profile.username.isEmpty else { return false }
        authId = ticket; account = profile; userCode = nil; verificationURL = nil
        return true
    }
}
