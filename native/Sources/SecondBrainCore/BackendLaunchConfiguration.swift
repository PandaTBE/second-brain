import Foundation

/// Resolves a bundled service before consulting a developer checkout.
public struct BackendLaunchConfiguration {
    public let executableURL: URL
    public let arguments: [String]
    public let currentDirectoryURL: URL?

    public static func resolve(
        resourceURL: URL?,
        projectDirectory: () throws -> URL,
        port: UInt16,
        isExecutable: (URL) -> Bool = { FileManager.default.isExecutableFile(atPath: $0.path) }
    ) throws -> BackendLaunchConfiguration {
        if let helper = resourceURL?.appendingPathComponent("Backend/SecondBrainHelper"),
           isExecutable(helper) {
            return BackendLaunchConfiguration(
                executableURL: helper,
                arguments: ["serve", "--port", String(port)],
                currentDirectoryURL: nil
            )
        }

        let root = try projectDirectory()
        let python = root.appendingPathComponent(".venv/bin/python")
        guard isExecutable(python) else {
            throw ServiceError("Сервис приложения не найден. Для готовой версии скачайте приложение заново. Для сборки из исходников установите зависимости через start.command.")
        }
        return BackendLaunchConfiguration(
            executableURL: python,
            arguments: ["-m", "uvicorn", "brain_settings.app:create_app", "--factory",
                        "--host", "127.0.0.1", "--port", String(port),
                        "--no-access-log", "--log-level", "warning"],
            currentDirectoryURL: root
        )
    }
}
