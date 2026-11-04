import Foundation
import Combine
import Darwin

@MainActor
public final class BackendService: ObservableObject {
    @Published public private(set) var isRunning = false
    @Published public private(set) var statusText = "Запускаем локальный сервис…"
    @Published public private(set) var baseURL: URL?
    private var process: Process?
    private var stopping = false

    public init() {}

    public static func projectDirectory() throws -> URL {
        if let path = ProcessInfo.processInfo.environment["SECOND_BRAIN_PROJECT_DIR"], !path.isEmpty {
            return URL(fileURLWithPath: path, isDirectory: true)
        }
        guard let resource = Bundle.main.resourceURL?.appendingPathComponent("project-location.txt"),
              let path = try? String(contentsOf: resource, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines), !path.isEmpty else {
            throw ServiceError("Не найден путь к проекту. Пересоберите приложение через ./scripts/build-app.sh в папке проекта.")
        }
        return URL(fileURLWithPath: path, isDirectory: true)
    }

    public func start() async throws -> URL {
        if isRunning, let baseURL { return baseURL }
        let port = try Self.availablePort()
        let launch = try BackendLaunchConfiguration.resolve(
            resourceURL: Bundle.main.resourceURL,
            projectDirectory: { try Self.projectDirectory() },
            port: port
        )
        let instance = UUID().uuidString
        let url = URL(string: "http://127.0.0.1:\(port)")!
        let child = Process()
        child.executableURL = launch.executableURL
        child.arguments = launch.arguments
        child.currentDirectoryURL = launch.currentDirectoryURL
        var environment = ProcessInfo.processInfo.environment
        environment["SECOND_BRAIN_INSTANCE_ID"] = instance
        child.environment = environment
        child.standardOutput = FileHandle.nullDevice
        child.standardError = FileHandle.nullDevice
        child.terminationHandler = { [weak self] exited in
            Task { @MainActor in
                guard let self, self.process === exited, !self.stopping else { return }
                self.isRunning = false
                self.baseURL = nil
                self.statusText = "Локальный сервис остановился"
            }
        }
        stopping = false
        try child.run()
        process = child
        statusText = "Запускаем локальный сервис…"
        do {
            let configuration = URLSessionConfiguration.ephemeral
            configuration.timeoutIntervalForRequest = 1
            configuration.timeoutIntervalForResource = 1
            let session = URLSession(configuration: configuration)
            defer { session.invalidateAndCancel() }
            let client = APIClient(baseURL: url, session: session)
            let deadline = Date().addingTimeInterval(15)
            while Date() < deadline {
                try Task.checkCancellation()
                guard child.isRunning else { throw ServiceError("Локальный сервис не запустился. Пересоберите приложение и попробуйте снова.") }
                if let health: HealthResponse = try? await client.get("/api/health"),
                   health.appId == "second-brain-settings", health.instanceId == instance {
                    baseURL = url
                    isRunning = true
                    statusText = "Локальный сервис работает"
                    return url
                }
                try await Task.sleep(nanoseconds: 150_000_000)
            }
            throw ServiceError("Сервис не успел запуститься. Проверьте установку зависимостей и попробуйте снова.")
        } catch {
            await stop()
            throw error
        }
    }

    public func stop() async {
        stopping = true
        guard let process else { return }
        if process.isRunning { process.terminate() }
        for _ in 0..<40 {
            if !process.isRunning { break }
            try? await Task.sleep(nanoseconds: 50_000_000)
        }
        if process.isRunning { Darwin.kill(process.processIdentifier, SIGKILL) }
        self.process = nil
        isRunning = false
        baseURL = nil
        statusText = "Сервис остановлен"
    }

    private static func availablePort() throws -> UInt16 {
        let descriptor = socket(AF_INET, SOCK_STREAM, 0)
        guard descriptor >= 0 else { throw ServiceError("Не удалось выбрать порт локального сервиса.") }
        defer { Darwin.close(descriptor) }
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_port = 0
        address.sin_addr = in_addr(s_addr: inet_addr("127.0.0.1"))
        let bound = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.bind(descriptor, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
        }
        guard bound == 0 else { throw ServiceError("Не удалось выбрать порт локального сервиса.") }
        var length = socklen_t(MemoryLayout<sockaddr_in>.size)
        let result = withUnsafeMutablePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { getsockname(descriptor, $0, &length) }
        }
        guard result == 0 else { throw ServiceError("Не удалось выбрать порт локального сервиса.") }
        return UInt16(bigEndian: address.sin_port)
    }
}
