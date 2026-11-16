import SwiftUI
import SecondBrainCore

@MainActor
final class TasksViewModel: ObservableObject {
    @Published private(set) var board: TaskBoard?
    @Published private(set) var isLoading = false
    @Published private(set) var error: String?
    @Published private(set) var action = ""
    @Published private(set) var notice: String?
    @Published private(set) var restoreEpoch = UUID()
    @Published private(set) var isRestoring = false
    private var client: APIClient?
    private var request = 0
    private let now: () -> Date
    init(client: APIClient? = nil, now: @escaping () -> Date = Date.init) {
        self.client = client; self.now = now
    }

    var busy: Bool { isRestoring || isLoading || !action.isEmpty || client == nil }

    func configure(_ client: APIClient) async {
        guard !isRestoring else { return }
        request += 1
        self.client = client
        board = nil; action = ""; isLoading = false; error = nil; notice = nil
        await load()
    }

    func invalidateForRestore() {
        restoreEpoch = UUID(); isRestoring = true; request += 1
        client = nil; board = nil; error = nil; notice = nil; action = ""; isLoading = false
    }

    func restoreDidComplete(_ client: APIClient) async {
        isRestoring = false
        await configure(client)
    }

    func reloadIfDayChanged() async {
        if board?.day != TaskDay.string(from: now()) { await load() }
    }

    func load() async {
        guard !isRestoring, action.isEmpty, let client else { return }
        let lifetime = restoreEpoch
        request += 1; let current = request
        let day = TaskDay.string(from: now())
        if board?.day != day { board = nil; notice = nil }
        isLoading = true; error = nil
        defer { if lifetime == restoreEpoch && current == request { isLoading = false } }
        do {
            let response: TaskBoard = try await client.get("/api/tasks", query: [URLQueryItem(name: "day", value: day)])
            guard lifetime == restoreEpoch, current == request, !isRestoring else { return }
            guard day == TaskDay.string(from: now()) else { await load(); return }
            guard response.day == day else { throw ServiceError("Дата доски изменилась. Обновите задачи.") }
            board = response
        } catch {
            if lifetime == restoreEpoch && current == request && !isRestoring { self.error = error.localizedDescription }
        }
    }

    @discardableResult
    func create(title: String, kind: TaskKind, stat: TaskStat, difficulty: Int) async -> Bool {
        guard !busy else { return false }
        let title = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty, title.unicodeScalars.count <= 200, (1...3).contains(difficulty) else {
            error = "Введите название от 1 до 200 символов и выберите сложность от 1 до 3."
            return false
        }
        return await mutate("create") { client, day in
            try await client.send("/api/tasks", body: TaskCreateRequest(day: day, title: title, kind: kind, stat: stat, difficulty: difficulty))
        }
    }

    func move(_ id: String, to status: TaskStatus) async {
        guard let task = board?.tasks.first(where: { $0.id == id }), task.status != status else { return }
        await mutate(id) { client, day in
            try await client.send("/api/tasks/\(id)/status", body: TaskStatusRequest(day: day, status: status))
        }
    }

    func delete(_ id: String) async {
        guard board?.tasks.contains(where: { $0.id == id }) == true else { return }
        await mutate(id) { client, day in
            try await client.send("/api/tasks/\(id)", method: "DELETE", body: TaskDayRequest(day: day))
        }
    }

    @discardableResult
    private func mutate(_ action: String, operation: (APIClient, String) async throws -> TaskBoard) async -> Bool {
        guard !busy, let client else { return false }
        let lifetime = restoreEpoch
        request += 1; let current = request
        let day = TaskDay.string(from: now())
        let previousLevel = board?.player.level
        self.action = action; error = nil; notice = nil
        defer { if lifetime == restoreEpoch && current == request { self.action = "" } }
        do {
            let response = try await operation(client, day)
            guard lifetime == restoreEpoch, current == request, !isRestoring else { return false }
            guard response.day == day else { throw ServiceError("Дата доски изменилась. Обновите задачи.") }
            if day == TaskDay.string(from: now()) {
                board = response
                if let previousLevel, response.player.level > previousLevel {
                    notice = "Уровень повышен: \(response.player.level)"
                }
            } else {
                self.action = ""
                await load()
            }
            return true
        } catch {
            if lifetime == restoreEpoch && current == request && !isRestoring { self.error = error.localizedDescription }
            return false
        }
    }
}
