import Foundation

public enum MemoryAutomaticRetryState: Equatable {
    case enabled, paused, disabled, waitingProvider, stopped

    public var title: String {
        switch self {
        case .enabled: return "Автоповторы включены"
        case .paused: return "Автоповторы на паузе"
        case .disabled: return "Автоповторы выключены"
        case .waitingProvider: return "Автоповторы ожидают провайдера"
        case .stopped: return "Автоповторы недоступны"
        }
    }

    public var explanation: String {
        switch self {
        case .enabled: return "Карточки с оставшимися попытками повторяются автоматически."
        case .paused: return "Возобновите синхронизацию для продолжения автоматической обработки."
        case .disabled: return "Включите автокарточки в настройках автоматизации для обработки очереди."
        case .waitingProvider: return "Очередь продолжится после снятия паузы провайдера."
        case .stopped: return "Проверьте состояние локального сервиса перед автоматической обработкой."
        }
    }
}

public extension MemoryStatus {
    var automaticRetryState: MemoryAutomaticRetryState {
        if scheduler.paused == true { return .paused }
        guard scheduler.processCards && (scheduler.enabled || (scheduler.autoImportEnabled ?? true)) else { return .disabled }
        guard schedulerRunning else { return .stopped }
        if providerBackoff?.blocked == true { return .waitingProvider }
        return .enabled
    }

    var retryCountsDescription: String {
        guard let retrying = cardsRetryPending, let waiting = cardsWaitingUser else {
            return "Ошибок: \(max(cardsError, 0)). Сервер не передал сведения об оставшихся попытках."
        }
        return "Ещё могут повториться: \(max(retrying, 0)) · Ждут вашего действия: \(max(waiting, 0))"
    }
}

public struct MemoryRetryErrorsRequest: Encodable { public init() {} }

public struct MemoryRetryErrorsResponse: Decodable {
    public let requeued: Int
    public let processingState: String
    public let jobId: String?

    public var notice: String {
        let prefix = "Возвращено в очередь: \(max(requeued, 0)). "
        switch processingState {
        case "started": return prefix + "Обработка запущена; следующие партии продолжатся автоматически."
        case "waiting_provider": return prefix + "Обработка ожидает снятия паузы провайдера."
        case "paused": return prefix + "Синхронизация на паузе. Возобновите её для обработки очереди."
        case "automation_disabled": return prefix + "Автокарточки выключены. Включите их в настройках автоматизации."
        case "model_required": return prefix + "Выберите и сохраните модель для создания карточек."
        case "waiting_worker": return prefix + "Обработка продолжится после текущей операции."
        case "empty": return "Карточек с ошибками для повтора сейчас нет."
        default: return prefix + "Обновите состояние памяти, чтобы проверить обработку."
        }
    }
}
