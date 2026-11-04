import Foundation

private struct APIEnvelope<Value: Decodable>: Decodable {
    let success: Bool
    let data: Value?
    let error: ErrorBody?
    struct ErrorBody: Decodable { let code: String; let message: String }
}

public struct ServiceError: LocalizedError {
    public let message: String
    public var errorDescription: String? { message }
    public init(_ message: String) { self.message = message }
}

public struct APIClient {
    public let baseURL: URL
    public let csrf: String
    private let session: URLSession
    public init(baseURL: URL, csrf: String = "", session: URLSession = .shared) {
        self.baseURL = baseURL; self.csrf = csrf; self.session = session
    }
    public static var decoder: JSONDecoder {
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase; return decoder
    }
    public static var encoder: JSONEncoder {
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase; return encoder
    }
    public func makeRequest(_ path: String, method: String, body: Data? = nil, query: [URLQueryItem] = []) throws -> URLRequest {
        guard baseURL.scheme == "http", baseURL.host == "127.0.0.1", baseURL.port != nil,
              path.hasPrefix("/api/"), !path.contains(".."), !path.contains("?"), !path.contains("#"),
              let target = URL(string: path, relativeTo: baseURL)?.absoluteURL,
              target.host == baseURL.host, target.port == baseURL.port,
              var components = URLComponents(url: target, resolvingAgainstBaseURL: false) else {
            throw ServiceError("Недопустимый адрес локального сервиса.")
        }
        components.queryItems = query.isEmpty ? nil : query
        components.percentEncodedQuery = components.percentEncodedQuery?.replacingOccurrences(of: "+", with: "%2B")
        guard let url = components.url else { throw ServiceError("Недопустимый поисковый запрос.") }
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalAndRemoteCacheData, timeoutInterval: 30)
        request.httpMethod = method
        request.httpBody = body
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if method != "GET" {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.setValue(baseURL.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/")), forHTTPHeaderField: "Origin")
            request.setValue(csrf, forHTTPHeaderField: "X-Brain-CSRF")
        }
        return request
    }
    public func get<Value: Decodable>(_ path: String, query: [URLQueryItem] = []) async throws -> Value {
        try await perform(makeRequest(path, method: "GET", query: query))
    }
    public func send<Value: Decodable, Body: Encodable>(_ path: String, method: String = "POST", body: Body) async throws -> Value {
        try await perform(makeRequest(path, method: method, body: Self.encoder.encode(body)))
    }
    private func perform<Value: Decodable>(_ request: URLRequest) async throws -> Value {
        do {
            let (data, response) = try await session.data(for: request)
            guard let response = response as? HTTPURLResponse else { throw ServiceError("Локальный сервис вернул неожиданный ответ.") }
            let result: APIEnvelope<Value>
            do { result = try Self.decoder.decode(APIEnvelope<Value>.self, from: data) }
            catch { throw ServiceError("Не удалось прочитать ответ сервиса. Перезапустите приложение.") }
            guard response.statusCode < 400, result.success, let value = result.data else {
                throw ServiceError(result.error?.message ?? "Не удалось выполнить запрос.")
            }
            return value
        } catch let error as ServiceError { throw error }
        catch is CancellationError { throw CancellationError() }
        catch { throw ServiceError("Нет связи с локальным сервисом. Попробуйте ещё раз.") }
    }
}
