import Foundation
import AppKit

public enum MemorySyncIndicator: Equatable {
    case active, idle, paused, waiting, unavailable, checking

    public static func resolve(serviceRunning: Bool, status: MemoryStatus?, statusError: String?) -> Self {
        guard serviceRunning, statusError == nil else { return .unavailable }
        guard let status else { return .checking }
        if status.busy { return .active }
        guard status.schedulerRunning else { return .unavailable }
        return .idle
    }

    public var isReady: Bool { self == .active || self == .idle }

    public var title: String {
        switch self {
        case .active: return "Синхронизация активна"
        case .idle: return "Сервис запущен, готов к работе"
        case .paused: return "Автоматическая синхронизация приостановлена"
        case .waiting: return "Есть карточки, ожидающие вашего действия"
        case .unavailable: return "Нет связи с локальным сервисом"
        case .checking: return "Проверяем состояние синхронизации"
        }
    }

    public var symbol: String {
        switch self {
        case .active: return "arrow.triangle.2.circlepath"
        case .idle, .checking: return "square.stack.3d.up.fill"
        case .paused: return "pause.circle"
        case .waiting, .unavailable: return "exclamationmark.circle"
        }
    }
}

public enum MemorySyncSymbol {
    public static func image(for indicator: MemorySyncIndicator, darkAppearance: Bool) -> NSImage {
        let symbol = NSImage(systemSymbolName: indicator.symbol, accessibilityDescription: indicator.title)
            ?? NSImage(size: NSSize(width: 18, height: 18))
        guard indicator.isReady || indicator == .waiting || indicator == .unavailable else {
            symbol.isTemplate = true
            return symbol
        }
        let size = NSSize(width: 22, height: 18)
        let image = NSImage(size: size, flipped: false) { _ in
            let bounds = NSRect(x: 1, y: 1, width: 16, height: 16)
            symbol.draw(in: bounds)
            (darkAppearance ? NSColor.white : NSColor.black).setFill()
            bounds.fill(using: .sourceAtop)
            let badge = NSRect(x: 14, y: 0, width: 7, height: 7)
            (darkAppearance ? NSColor.black : NSColor.white).setFill()
            NSBezierPath(ovalIn: badge.insetBy(dx: -0.75, dy: -0.75)).fill()
            (indicator.isReady ? NSColor.systemGreen : NSColor.systemOrange).setFill()
            NSBezierPath(ovalIn: badge).fill()
            return true
        }
        image.isTemplate = false
        image.accessibilityDescription = indicator.title
        return image
    }
}

extension MemoryRecord {
    public var retryStatusText: String? {
        guard cardStatus == "error", let attempts = cardAttempts else { return nil }
        if cardWaitingForUser == true {
            return "Ожидает вашего действия. Автоматические попытки исчерпаны: первая обработка и два повтора. Ручной повтор не возобновляет автоматические попытки."
        }
        let next = min(max(attempts, 1), 2)
        return "Повтор \(next) из 2 ожидает запуска. После двух автоматических повторов обработка остановится до вашего действия."
    }

    public var retryDate: Date? {
        guard let value = cardRetryAt else { return nil }
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = parser.date(from: value) { return date }
        parser.formatOptions = [.withInternetDateTime]
        return parser.date(from: value)
    }
}
