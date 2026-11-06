import Foundation
import Darwin
import AppKit
import SecondBrainCore

private struct CheckFailure: Error, CustomStringConvertible {
    let description: String
}

private func require(_ condition: @autoclosure () throws -> Bool, _ message: String) throws {
    if try !condition() { throw CheckFailure(description: message) }
}

private func requireThrows(_ operation: () throws -> Void, _ message: String) throws {
    do { try operation() }
    catch { return }
    throw CheckFailure(description: message)
}

/// Framework-free checks for Command Line Tools installations without test SDKs.
@main
enum CoreChecks {
    static func main() async throws {
        let checks: [(String, () throws -> Void)] = [
            ("Price distinguishes unknown, free, invalid values and currency", price),
            ("Sorting uses both prices, preserves input and puts unknown last", sorting),
            ("Settings encode the API contract without persisting secrets", settings),
            ("Corporate API access is explicit, endpoint-scoped and backward compatible", corporateNetworkPermission),
            ("Local Qwen connections require no credentials and preserve cloud requirements", localModelConnection),
            ("Local model execution is distinct from temporary provider throttling", localModelExecution),
            ("Catalog pagination warnings remain optional and explicit", catalogPagination),
            ("Requests stay on the owned loopback origin and carry CSRF", requests),
            ("Source counts describe files or messages instead of imported knowledge", sources),
            ("Source policies preserve legacy defaults and independent permissions", sourcePolicyContract),
            ("Policy exclusions require a current preview and bounded relative paths", sourcePolicyPreview),
            ("Mail providers hide server settings and preserve explicit import scope", mailSources),
            ("Apple Notes require selected folders and retain source compatibility", appleNotesConnector),
            ("Outlook sign-in is scoped, cancellable and contains no raw credentials", outlookConnector),
            ("Notion pages stay scoped, paginated and separate from their token", notionSources),
            ("GitHub repository scope survives filtering and rejects stale token responses", githubSources),
            ("Memory requests encode search safely and bound paid card batches", memoryRequests),
            ("Memory records retain revision and citation evidence", memoryRecords),
            ("Combined import jobs keep nested counters and count file error arrays", importCounters),
            ("Memory browsing pages append uniquely, retry and reject stale responses", memoryBrowsing),
            ("Memory atlas opens only on the trusted local service origin", memoryAtlas),
            ("Sync indicator stays ready during idle, paused automation and row errors", memorySync),
            ("Sync indicator preserves real green pixels in a non-template image", memorySyncImage),
            ("Synchronization pause persists without losing edited automation settings", memoryPause),
            ("Card concurrency accepts positive values, defaults to ten and stays separate from batch size", memoryConcurrency),
            ("Card retry metadata describes the bounded automatic retry budget", memoryRetries),
            ("Provider holds preserve service readiness and configured concurrency", memoryProviderBackoff),
            ("Retry overview distinguishes automatic work from manual intervention", memoryRetryOverview),
            ("Retry-all contract resets only the explicit error queue", memoryRetryAllContract),
            ("Portable launch prefers bundled service and retains developer fallback", backendLaunch),
        ]
        var failures: [String] = []
        for (name, check) in checks {
            do {
                try check()
                print("PASS: \(name)")
            } catch {
                failures.append("\(name): \(error)")
            }
        }
        do {
            try await cancelledLaunch()
            print("PASS: Cancelling startup stops only its synthetic owned helper")
        } catch {
            failures.append("Cancelled startup cleanup: \(error)")
        }
        if !failures.isEmpty {
            throw CheckFailure(description: failures.joined(separator: "\n"))
        }
        print("\(checks.count + 1) core checks passed.")
    }


