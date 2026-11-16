import Foundation
import SecondBrainCore

private struct TaskCheckFailure: Error { let message: String }
private func taskRequire(_ condition: @autoclosure () -> Bool, _ message: String) throws {
    if !condition() { throw TaskCheckFailure(message: message) }
}

extension CoreChecks {
    static func taskContract() throws {
        let body = #"{"day":"2026-11-16","tasks":[{"id":"synthetic-daily","title":"Прочитать главу","kind":"daily","stat":"intelligence","difficulty":2,"status":"done","xp":25}],"player":{"xp":125,"level":2,"rank":"E","level_xp":50,"next_level_xp":200,"stats":{"strength":0,"agility":1,"intelligence":5,"vitality":7,"perception":2}}}"#
        let board = try APIClient.decoder.decode(TaskBoard.self, from: Data(body.utf8))
        try taskRequire(board.day == "2026-11-16" && board.tasks.count == 1, "Decode the complete board")
        let card = board.tasks[0]
        try taskRequire(card.id == "synthetic-daily" && card.title == "Прочитать главу" && card.kind == .daily && card.stat == .intelligence && card.difficulty == 2 && card.status == .done && card.xp == 25, "Task fields retain server values")
        let player = board.player
        try taskRequire(player.xp == 125 && player.level == 2 && player.rank == "E" && player.levelXp == 50 && player.nextLevelXp == 200 && player.levelProgress == 0.5, "Read snake-case thresholds without computing levels on the client")
        try taskRequire(TaskStat.allCases.map { player.stats[$0] } == [0, 1, 5, 7, 2], "All five stats retain their server values")

        func object<Body: Encodable>(_ body: Body) throws -> NSDictionary {
            try JSONSerialization.jsonObject(with: APIClient.encoder.encode(body)) as! NSDictionary
        }
        let create = try object(TaskCreateRequest(day: board.day, title: card.title, kind: .daily, stat: .intelligence, difficulty: 2))
        try taskRequire(create == ["day": "2026-11-16", "title": "Прочитать главу", "kind": "daily", "stat": "intelligence", "difficulty": 2] as NSDictionary, "Creation sends only the five required fields")
        for status in TaskStatus.allCases {
            let move = try object(TaskStatusRequest(day: board.day, status: status))
            try taskRequire(move == ["day": board.day, "status": status.rawValue] as NSDictionary, "Status changes carry the explicit local day")
        }
        let deletion = try object(TaskDayRequest(day: board.day))
        try taskRequire(deletion == ["day": board.day] as NSDictionary, "Deletion carries day in a JSON body")
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:8765")!, csrf: "synthetic")
        let request = try client.makeRequest("/api/tasks/synthetic-daily", method: "DELETE", body: APIClient.encoder.encode(TaskDayRequest(day: board.day)))
        try taskRequire(request.httpMethod == "DELETE" && request.httpBody != nil && request.value(forHTTPHeaderField: "X-Brain-CSRF") == "synthetic", "Task deletion uses the existing CSRF client")
    }

    static func taskLocalDay() throws {
        let formatter = ISO8601DateFormatter()
        let east = formatter.date(from: "2026-11-16T22:30:00Z")!
        let west = formatter.date(from: "2026-11-16T01:30:00Z")!
        let tokyo = TimeZone(identifier: "Asia/Tokyo")!
        let losAngeles = TimeZone(identifier: "America/Los_Angeles")!
        try taskRequire(TaskDay.string(from: east, timeZone: tokyo) == "2026-11-17", "Tokyo uses the next local day near UTC midnight")
        try taskRequire(TaskDay.string(from: east, timeZone: losAngeles) == "2026-11-16", "Los Angeles retains its local day")
        try taskRequire(TaskDay.string(from: west, timeZone: losAngeles) == "2026-11-15", "Los Angeles uses the previous local day near UTC midnight")
        try taskRequire(TaskDay.string(from: west, timeZone: tokyo) == "2026-11-16", "Tokyo uses Gregorian POSIX dates")
    }
}
