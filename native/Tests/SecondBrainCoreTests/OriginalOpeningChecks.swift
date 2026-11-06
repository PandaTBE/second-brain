import Foundation
import SecondBrainCore

private struct OriginalOpeningFailure: Error { let message: String }
private final class OriginalOpeningProtocol: URLProtocol {
    static var responseReference: [String: Any] = [:]
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let body: [String: Any] = ["success": true, "data": ["available": true,
            "uri": "file:///synthetic/current.md", "reference": Self.responseReference]]
        let data = try! JSONSerialization.data(withJSONObject: body)
        let response = HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.02) { [self] in
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        }
    }
    override func stopLoading() {}
}

extension CoreChecks {
    static func originalOpeningAfterAwait() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [OriginalOpeningProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:50321")!, session: session)
        let reference: [String: Any] = ["id": "one", "revision": 3, "source_id": "source",
            "source_kind": "folder", "source_object_id": "note.md::chunk:0",
            "source_uri": "file:///synthetic/previous.md"]
        let record = try APIClient.decoder.decode(MemoryRecord.self,
            from: JSONSerialization.data(withJSONObject: reference.merging(["title": "Synthetic"], uniquingKeysWith: { _, new in new })))
        let operation = OriginalSourceOperations(client: client)
        for update: [String: Any] in [["revision": 4], ["source_uri": "file:///synthetic/changed.md"],
                                     ["source_id": "other"], ["source_object_id": "other.md"]] {
            OriginalOpeningProtocol.responseReference = reference.merging(update, uniquingKeysWith: { _, new in new })
            do {
                _ = try await operation.target(for: record)
                throw OriginalOpeningFailure(message: "An asynchronous stale reference authorized opening")
            } catch is ServiceError { }
        }
        OriginalOpeningProtocol.responseReference = reference
        guard try await operation.target(for: record) == .revealFile(URL(fileURLWithPath: "/synthetic/current.md")) else {
            throw OriginalOpeningFailure(message: "The exact current reference did not authorize its safe target")
        }
    }
}
