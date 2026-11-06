# Security policy / Политика безопасности

[English security guide](docs/security.en.md) · [Безопасность на русском](docs/security.ru.md)

## Supported versions

Version **0.1.0** is the initial public release. Security fixes target the current `0.1.x` development line on a best-effort basis. There is no long-term support commitment, response-time SLA, security certification, or guarantee that every vulnerability has been found. Use the latest reviewed release and its locked dependencies.

Second Brain is a personal macOS application with a local service and a shared MCP memory. Its security model assumes a trusted operating-system account. It is not designed to be a public network service or a multi-user hosted application.

## Report a vulnerability privately

Use the repository's **Security → Report a vulnerability** action when it is available. Follow [GitHub's private reporting instructions](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/report-privately).

If that action is unavailable, open an issue asking the maintainer for a private security contact. Include **no vulnerability details, exploit steps, credentials, personal records, or private repository/mail content** in that public request. Wait for a private channel before sharing the report.

In a private report, include the affected version, operating system, impact, and a minimal reproduction using synthetic data. Mention any proposed fix. Never send production credentials or a copy of your memory database. Coordinate disclosure with the maintainer while a report is investigated and a fix is prepared.

If a credential has been exposed, revoke it at its provider and replace it. Removing it from a Git commit, issue, screenshot, or local database does not revoke it.

**Maintainer release requirement:** enable [private vulnerability reporting](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/configure-vulnerability-reporting/configure-for-a-repository) before public launch, or publish a working private contact here. Repository creation and these account settings are separate from preparing the source tree. See the [release checklist](docs/releasing.md).

## Release artifacts and private state

The portable release embeds a Python interpreter, reviewed application files and
runtime dependencies. It must not include a developer's database, source
connections, local preferences, session archives, credentials, environment,
logs, backups or diagnostic reports. The source archive and binary ZIP are
separate artifacts; both need inspection. See [artifact checks](docs/releasing.md#privacy-and-artifact-checks).

Release data is created per user in `~/Library/Application Support/Second Brain`;
source builds use checkout-local `.data`. Credentials remain in macOS Keychain.
The portable package does not automatically migrate a source-build archive.

For optional card generation, cloud Qwen API uses a region-matched API key in
Keychain. The named local Ollama connection needs no model token and connects
only to its fixed loopback service, using downloaded models without cloud
fallback. Ollama and model weights are installed separately. Custom provider
endpoints use public HTTPS by default; published `v0.1.0` rejects private
addresses. **Unreleased source builds** add a custom-API-only, disabled-by-default
internal-network permission for a trusted corporate endpoint. It resets when the
address or provider changes, retains HTTPS/443 and verified TLS, and keeps
loopback, link-local and known cloud metadata endpoints blocked. See the
[network policy](docs/security.en.md#outbound-connections-and-source-access).
The local server and OS must be trusted. Remote-source imports and MCP-client
processing have separate data flows.

**Unreleased source builds** also add Apple Notes through explicit folder selection
and macOS Automation, and Outlook through Microsoft device-code sign-in with
user-registered Client ID and delegated read permissions. Outlook credentials
are stored in Keychain; no client secret is required. Neither connector is in
the published `v0.1.0` ZIP. See [Outlook setup](docs/outlook-setup.md#english) and
[source access boundaries](docs/security.en.md#outbound-connections-and-source-access).

The current downloadable package is ad-hoc signed and **not Developer ID signed
or notarized by Apple**. Scanning for known secret patterns and personal paths is
not a security certification and cannot prove that every sensitive detail was
found. Do not disable operating-system protections to run an untrusted download.

## Версии и область применения

**0.1.0** — первый публичный выпуск. Исправления безопасности предназначены для текущей ветки `0.1.x` по мере возможностей сопровождения. Долгосрочная поддержка, сроки ответа, сертификация и обнаружение всех возможных уязвимостей не гарантируются. Используйте последний проверенный выпуск и зафиксированные зависимости.

Second Brain рассчитан на личный Mac и доверенную учётную запись ОС. Локальный сервис и общая MCP-память не предназначены для публикации в интернете или использования как многопользовательский хостинг.

## Как сообщить об уязвимости

Если доступен пункт **Security → Report a vulnerability**, отправьте приватный отчёт через него. Если пункта нет, создайте issue с просьбой предоставить приватный контакт. **Не публикуйте в таком issue описание уязвимости, шаги эксплуатации, ключи, личные записи или содержимое почты и закрытых репозиториев.** Дождитесь приватного канала.

В приватном отчёте укажите версию, ОС, возможные последствия и минимальное воспроизведение на искусственных данных. Не отправляйте действующие секреты и копию своей базы памяти. Согласуйте раскрытие деталей с сопровождающим проекта.

При утечке ключа отзовите его у провайдера и создайте новый: удаление из Git, issue или базы не делает прежний ключ недействительным.

**Перед публикацией** сопровождающий должен включить приватные отчёты GitHub либо указать здесь действующий приватный контакт. Эти настройки не включаются автоматически при подготовке исходников. Подробности: [безопасность и ограничения](docs/security.ru.md), [подготовка выпуска](docs/releasing.md).

## Артефакты выпуска и личные данные

В готовый ZIP включаются интерпретатор Python, проверенные файлы приложения и
зависимости runtime. Личная база, подключения источников, локальные настройки,
сессии, секреты, окружение разработчика, журналы, резервные копии и отчёты проверки
не должны попадать в выпуск. Архив исходников и архив приложения проверяются
отдельно, вместе с метаданными и историей публикуемого Git.

Готовый выпуск создаёт данные пользователя в
`~/Library/Application Support/Second Brain`; сборка из исходников использует
проектную `.data`. Секреты остаются в Связке ключей. Автоматического переноса
проектного архива нет. Облачная Qwen API использует региональный API-ключ в
Keychain; локальное подключение Ollama не требует токена модели, обращается
только к фиксированному loopback-сервису и загруженным моделям без перехода в
облако. Ollama и веса устанавливаются отдельно. Собственный API по умолчанию
использует публичный HTTPS; опубликованная `v0.1.0` запрещает приватные адреса.
**В неопубликованной сборке из исходников** добавлено выключенное по умолчанию
разрешение внутренней сети только для доверенного собственного API. Оно
сбрасывается при смене адреса или провайдера, сохраняет HTTPS/443 и проверку TLS;
loopback, link-local и известные адреса метаданных облачных машин блокируются.
Подробности — в [сетевой политике](docs/security.ru.md).
Локальный сервер и ОС должны быть доверенными. Импорт удалённых источников и
обработка MCP-клиентом — отдельные потоки данных.

**В неопубликованной сборке из исходников** также добавлены Apple Заметки с
явным выбором папок и разрешением «Автоматизация», а также Outlook со входом
Microsoft по коду, собственным Client ID и делегированными правами чтения.
Секреты Outlook хранятся в Keychain; client secret не нужен. В ZIP `v0.1.0`
этих источников нет. См. [настройку Outlook](docs/outlook-setup.md#russian) и
[границы доступа](docs/security.ru.md).

Проверка известных шаблонов не гарантирует обнаружение
всех чувствительных сведений. Текущая ad-hoc подпись не является Developer ID или
нотарификацией Apple. Не отключайте защиту ОС ради недоверенного файла.