    private static func mailSources() throws {
        let legacy = try APIClient.decoder.decode(MemorySource.self, from: Data(#"{"id":"old-mail","name":"Old mail","kind":"imap","host":"imap.gmail.com","username":"reader@gmail.com","enabled":true,"credential_saved":true,"status":"not_checked"}"#.utf8))
        try require(legacy.displayKind == "Gmail" && legacy.location == "reader@gmail.com", "Known legacy servers receive readable provider labels")
        try require(!legacy.canImportMemory && legacy.mailImportEnabled == nil, "Legacy mail connections never opt into body imports automatically")
        let source = try APIClient.decoder.decode(MemorySource.self, from: Data(#"{"id":"new-mail","name":"Work","kind":"imap","host":"imap.yandex.ru","username":"reader@yandex.ru","enabled":true,"credential_saved":true,"status":"not_checked","mail_provider":"yandex","mail_import_enabled":true,"mail_lookback_days":90}"#.utf8))
        try require(source.displayKind == "Яндекс Почта" && source.canImportMemory && source.mailLookbackDays == 90, "Mail source describes provider and explicit import scope")
        let request = AddSourceRequest(kind: "imap", name: "Personal", username: "reader@gmail.com", password: "synthetic-" + "app-password", mailProvider: "gmail", mailImportEnabled: true, mailLookbackDays: 30)
        let body = try JSONSerialization.jsonObject(with: APIClient.encoder.encode(request)) as! [String: Any]
        try require(body["mail_provider"] as? String == "gmail" && body["mail_import_enabled"] as? Bool == true && body["mail_lookback_days"] as? Int == 30, "Provider and import window encode as backend fields")
        try require(body["host"] == nil && body["token"] == nil && body["password"] as? String == "synthetic-app-password", "Native users supply an app password rather than server coordinates or API tokens")
        let local = AddSourceRequest(kind: "folder", name: "Notes", path: "/synthetic")
        let localBody = try JSONSerialization.jsonObject(with: APIClient.encoder.encode(local)) as! [String: Any]
        try require(localBody["mail_provider"] == nil && localBody["mail_import_enabled"] == nil, "Unrelated source requests preserve their existing contract")
        let toggle = try JSONSerialization.jsonObject(with: APIClient.encoder.encode(MailImportToggleRequest(enabled: false))) as! [String: Any]
        try require(toggle.count == 1 && toggle["enabled"] as? Bool == false, "Pausing a mailbox changes only its import state, never credentials or period")
        try require(MailImportPeriod.allCases.map(\.rawValue) == [30, 90, 365, 0], "Time windows are bounded named choices with all-history explicit")
        try require(MailProvider.gmail.canSave(email: "reader@workspace.example", password: "abcd efgh " + "ijkl mnop"), "Workspace email and Google grouped app passwords remain usable")
        try require(!MailProvider.gmail.canSave(email: "reader", password: "secret") && !MailProvider.yandex.canSave(email: "reader@yandex.ru", password: ""), "Incomplete credentials cannot be saved")
        try require(!MailProvider.gmail.canSave(email: "reader\n@gmail.com", password: "secret") && !MailProvider.yandex.canSave(email: "reader@yandex.ru", password: "bad\n" + "password"), "Control characters are rejected before saving")
    }

    private static func githubSources() throws {
        let source = try APIClient.decoder.decode(MemorySource.self, from: Data(#"{"id":"github-source","name":"Projects","kind":"github","enabled":true,"credential_saved":true,"status":"not_checked","github_repositories":["synthetic/one"],"github_content":["docs","issues"]}"#.utf8))
        try require(source.location == "Выбрано репозиториев: 1", "GitHub location explains selected repository scope")
        try require(SourceFormatting.count(kind: "github", count: 2) == "2 репозиториев", "GitHub counts repositories")
        try require(SourceFormatting.label("github") == "GitHub", "Native GitHub label")
        let save = AddSourceRequest(kind: "github", name: "Projects", token: "synthetic-" + "test-secret", githubRepositories: ["synthetic/one"], githubContent: ["docs", "issues"])
        let body = try JSONSerialization.jsonObject(with: APIClient.encoder.encode(save)) as! [String: Any]
        try require(body["github_repositories"] as? [String] == ["synthetic/one"] && body["github_content"] as? [String] == ["docs", "issues"], "Only explicitly selected repository and content scope enter the save request")
        try require(body["token"] as? String == "synthetic-test-secret" && body["path"] == nil && body["password"] == nil, "GitHub token stays separate from local paths and mail passwords")
        let discovery = GitHubRepositoriesRequest(token: "synthetic-" + "test-secret", page: 2)
        let discoveryBody = try JSONSerialization.jsonObject(with: APIClient.encoder.encode(discovery)) as! [String: Any]
        try require(discoveryBody["page"] as? Int == 2 && discoveryBody["token"] as? String == "synthetic-test-secret", "Repository discovery uses a POST body, never URL credentials")
        let page = try APIClient.decoder.decode(GitHubRepositoriesResponse.self, from: Data(#"{"repositories":[{"id":1,"full_name":"synthetic/one","description":"First project","private":true,"default_branch":"main","url":"https://github.com/synthetic/one"}],"next_page":2}"#.utf8))
        var picker = GitHubRepositorySelection()
        let first = picker.begin()
        try require(picker.begin() == nil && first?.page == 1, "Only one discovery request may be active")
        try require(picker.accept(page, request: first), "Accept first repository page")
        picker.toggle(999)
        try require(picker.selectedIds.isEmpty, "Only displayed repositories may be selected")
        picker.toggle(1)
        try require(picker.filteredRepositories(query: "missing").isEmpty && picker.selectedNames == ["synthetic/one"], "Local filtering retains the selected scope")
        try require(picker.filteredRepositories(query: "FIRST").count == 1, "Filter matches description without case sensitivity")
        let next = picker.begin(continuation: true)
        try require(next?.page == 2, "Continue numeric server pagination")
        try require(picker.accept(GitHubRepositoriesResponse(repositories: page.repositories, nextPage: nil), request: next), "Accept final page")
        try require(picker.repositories.count == 1 && picker.selectedNames == ["synthetic/one"], "Appending deduplicates repository IDs and preserves selections")
        let stale = picker.begin()
        picker.invalidate()
        let latest = picker.begin()
        try require(!picker.accept(page, request: stale) && !picker.fail(request: stale) && picker.isLoading, "Changed token rejects stale data and errors")
        try require(picker.fail(request: latest) && !picker.isLoading && picker.selectedNames.isEmpty, "Current failure allows retry and old scope is cleared")
        let rows = (1...21).map { ["id": $0, "full_name": "synthetic/repo-\($0)", "private": false, "default_branch": "main", "url": "https://github.com/synthetic/repo-\($0)"] as [String: Any] }
        let many = try APIClient.decoder.decode(GitHubRepositoriesResponse.self, from: JSONSerialization.data(withJSONObject: ["repositories": rows]))
        let limited = picker.begin()
        try require(picker.accept(many, request: limited), "Accept bounded selection choices")
        for repository in picker.repositories { picker.toggle(repository.id) }
        try require(picker.selectedNames.count == 20, "Limit explicit scope to twenty repositories")
        picker.toggle(1)
        try require(picker.selectedNames.count == 19, "Selection remains removable at the limit")
    }

    private static func notionSources() throws {
        let source = try APIClient.decoder.decode(MemorySource.self, from: Data(#"{"id":"notion-source","name":"Notes","kind":"notion","enabled":true,"credential_saved":true,"status":"not_checked","notion_page_ids":["page-1","page-2"]}"#.utf8))
        try require(source.location == "Выбрано страниц: 2", "Notion location explains the exact scope")
        try require(SourceFormatting.count(kind: "notion", count: 2) == "2 страниц", "Notion counts pages")
        try require(SourceFormatting.label("notion") == "Notion", "Native source label")
        let request = AddSourceRequest(kind: "notion", name: "Notes", token: "synthetic-" + "secret", notionPageIds: ["page-1"])
        let body = try JSONSerialization.jsonObject(with: APIClient.encoder.encode(request)) as! [String: Any]
        try require(body["token"] as? String == "synthetic-secret" && body["notion_page_ids"] as? [String] == ["page-1"], "Save token and explicit pages in POST body")
        try require(body["path"] == nil && body["password"] == nil, "Notion does not misuse local folder or mail password fields")
        let page = try APIClient.decoder.decode(NotionPagesResponse.self, from: Data(#"{"pages":[{"id":"page-1","title":"Notes","url":"https://www.notion.so/page-1"}],"next_cursor":"more"}"#.utf8))
        var picker = NotionPageSelection()
        let first = picker.begin()
        try require(picker.begin() == nil, "Only one discovery request runs at a time")
        try require(picker.accept(page, request: first), "Accept first page")
        picker.toggle("not-a-visible-page")
        try require(picker.selectedIds.isEmpty, "Only displayed pages can be selected")
        picker.toggle("page-1")
        let next = picker.begin(continuation: true)
        try require(next?.cursor == "more", "Continue server pagination")
        try require(picker.accept(NotionPagesResponse(pages: page.pages, nextCursor: nil), request: next), "Accept final page")
        try require(picker.pages.count == 1 && picker.selectedIds == ["page-1"], "Deduplicate appended rows without losing selected scope")
        let stale = picker.begin()
        picker.invalidate()
        try require(!picker.accept(page, request: stale) && picker.selectedIds.isEmpty && picker.pages.isEmpty, "A token or query change removes old scope and rejects late data")
        let retry = picker.begin()
        try require(!picker.fail(request: stale) && picker.isLoading, "Stale errors cannot release the latest request")
        try require(picker.fail(request: retry) && !picker.isLoading, "A current error permits retry")
        let limited = picker.begin()
        let rows = (0...100).map { ["id": "page-\($0)", "title": "Synthetic", "url": "https://www.notion.so/synthetic"] }
        let many = try APIClient.decoder.decode(NotionPagesResponse.self, from: JSONSerialization.data(withJSONObject: ["pages": rows]))
        try require(picker.accept(many, request: limited), "Load available choices")
        for row in many.pages { picker.toggle(row.id) }
        try require(picker.selectedIds.count == 100, "User scope has a maximum of one hundred pages")
        picker.toggle("page-1")
        try require(picker.selectedIds.count == 99, "Selected pages remain removable at the limit")
    }

    private static func memoryAtlas() throws {
        let base = URL(string: "http://127.0.0.1:45321")!
        let target = MemoryAtlasLink.url(baseURL: base)
        try require(target?.absoluteString == "http://127.0.0.1:45321/static/memory-atlas.html", "Atlas must use the current service port and local HTML path")
        try require(target?.query == nil && target?.fragment == nil, "Atlas must not expose credentials in its URL")
        let rejected = [
            "https://example.com:45321", "http://localhost:45321",
            "http://127.0.0.1", "http://127.0.0.1:0",
            "http://user:secret@127.0.0.1:45321",
            "http://127.0.0.1:45321/?token=secret",
            "http://127.0.0.1:45321/#secret", "http://127.0.0.1:45321/nested",
        ]
        for value in rejected {
            try require(MemoryAtlasLink.url(baseURL: URL(string: value)!) == nil, "Atlas must reject an unsafe origin")
        }
    }

    private static func syncStatus(jobs: String = "[]", enabled: Bool = true, waiting: Int = 0, schedulerRunning: Bool = true, paused: Bool? = nil) throws -> MemoryStatus {
        let pauseField = paused.map { ",\"paused\":\($0)" } ?? ""
        let body = """
        {"records":10,"cards_pending":4,"cards_ready":3,"cards_error":3,"cards_retry_pending":2,"cards_waiting_user":\(waiting),"jobs":\(jobs),"scheduler_running":\(schedulerRunning),"scheduler":{"enabled":\(enabled),"interval_minutes":15,"process_cards":true,"batch_limit":10,"auto_import_enabled":\(enabled)\(pauseField)}}
        """
        return try APIClient.decoder.decode(MemoryStatus.self, from: Data(body.utf8))
    }

    private static func memorySync() throws {
        let active = try syncStatus(jobs: #"[{"id":"auto","kind":"import","status":"running","phase":"cards","processed":2,"total":10,"stats":{"import":{"imported":1}}}]"#, waiting: 3)
        let indicator = MemorySyncIndicator.resolve(serviceRunning: true, status: active, statusError: nil)
        try require(indicator == .active, "Active work wins over historical exhausted errors")
        try require(active.jobs[0].title == "Создание карточек", "Combined import uses card phase")
        try require(active.jobs[0].progressLabel == "2 из 10 материалов", "Card phase has material units")
        let legacy = try syncStatus(jobs: #"[{"id":"legacy","kind":"import","status":"running","stats":{"import":{"imported":1}}}]"#)
        try require(legacy.jobs[0].isCardPhase, "Legacy combined jobs infer their phase")
        try require(MemorySyncIndicator.resolve(serviceRunning: true, status: try syncStatus(), statusError: nil) == .idle, "Healthy service remains ready while waiting for new material")
        try require(MemorySyncIndicator.resolve(serviceRunning: true, status: try syncStatus(enabled: false), statusError: nil) == .idle, "Paused automation does not stop the ready service")
        try require(MemorySyncIndicator.resolve(serviceRunning: true, status: try syncStatus(waiting: 3), statusError: nil) == .idle, "Old exhausted cards do not change service readiness")
        try require(MemorySyncIndicator.resolve(serviceRunning: true, status: try syncStatus(paused: true), statusError: nil) == .idle, "Paused ingestion leaves the service ready for memory queries")
        try require(MemorySyncIndicator.resolve(serviceRunning: true, status: try syncStatus(schedulerRunning: false), statusError: nil) == .unavailable, "A stopped intake worker cannot report readiness")
        try require(MemorySyncIndicator.resolve(serviceRunning: false, status: active, statusError: nil) == .unavailable, "Stopped helper cannot retain stale green")
        try require(MemorySyncIndicator.resolve(serviceRunning: true, status: active, statusError: "No connection") == .unavailable, "Failed status refresh cannot retain stale green")
        try require(MemorySyncIndicator.resolve(serviceRunning: true, status: nil, statusError: nil) == .checking, "Startup status remains neutral")
    }

    private static func memorySyncImage() throws {
        for indicator: MemorySyncIndicator in [.active, .idle] {
            for darkAppearance in [false, true] {
                let image = MemorySyncSymbol.image(for: indicator, darkAppearance: darkAppearance)
                try require(!image.isTemplate, "Ready and busy menu icons retain their green badge")
                let representation = NSBitmapImageRep(data: image.tiffRepresentation!)!
                var greenPixels = 0
                for x in 0..<representation.pixelsWide {
                    for y in 0..<representation.pixelsHigh {
                        if let color = representation.colorAt(x: x, y: y)?.usingColorSpace(.deviceRGB), color.alphaComponent > 0.5,
                           color.greenComponent > color.redComponent * 1.3, color.greenComponent > color.blueComponent * 1.1 { greenPixels += 1 }
                    }
                }
                try require(greenPixels > 5, "Ready and busy icons contain visible green pixels in both appearances")
            }
        }
        try require(MemorySyncSymbol.image(for: .checking, darkAppearance: false).isTemplate, "Unknown readiness remains neutral")
    }

    private static func memoryRetryOverview() throws {
        let enabled = try syncStatus(waiting: 1)
        try require(enabled.automaticRetryState == .enabled, "Enabled automatic processing is explicit")
        try require(enabled.retryCountsDescription.contains("2") && enabled.retryCountsDescription.contains("1"), "Remaining budgets and manual intervention have separate counts")
        try require(try syncStatus(paused: true).automaticRetryState == .paused, "Global pause wins over enabled settings")
        try require(try syncStatus(enabled: false).automaticRetryState == .disabled, "Disabled reconciliation and auto-import cannot promise retries")
        try require(try syncStatus(schedulerRunning: false).automaticRetryState == .stopped, "Stopped scheduler cannot promise automatic work")
        let raw = Data(#"{"records":3,"cards_pending":0,"cards_ready":0,"cards_error":3,"jobs":[],"scheduler_running":true,"scheduler":{"enabled":false,"auto_import_enabled":true,"process_cards":true,"interval_minutes":15,"batch_limit":10},"provider_backoff":{"blocked":true,"retry_at":null,"requires_user_action":true,"message":"Budget","effective_concurrency":1,"configured_concurrency":10}}"#.utf8)
        let held = try APIClient.decoder.decode(MemoryStatus.self, from: raw)
        try require(held.automaticRetryState == .waitingProvider, "Auto-import alone keeps opted-in card processing enabled, but provider holds block it")
        try require(held.retryCountsDescription.contains("не передал"), "Legacy missing budget counts are not presented as zero")
        let noHold = String(data: raw, encoding: .utf8)!.replacingOccurrences(of: #""blocked":true"#, with: #""blocked":false"#)
        try require(try APIClient.decoder.decode(MemoryStatus.self, from: Data(noHold.utf8)).automaticRetryState == .enabled, "Auto-import alone permits automatic cards")
    }

    private static func memoryRetryAllContract() throws {
        let request = try JSONSerialization.jsonObject(with: APIClient.encoder.encode(MemoryRetryErrorsRequest())) as! [String: Any]
        try require(request.isEmpty, "Retry-all sends no flags that could change automation, provider holds or ready cards")
        for (state, expected) in [("started", "запущена"), ("waiting_provider", "провайдера"), ("paused", "паузе"), ("automation_disabled", "выключены"), ("model_required", "модель"), ("waiting_worker", "текущей операции"), ("empty", "нет"), ("future_state", "Обновите")] {
            let response = try APIClient.decoder.decode(MemoryRetryErrorsResponse.self, from: Data("{\"requeued\":7,\"processing_state\":\"\(state)\",\"job_id\":null}".utf8))
            try require(response.notice.contains(expected), "Retry result must explain the actual processing state")
        }
    }

    private static func memoryProviderBackoff() throws {
        try require(try syncStatus().providerBackoff == nil, "Older helpers need no provider status")
        let heldJSON = #"{"blocked":true,"retry_at":"2026-11-04T12:00:00.000Z","requires_user_action":false,"message":"Провайдер ограничил запросы.","effective_concurrency":1,"configured_concurrency":10}"#
        let held = try APIClient.decoder.decode(MemoryProviderBackoff.self, from: Data(heldJSON.utf8))
        try require(held.blocked && held.retryDate != nil && !held.requiresUserAction, "Decode timed provider hold")
        try require(held.concurrencyLabel == "Сейчас одновременно: до 1 · В настройках: 10",
                    "Adaptive concurrency stays separate from the requested value")
        let resumeBody = try JSONSerialization.jsonObject(with: APIClient.encoder.encode(MemoryProviderResumeRequest())) as! [String: Any]
        try require(resumeBody.isEmpty, "Resume carries no model call or record retry arguments")
        let body = ["provider_backoff": try JSONSerialization.jsonObject(with: Data(heldJSON.utf8))]
        let response = try APIClient.decoder.decode(MemoryProviderResumeResponse.self, from: JSONSerialization.data(withJSONObject: body))
        try require(response.providerBackoff.blocked, "Decode resume response envelope data")
        let statusData = try JSONSerialization.jsonObject(with: Data(#"{"records":1,"cards_pending":1,"cards_ready":0,"cards_error":0,"jobs":[],"scheduler_running":true,"scheduler":{"enabled":true,"interval_minutes":15,"process_cards":true,"batch_limit":10}}"#.utf8)) as! [String: Any]
        let status = try APIClient.decoder.decode(MemoryStatus.self, from: JSONSerialization.data(withJSONObject: statusData.merging(body) { _, new in new }))
        try require(status.providerBackoff?.blocked == true && status.scheduler.request.cardConcurrency == 10,
                    "Provider cooldown leaves configured concurrency intact")
        try require(MemorySyncIndicator.resolve(serviceRunning: true, status: status, statusError: nil) == .idle,
                    "Provider throttling does not turn healthy import and MCP red")
        let balance = try APIClient.decoder.decode(MemoryProviderBackoff.self, from: Data(#"{"blocked":true,"retry_at":null,"requires_user_action":true,"message":"Проверьте баланс.","effective_concurrency":1,"configured_concurrency":1000}"#.utf8))
        try require(balance.requiresUserAction && balance.retryDate == nil && balance.configuredConcurrency == 1000,
                    "Balance holds require a user action and preserve high configured counts")
        let recovered = try APIClient.decoder.decode(MemoryProviderBackoff.self, from: Data(#"{"blocked":false,"retry_at":null,"requires_user_action":false,"message":null,"effective_concurrency":10,"configured_concurrency":10}"#.utf8))
        try require(recovered.concurrencyLabel == nil, "Recovered provider needs no adaptive warning")
    }

    private static func memoryRetries() throws {
        let waiting = try APIClient.decoder.decode(MemoryRecord.self, from: Data(#"{"id":"retry","title":"Retry","card_status":"error","card_attempts":3,"card_retry_at":null,"card_waiting_for_user":true}"#.utf8))
        try require(waiting.cardWaitingForUser == true && waiting.cardAttempts == 3, "Decode exhausted retry metadata")
        try require(waiting.retryStatusText?.contains("Ожидает вашего действия") == true, "Explain manual intervention")
        let scheduled = try APIClient.decoder.decode(MemoryRecord.self, from: Data(#"{"id":"retry","title":"Retry","card_status":"error","card_attempts":1,"card_retry_at":"2026-11-04T12:00:00Z","card_waiting_for_user":false}"#.utf8))
        try require(scheduled.retryStatusText?.contains("Повтор") == true, "Explain scheduled retry")
        let ready = try APIClient.decoder.decode(MemoryRecord.self, from: Data(#"{"id":"ready","title":"Ready","card_status":"ready","card_attempts":2}"#.utf8))
        try require(ready.retryStatusText == nil, "No failure warning for ready cards")
    }

    private static func memoryConcurrency() throws {
        try require(MemoryCardConcurrency.clamped(-1) == 1, "Concurrency minimum")
        try require(MemoryCardConcurrency.clamped(1000) == 1000, "Concurrency has no application ceiling")
        try require(MemoryCardConcurrency.clamped(Int.max) == Int.max, "Representable positive values are preserved")
        try require(MemoryCardConcurrency.defaultValue == 10 && MemoryScheduleRequest().cardConcurrency == 10,
                    "New schedules use ten parallel requests")
        try require(try syncStatus().scheduler.request.cardConcurrency == 10,
                    "Older responses use ten concurrent requests")
        let body = Data(#"{"enabled":true,"interval_minutes":30,"process_cards":true,"batch_limit":10,"auto_import_enabled":true,"paused":false,"card_concurrency":6}"#.utf8)
        let scheduler = try APIClient.decoder.decode(MemoryScheduler.self, from: body)
        let request = scheduler.request
        try require(request.cardConcurrency == 6 && request.batchLimit == 10,
                    "Parallel limit is separate from the total ten-record batch")
        let paused = request.applyingSynchronizationPause(true)
        try require(paused.cardConcurrency == 6 && paused.batchLimit == 10,
                    "Pausing preserves concurrency and batch size")
        let edited = MemoryScheduleRequest(intervalMinutes: 45, batchLimit: 7, cardConcurrency: 5)
        try require(edited.applyingSynchronizationPause(true).cardConcurrency == 5,
                    "Pausing keeps unsaved concurrency edits")
        let encoded = try JSONSerialization.jsonObject(with: APIClient.encoder.encode(paused)) as! [String: Any]
        try require(encoded["card_concurrency"] as? Int == 6,
                    "Schedule API encodes the parallel limit")
        try require(MemoryScheduleRequest(cardConcurrency: 1000).cardConcurrency == 1000,
                    "Constructors preserve the requested parallel count")
        try require(MemoryCardConcurrency.parse(" 1000 ") == 1000,
                    "Numeric input accepts direct large positive values")
        for invalid in ["", "0", "-1", "1.5", "abc", String(Int.max) + "0"] {
            try require(MemoryCardConcurrency.parse(invalid) == nil, "Invalid numeric input stays invalid")
        }
        let high = try APIClient.decoder.decode(MemoryScheduler.self, from: Data(#"{"enabled":true,"interval_minutes":15,"process_cards":false,"batch_limit":10,"card_concurrency":1000}"#.utf8))
        try require(high.request.cardConcurrency == 1000 && high.request.batchLimit == 10,
                    "Responses keep high concurrency without enlarging the batch")
        let highPaused = high.request.applyingSynchronizationPause(true)
        let highEncoded = try JSONSerialization.jsonObject(with: APIClient.encoder.encode(highPaused)) as! [String: Any]
        try require(highPaused.cardConcurrency == 1000 && highEncoded["card_concurrency"] as? Int == 1000,
                    "Pausing and API encoding preserve high concurrency")
        let highJob = try APIClient.decoder.decode(MemoryJob.self, from: Data(#"{"id":"large","kind":"cards","status":"running","concurrency":1000,"in_flight":10}"#.utf8))
        try require(highJob.concurrencyLabel == "Одновременно: 10 из 1000", "Job labels preserve requested concurrency")
        let job = try APIClient.decoder.decode(MemoryJob.self, from: Data(#"{"id":"parallel","kind":"import","phase":"cards","status":"running","processed":2,"total":10,"concurrency":3,"in_flight":3}"#.utf8))
        try require(job.concurrencyLabel == "Одновременно: 3 из 3", "Live parallel activity is visible")
        try require(job.total == 10 && job.progressLabel.contains("2 из 10"),
                    "Parallel activity does not multiply total materials")
        let legacy = try APIClient.decoder.decode(MemoryJob.self, from: Data(#"{"id":"old","kind":"cards","status":"running"}"#.utf8))
        try require(legacy.concurrencyLabel == nil, "Older jobs need no parallel metadata")
    }

    private static func memoryPause() throws {
        let legacy = try syncStatus().scheduler
        try require(legacy.request.paused == false, "Older responses default to unpaused synchronization")
        let scheduler = try syncStatus(paused: true).scheduler
        try require(scheduler.paused == true && scheduler.request.paused, "Persisted pause survives decoding and editing")
        let edited = MemoryScheduleRequest(enabled: true, intervalMinutes: 45, processCards: false,
                                           batchLimit: 7, autoImportEnabled: false)
        let paused = edited.applyingSynchronizationPause(true)
        try require(!edited.paused && paused.paused, "Pause returns a new draft without mutating existing edits")
        try require(paused.enabled == edited.enabled && paused.intervalMinutes == 45,
                    "Pause keeps reconciliation settings")
        try require(paused.processCards == false && paused.batchLimit == 7 && paused.autoImportEnabled == false,
                    "Pause keeps paid-call preference, batch size and source watcher settings")
        let body = try JSONSerialization.jsonObject(with: APIClient.encoder.encode(paused)) as! [String: Any]
        try require(body["paused"] as? Bool == true, "Schedule API encodes the persistent pause flag")
        try require(paused.applyingSynchronizationPause(false) == edited, "Resume restores the same edited automation settings")
    }

    private static func price() throws {
        try require(ModelFormatting.price(nil, currency: "USD") == "Нет данных", "Missing price")
        try require(ModelFormatting.price(0, currency: "USD") == "Бесплатно", "Zero means free")
        try require(ModelFormatting.price(0.05, currency: "USD") == "$0.05", "USD units")
        try require(ModelFormatting.price(1.25, currency: "RUB") == "1,25 ₽", "RUB units")
        try require(ModelFormatting.price(-1, currency: "RUB") == "Нет данных", "Negative price")
        try require(ModelFormatting.price(.nan, currency: "USD") == "Нет данных", "NaN price")
        try require(ModelFormatting.price(.infinity, currency: "USD") == "Нет данных", "Infinite price")
    }

    private static func sorting() throws {
        let models = [
            ModelInfo(id: "unknown", name: "Unknown", inputPrice: nil, outputPrice: nil),
            ModelInfo(id: "b", name: "B", inputPrice: 0.1, outputPrice: 1),
            ModelInfo(id: "free", name: "Free", inputPrice: 0, outputPrice: 0),
            ModelInfo(id: "a", name: "A", inputPrice: 0.1, outputPrice: 0.2),
        ]
        try require(ModelFormatting.sorted(models).map(\.id) == ["free", "a", "b", "unknown"],
                    "Cost ordering")
        try require(models.first?.id == "unknown", "Input must remain unchanged")
    }

    private static func localModelConnection() throws {
        let local = ModelSettings(provider: "ollama", baseUrl: "http://127.0.0.1:11434/v1", modelId: "qwen3:8b")
        try require(local.isLocal && !local.requiresCredential, "Ollama is a token-free local connection")
        try require(local.canUseCredential(tokenSaved: false, tokenDraft: ""), "Local model needs no saved token")
        try require(local.matchesSavedConnection(local, tokenSaved: false, tokenDraft: ""), "Local settings are saved without faking tokenSaved")
        let cloud = ModelSettings(provider: "qwen", baseUrl: "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", modelId: "qwen-flash")
        try require(!cloud.isLocal && cloud.requiresCredential, "Qwen API remains a credentialed cloud provider")
        try require(!cloud.canUseCredential(tokenSaved: false, tokenDraft: ""), "Cloud requires a real credential")
        try require(cloud.canUseCredential(tokenSaved: false, tokenDraft: "synthetic"), "Cloud accepts a draft credential")
        try require(!cloud.matchesSavedConnection(cloud, tokenSaved: false, tokenDraft: ""), "Cloud cannot claim saved without a saved token")
        let provider = try APIClient.decoder.decode(ProviderInfo.self, from: Data(#"{"id":"ollama","label":"Local","base_url":"http://127.0.0.1:11434/v1","description":"Local models","key_url":"","requires_token":false}"#.utf8))
        try require(provider.requiresToken == false && !provider.needsToken, "Snake-case requires_token decodes explicitly, without relying on provider ID fallback")
        let legacy = try APIClient.decoder.decode(ProviderInfo.self, from: Data(#"{"id":"qwen","label":"Qwen API","base_url":"https://dashscope-intl.aliyuncs.com/compatible-mode/v1","description":"Cloud","key_url":""}"#.utf8))
        try require(legacy.needsToken, "Legacy cloud metadata remains credentialed")
        let payload = try JSONSerialization.jsonObject(with: APIClient.encoder.encode(SaveRequest(local, token: nil))) as! [String: Any]
        try require(payload["token"] == nil, "Local save sends no placeholder credential")
    }

    private static func catalogPagination() throws {
        let partial = try APIClient.decoder.decode(CatalogResponse.self, from: Data(#"{"models":[],"fetched_at":"2026-11-05T12:00:00Z","truncated":true,"message":"Показана часть каталога."}"#.utf8))
        try require(partial.truncated == true && partial.message == "Показана часть каталога.", "Partial catalog must carry an explicit nonblocking warning")
        let legacy = try APIClient.decoder.decode(CatalogResponse.self, from: Data(#"{"models":[],"fetched_at":"2026-11-05T12:00:00Z"}"#.utf8))
        try require(legacy.truncated == nil && legacy.message == nil, "Older catalogs decode without pagination fields")
    }

    private static func localModelExecution() throws {
        let local = try APIClient.decoder.decode(MemoryProviderBackoff.self, from: Data(#"{"blocked":false,"retry_at":null,"requires_user_action":false,"message":null,"effective_concurrency":1,"configured_concurrency":10,"execution_mode":"local"}"#.utf8))
        try require(local.isLocalExecution && local.executionTitle == "Локальная обработка карточек", "A local cap is not reported as temporary throttling")
        try require(local.concurrencyLabel != nil, "Local effective concurrency stays visible")
        let cloud = try APIClient.decoder.decode(MemoryProviderBackoff.self, from: Data(#"{"blocked":false,"retry_at":null,"requires_user_action":false,"message":null,"effective_concurrency":4,"configured_concurrency":10}"#.utf8))
        try require(!cloud.isLocalExecution && cloud.executionTitle == "Скорость карточек временно снижена", "Older cloud helpers preserve throttling wording")
    }

    private static func corporateNetworkPermission() throws {
        let legacy = try APIClient.decoder.decode(ModelSettings.self, from: Data(#"{"provider":"custom","base_url":"https://models.example.com/api/v2","model_id":"small","model_name":"Small"}"#.utf8))
        let legacyBody = try JSONSerialization.jsonObject(with: APIClient.encoder.encode(SaveRequest(legacy, token: nil))) as! [String: Any]
        try require(legacyBody["allow_private_network"] as? Bool == false, "Older settings never opt into an internal network")
        let allowed = try APIClient.decoder.decode(ModelSettings.self, from: Data(#"{"provider":"custom","base_url":"https://models.internal/api/v2","model_id":"small","model_name":"Small","allow_private_network":true}"#.utf8))
        try require(!legacy.allowPrivateNetwork && allowed.privateNetworkAccessAllowed, "Only explicit custom settings grant internal access")
        try require(allowed.customEndpointValidationMessage == nil && legacy.customEndpointValidationMessage == nil, "A versioned HTTPS base URL is accepted")
        let denied = allowed.settingPrivateNetworkAccess(false)
        try require(denied.customEndpointValidationMessage != nil, "Internal hostname requires the checkbox")
        try require(allowed.connectionScopeKey != denied.connectionScopeKey, "Policy changes invalidate stale network results")
        try require(!allowed.matchesSavedConnection(denied, tokenSaved: true, tokenDraft: ""), "Changing policy is an unsaved connection change")
        try require(allowed.replacingBaseURL(" https://models.internal/api/v2 ").privateNetworkAccessAllowed, "Whitespace-only URL edits retain the same endpoint permission")
        let moved = allowed.replacingBaseURL("https://other.internal/api/v2")
        try require(!moved.allowPrivateNetwork && allowed.allowPrivateNetwork, "Moving to a different host clears permission without mutating the original")
        try require(!allowed.replacingBaseURL("https://models.internal/api/v3").allowPrivateNetwork, "A different base path requires renewed permission")
        try requireThrows({
            _ = try APIClient.decoder.decode(ModelSettings.self, from: Data(#"{"provider":"custom","base_url":"https://models.internal/api/v2","model_id":"small","model_name":"Small","allow_private_network":"true"}"#.utf8))
        }, "A string cannot silently grant internal access")
        for provider in ["openrouter", "qwen", "ollama"] {
            var named = ModelSettings(provider: provider, allowPrivateNetwork: true)
            try require(!named.allowPrivateNetwork, "Changing to a named provider starts with internal access disabled")
            named.allowPrivateNetwork = true
            let body = try JSONSerialization.jsonObject(with: APIClient.encoder.encode(SaveRequest(named, token: nil))) as! [String: Any]
            try require(body["allow_private_network"] as? Bool == false, "Named provider requests cannot carry custom API permission")
        }
        for suffix in [".internal", ".local", ".lan"] {
            let custom = ModelSettings(provider: "custom", baseUrl: "https://models" + suffix + "/api/v2")
            try require(custom.customEndpointValidationMessage != nil && custom.settingPrivateNetworkAccess(true).customEndpointValidationMessage == nil, "Trusted internal hostname validation follows the explicit policy")
        }
        let numberedDomain = ModelSettings(provider: "custom", baseUrl: "https://127.example.com/api/v2")
        try require(numberedDomain.customEndpointValidationMessage == nil, "A DNS label beginning with 127 is not an IPv4 loopback address")
        let numberedInternal = ModelSettings(provider: "custom", baseUrl: "https://127.models.internal/api/v2", allowPrivateNetwork: true)
        try require(numberedInternal.customEndpointValidationMessage == nil, "A numbered internal hostname remains usable with explicit permission")
        for address in ["http://models.internal/api/v2", "https://localhost/api/v2", "https://service.localhost/api/v2", "https://127.0.0.1/api/v2", "https://[::1]/api/v2", "https://models.internal:8443/api/v2", "https://user@models.internal/api/v2", "https://models.internal/api/v2?extra=yes", "https://models.internal/api/v2#part"] {
            let invalid = ModelSettings(provider: "custom", baseUrl: address, allowPrivateNetwork: true)
            try require(invalid.customEndpointValidationMessage != nil, "Corporate access retains HTTPS, default port and loopback restrictions")
        }
        for data in [try APIClient.encoder.encode(ScopeRequest(allowed)),
                     try APIClient.encoder.encode(ModelRequest(allowed, token: nil)),
                     try APIClient.encoder.encode(ConnectionRequest(allowed, token: nil)),
                     try APIClient.encoder.encode(SaveRequest(allowed, token: nil))] {
            let body = try JSONSerialization.jsonObject(with: data) as! [String: Any]
            try require(body["allow_private_network"] as? Bool == true, "Every custom API operation carries the explicit network policy")
            try require(body["base_url"] as? String == "https://models.internal/api/v2", "The versioned base URL is preserved without appending the completion path")
            try require(body["token"] == nil, "A network permission never supplies a credential")
        }
    }

    private static func settings() throws {
        let body = Data(#"{"provider":"openrouter","base_url":"https://openrouter.ai/api/v1","model_id":"vendor/small","model_name":"Small","token":"untrusted-extra-field"}"#.utf8)
        let settings = try APIClient.decoder.decode(ModelSettings.self, from: body)
        try require(settings.modelId == "vendor/small", "Snake-case model ID")
        let encoded = try APIClient.encoder.encode(settings)
        guard let object = try JSONSerialization.jsonObject(with: encoded) as? [String: Any] else {
            throw CheckFailure(description: "Settings must encode to an object")
        }
        try require(object["base_url"] as? String == "https://openrouter.ai/api/v1", "API URL key")
        try require(object["token"] == nil, "Settings must not encode a secret")
        let source = AddSourceRequest(kind: "imap", name: "Mail", host: "imap.example.com",
                                      username: "test@example.com", password: "test-only-" + "password")
        let sourceBody = try APIClient.encoder.encode(source)
        guard let sourceObject = try JSONSerialization.jsonObject(with: sourceBody) as? [String: Any] else {
            throw CheckFailure(description: "Source must encode to an object")
        }
        try require(sourceObject["port"] as? Int == 993, "IMAP must use TLS port")
        try require(sourceObject["mailbox"] as? String == "INBOX", "IMAP mailbox contract")
    }

    private static func requests() throws {
        guard let base = URL(string: "http://127.0.0.1:45321"),
              let external = URL(string: "https://example.com") else {
            throw CheckFailure(description: "Invalid fixed test URLs")
        }
        let client = APIClient(baseURL: base, csrf: "test-csrf")
        let request = try client.makeRequest("/api/settings", method: "POST", body: Data("{}".utf8))
        try require(request.url?.absoluteString == "http://127.0.0.1:45321/api/settings", "Request origin")
        try require(request.value(forHTTPHeaderField: "Origin") == "http://127.0.0.1:45321", "Origin header")
        try require(request.value(forHTTPHeaderField: "X-Brain-CSRF") == "test-csrf", "CSRF header")
        try require(request.value(forHTTPHeaderField: "Content-Type") == "application/json", "JSON header")
        try requireThrows({
            _ = try client.makeRequest("https://example.com/api/settings", method: "POST")
        }, "External paths must be rejected")
        try requireThrows({
            _ = try client.makeRequest("/api/../secret", method: "GET")
        }, "Parent traversal must be rejected")
        try requireThrows({
            _ = try APIClient(baseURL: external).makeRequest("/api/settings", method: "POST")
        }, "External base URLs must be rejected")
    }

    private static func sources() throws {
        try require(SourceFormatting.count(kind: "codex", count: 12) == "12 файлов", "File count")
        try require(SourceFormatting.count(kind: "imap", count: 0) == "0 писем", "Mail count")
        try require(SourceFormatting.count(kind: "folder", count: nil) == "Не проверено", "Unchecked count")
        try require(SourceFormatting.label("claude") == "Claude Code", "Source display label")
        let qwen = try APIClient.decoder.decode(MemorySource.self, from: Data(#"{"id":"qwen-session-source","name":"Local sessions","kind":"qwen","path":"/synthetic/qwen","enabled":true,"credential_saved":false,"status":"not_checked"}"#.utf8))
        try require(qwen.displayKind == "Qwen Code" && qwen.canImportMemory, "Qwen Code sessions are a named importable source")
        try require(SourceFormatting.symbol("qwen") == "terminal", "Qwen Code uses the session source icon")
        try require(SourceFormatting.count(kind: "qwen", count: 3) == "3 файлов", "Qwen session counts describe source files")
    }

    private static func memoryRequests() throws {
        let client = APIClient(baseURL: URL(string: "http://127.0.0.1:45321")!)
        let text = "C++ & SQLite? model=small"
        let request = try client.makeRequest("/api/memory/search", method: "GET", query: [URLQueryItem(name: "q", value: text), URLQueryItem(name: "limit", value: "8")])
        let components = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)
        try require(components?.queryItems?.first(where: { $0.name == "q" })?.value == text, "Search text must remain one encoded query value")
        let encodedValue = components?.percentEncodedQuery?.components(separatedBy: "&").first(where: { $0.hasPrefix("q=") }).map { String($0.dropFirst(2)) }
        let formDecoded = encodedValue?.replacingOccurrences(of: "+", with: " ").removingPercentEncoding
        try require(formDecoded == text, "Form-style backend parsing must preserve literal plus characters")
        try require(request.url?.host == "127.0.0.1", "Search remains local")
        try require(MemoryProcessingLimit.clamped(-2) == 1, "Paid batch minimum")
        try require(MemoryProcessingLimit.clamped(10) == 10, "Default paid batch")
        try require(MemoryProcessingLimit.clamped(100) == 50, "Paid batch maximum")
    }

    private static func memoryRecords() throws {
        let body = Data(#"{"id":"record-1","revision":2,"title":"Решение","text":"Используем FTS5","source_uri":"file:///fixture/session.jsonl#L2-L3","card_status":"ready","card":{"summary":"Выбран FTS5","atoms":[{"text":"Используем FTS5","kind":"decision","evidence":"Используем FTS5","record_id":"record-1","revision":2,"source_uri":"file:///fixture/session.jsonl#L2-L3"}]}}"#.utf8)
        let record = try APIClient.decoder.decode(MemoryRecord.self, from: body)
        try require(record.revision == 2, "Record revision retained")
        try require(record.card?.atoms?.first?.recordId == "record-1", "Atom cites record")
        try require(record.card?.atoms?.first?.evidence == "Используем FTS5", "Evidence retained")
        let schedule = MemoryScheduleRequest(enabled: true, intervalMinutes: 15, processCards: false, batchLimit: 10, autoImportEnabled: true)
        let object = try JSONSerialization.jsonObject(with: APIClient.encoder.encode(schedule)) as! [String: Any]
        try require(object["process_cards"] as? Bool == false, "Scheduled paid calls require explicit opt-in")
        try require(object["auto_import_enabled"] as? Bool == true, "Local autoimport stays independently configured")
    }

    private static func importCounters() throws {
        let combined = Data(#"{"id":"auto","kind":"import","status":"completed","stats":{"ready":1,"errors":0,"import":{"imported":0,"updated":1,"skipped":2,"errors":[{"file":"a.jsonl"},{"file":"b.jsonl"}]}}}"#.utf8)
        let job = try APIClient.decoder.decode(MemoryJob.self, from: combined)
        try require(job.importCounter("updated") == 1, "Combined jobs use nested import updates")
        try require(job.importCounter("skipped") == 2, "Combined jobs use nested import skips")
        try require(job.importCounter("errors") == 2, "File error arrays are counted")
        try require(job.stats?["ready"]?.integer == 1, "Card counters remain at top level")
        let manual = Data(#"{"id":"manual","kind":"import","status":"completed","stats":{"updated":7,"errors":["a.jsonl","b.jsonl","c.jsonl"]}}"#.utf8)
        let manualJob = try APIClient.decoder.decode(MemoryJob.self, from: manual)
        try require(manualJob.importCounter("updated") == 7, "Manual jobs retain direct import counters")
        try require(manualJob.importCounter("errors") == 3, "Manual file errors are counted")
        let numeric = Data(#"{"id":"numeric","kind":"import","status":"completed","stats":{"errors":4}}"#.utf8)
        let numericJob = try APIClient.decoder.decode(MemoryJob.self, from: numeric)
        try require(numericJob.importCounter("errors") == 4, "Numeric error counters remain supported")
    }

    private static func memoryBrowsing() throws {
        try require(MemoryBrowseFilter.all.rawValue == "all", "All materials filter")
        try require(MemoryBrowseFilter.ready.rawValue == "ready", "Ready cards filter")
        let page = try APIClient.decoder.decode(MemoryBrowseResponse.self, from: Data(#"{"results":[{"id":"one","title":"One"},{"id":"two","title":"Two"},{"id":"two","title":"Duplicate"}],"next_cursor":"cursor-1","has_more":true}"#.utf8))
        var state = MemoryBrowseState()
        let first = state.beginFirstPage()
        try require(state.beginNextPage() == nil, "No overlapping requests")
        try require(try state.accept(page, for: first), "Accept current initial response")
        try require(state.results.map(\.id) == ["one", "two"], "Deduplicate first page")
        let more = state.beginNextPage()!
        try require(more.cursor == "cursor-1", "Forward opaque cursor unchanged")
        try require(state.beginNextPage() == nil, "Deduplicate load-more requests")
        try require(state.fail("Temporary failure", for: more), "Accept current failure")
        try require(state.results.count == 2 && state.hasMore, "Keep content and cursor on failure")
        let retry = state.beginNextPage()!
        let last = try APIClient.decoder.decode(MemoryBrowseResponse.self, from: Data(#"{"results":[{"id":"two","title":"Two updated"},{"id":"three","title":"Three"}],"next_cursor":null,"has_more":false}"#.utf8))
        try require(try state.accept(last, for: retry), "Retry succeeds")
        try require(state.results.map(\.id) == ["one", "two", "three"], "Append unique IDs")
        try require(state.results[1].title == "Two updated", "Refresh overlapping rows")
        try require(!state.hasMore && state.beginNextPage() == nil, "Stop on terminal page")
        let old = state.beginFirstPage()
        let current = state.beginFirstPage()
        try require(!(try state.accept(last, for: old)), "Ignore stale query/filter page")
        try require(!state.fail("Old failure", for: old) && state.isLoading, "Stale failure cannot release current request")
        try require(try state.accept(page, for: current), "Accept latest query")
        let pending = state.beginNextPage()!
        state.invalidate()
        try require(!(try state.accept(last, for: pending)) && state.results.isEmpty, "Invalidate late continuation on filter change")
        let reset = state.beginFirstPage()
        try require(try state.accept(page, for: reset), "Restore initial page")
        let repeated = state.beginNextPage()!
        try requireThrows({ _ = try state.accept(page, for: repeated) }, "Cursor must advance")
        try require(state.fail("Retry", for: repeated), "Malformed page remains retryable")
        try require(state.results.count == 2, "Invalid response never discards loaded rows")
    }

    private static func backendLaunch() throws {
        let resources = URL(fileURLWithPath: "/Applications/Second Brain.app/Contents/Resources", isDirectory: true)
        let helper = resources.appendingPathComponent("Backend/SecondBrainHelper")
        let bundled = try BackendLaunchConfiguration.resolve(
            resourceURL: resources,
            projectDirectory: { throw CheckFailure(description: "Portable app must not resolve a source checkout") },
            port: 54321,
            isExecutable: { $0 == helper }
        )
        try require(bundled.executableURL == helper, "Use the bundled helper without Python or Xcode")
        try require(bundled.arguments == ["serve", "--port", "54321"], "Pass helper arguments without shell interpolation")
        try require(bundled.currentDirectoryURL == nil, "Portable helper does not require a checkout working directory")
        let moved = URL(fileURLWithPath: "/Volumes/Apps/Second Brain.app/Contents/Resources", isDirectory: true)
        let relocated = try BackendLaunchConfiguration.resolve(
            resourceURL: moved,
            projectDirectory: { throw CheckFailure(description: "Moved app must stay self-contained") },
            port: 54322,
            isExecutable: { $0 == moved.appendingPathComponent("Backend/SecondBrainHelper") }
        )
        try require(relocated.executableURL.path.hasPrefix(moved.path), "Moving the app resolves the helper relative to its new bundle")

        let project = URL(fileURLWithPath: "/tmp/synthetic-checkout", isDirectory: true)
        let python = project.appendingPathComponent(".venv/bin/python")
        let development = try BackendLaunchConfiguration.resolve(
            resourceURL: resources, projectDirectory: { project }, port: 54323,
            isExecutable: { $0 == python }
        )
        try require(development.executableURL == python && development.currentDirectoryURL == project, "Developer build retains its existing Python environment")
        try require(development.arguments == ["-m", "uvicorn", "brain_settings.app:create_app", "--factory", "--host", "127.0.0.1", "--port", "54323", "--no-access-log", "--log-level", "warning"], "Developer service retains loopback and bounded logging settings")
        try requireThrows({
            _ = try BackendLaunchConfiguration.resolve(
                resourceURL: nil, projectDirectory: { project }, port: 54324,
                isExecutable: { _ in false }
            )
        }, "A missing service must fail before launching a process")
    }

    @MainActor
    private static func cancelledLaunch() async throws {
        let manager = FileManager.default
        let root = manager.temporaryDirectory.appendingPathComponent("SecondBrainCheck-\(UUID().uuidString)", isDirectory: true)
        let bin = root.appendingPathComponent(".venv/bin", isDirectory: true)
        try manager.createDirectory(at: bin, withIntermediateDirectories: true)
        let helper = bin.appendingPathComponent("python")
        try "#!/bin/sh\nprintf '%s' \"$$\" > \"$PWD/helper.pid\"\nexec /bin/sleep 60\n".write(to: helper, atomically: true, encoding: .utf8)
        try manager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: helper.path)
        let previous = ProcessInfo.processInfo.environment["SECOND_BRAIN_PROJECT_DIR"]
        setenv("SECOND_BRAIN_PROJECT_DIR", root.path, 1)
        defer {
            if let previous { setenv("SECOND_BRAIN_PROJECT_DIR", previous, 1) }
            else { unsetenv("SECOND_BRAIN_PROJECT_DIR") }
            try? manager.removeItem(at: root)
        }
        let service = BackendService()
        let launch = Task { try await service.start() }
        do {
            let pidFile = root.appendingPathComponent("helper.pid")
            var pid: Int32?
            for _ in 0..<80 {
                if let value = try? String(contentsOf: pidFile, encoding: .utf8), let parsed = Int32(value) {
                    pid = parsed
                    break
                }
                try await Task.sleep(nanoseconds: 20_000_000)
            }
            guard let pid else { throw CheckFailure(description: "Synthetic helper did not launch") }
            launch.cancel()
            do {
                _ = try await launch.value
                throw CheckFailure(description: "Cancelled launch unexpectedly succeeded")
            } catch is CancellationError {}
            let stopped = !service.isRunning && service.baseURL == nil
            try require(stopped, "Cancelled startup must reset service state")
            try require(Darwin.kill(pid, 0) != 0, "Cancelled startup must stop its helper PID")
        } catch {
            launch.cancel()
            _ = try? await launch.value
            await service.stop()
            throw error
        }
    }
}
