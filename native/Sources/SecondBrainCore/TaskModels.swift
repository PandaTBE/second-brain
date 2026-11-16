import Foundation

public enum TaskKind: String, Codable, CaseIterable, Identifiable {
    case todo, daily
    public var id: String { rawValue }
    public var title: String { self == .daily ? "Ежедневная" : "Задача" }
}

public enum TaskStat: String, Codable, CaseIterable, Identifiable {
    case strength, agility, intelligence, vitality, perception
    public var id: String { rawValue }
    public var title: String {
        switch self {
        case .strength: "Сила"
        case .agility: "Ловкость"
        case .intelligence: "Интеллект"
        case .vitality: "Выносливость"
        case .perception: "Восприятие"
        }
    }
}

public enum TaskStatus: String, Codable, CaseIterable, Identifiable {
    case todo, doing, done
    public var id: String { rawValue }
    public var title: String {
        switch self {
        case .todo: "К выполнению"
        case .doing: "В работе"
        case .done: "Готово"
        }
    }
}

public struct TrackerTask: Decodable, Identifiable, Equatable {
    public let id: String
    public let title: String
    public let kind: TaskKind
    public let stat: TaskStat
    public let difficulty: Int
    public let status: TaskStatus
    public let xp: Int
}

public struct TaskStats: Decodable, Equatable {
    public let strength: Int
    public let agility: Int
    public let intelligence: Int
    public let vitality: Int
    public let perception: Int
    public subscript(stat: TaskStat) -> Int {
        switch stat {
        case .strength: strength
        case .agility: agility
        case .intelligence: intelligence
        case .vitality: vitality
        case .perception: perception
        }
    }
}

public struct TaskPlayer: Decodable, Equatable {
    public let xp: Int
    public let level: Int
    public let rank: String
    public let levelXp: Int
    public let nextLevelXp: Int
    public let stats: TaskStats
    public var levelProgress: Double {
        min(1, max(0, (Double(xp) - Double(levelXp)) / max(1, Double(nextLevelXp) - Double(levelXp))))
    }
}

public struct TaskBoard: Decodable, Equatable {
    public let day: String
    public let tasks: [TrackerTask]
    public let player: TaskPlayer
}

public struct TaskCreateRequest: Encodable {
    public let day: String
    public let title: String
    public let kind: TaskKind
    public let stat: TaskStat
    public let difficulty: Int
    public init(day: String, title: String, kind: TaskKind, stat: TaskStat, difficulty: Int) {
        self.day = day; self.title = title; self.kind = kind; self.stat = stat; self.difficulty = difficulty
    }
}

public struct TaskStatusRequest: Encodable {
    public let day: String
    public let status: TaskStatus
    public init(day: String, status: TaskStatus) { self.day = day; self.status = status }
}

public struct TaskDayRequest: Encodable {
    public let day: String
    public init(day: String) { self.day = day }
}

public enum TaskDay {
    public static func string(from date: Date, timeZone: TimeZone = .current) -> String {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = timeZone
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: date)
    }
}
