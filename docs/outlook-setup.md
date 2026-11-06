# Outlook connection / Подключение Outlook

**Updated / Обновлено: 2026-11-06.** [User guide](user-guide.en.md) · [Руководство](user-guide.ru.md)

<a id="english"></a>

## English

**Unreleased source builds only; not included in the published `v0.1.0` ZIP.** Outlook.com and Microsoft 365 cloud mailboxes use Microsoft Graph. You need an application registered by you or your IT administrator. A Client ID identifies that registration; it is not a password. Do not copy another product's Client ID.

### Obtain a Client ID

1. Sign in to the [Microsoft Entra admin center](https://entra.microsoft.com/). Open **App registrations → New registration**, give the app a name, and select **Accounts in any organizational directory and personal Microsoft accounts**. This matches the connector's shared Microsoft sign-in endpoint. Register the app, then copy **Application (client) ID** from its Overview page. Do not use the Object ID. See [Microsoft's registration instructions](https://learn.microsoft.com/en-us/graph/auth-register-app-v2).
2. In **Authentication → Advanced settings**, set **Allow public client flows → Yes**, then save. The device-code flow needs no redirect URI or client secret. Do not create a secret for this desktop connection. See [Microsoft's desktop registration guide](https://learn.microsoft.com/en-us/entra/identity-platform/scenario-desktop-app-registration).
3. In **API permissions → Add a permission → Microsoft Graph → Delegated permissions**, add **Mail.Read** and **User.Read**. The connector also requests **offline_access** to renew its access when permitted. It does not need application permissions, Mail.ReadWrite or Mail.Send. Mail.Read authorizes mailbox reading at Microsoft; Second Brain limits its importer to Inbox. See [Microsoft's permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference) and [device-code flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code).

### Sign in from Second Brain

1. Open **Источники → Добавить источник → Outlook / Microsoft 365**, paste the Client ID into **Client ID приложения**, and choose whether to import Inbox and its period: 30, 90, 365 days or all history.
2. Press **Войти через Microsoft**. Open the displayed Microsoft verification page with **Открыть страницу Microsoft**, enter the displayed code yourself, and review the account and consent screen. Enter your password only on Microsoft's page. Keep the code private; only approve a sign-in you started.
3. Return to Second Brain, verify the connected account, and press **Добавить источник**. Credentials are saved in macOS Keychain after this explicit addition. Start a new sign-in if the code expires.

If app registration, user consent or device-code authentication is blocked by your organization, ask IT to register/approve the app and permitted access. Organization policy and Conditional Access may prohibit this flow; the connector does not bypass them. Removing the source clears its local credentials and imported materials; revoking Microsoft's consent is a separate account/administrator action. See [security and data handling](security.en.md).

<a id="russian"></a>

## Русский

**Только неопубликованная сборка из исходников; в ZIP `v0.1.0` этого подключения нет.** Облачные ящики Outlook.com и Microsoft 365 подключаются через Microsoft Graph. Нужно приложение, зарегистрированное вами или IT-администратором. Client ID обозначает регистрацию, это не пароль. Не используйте Client ID другого продукта.

### Получить Client ID

1. Войдите в [центр администрирования Microsoft Entra](https://entra.microsoft.com/). Откройте **App registrations → New registration**, задайте имя и выберите **Accounts in any organizational directory and personal Microsoft accounts** — рабочие и личные аккаунты. Это соответствует общему адресу входа коннектора. После регистрации скопируйте **Application (client) ID** из Overview. Object ID не подходит. См. [официальную инструкцию регистрации](https://learn.microsoft.com/en-us/graph/auth-register-app-v2).
2. В **Authentication → Advanced settings** установите **Allow public client flows → Yes** и сохраните. Для входа по коду не нужны redirect URI и client secret; секрет для этого подключения создавать не нужно. См. [настройку настольного приложения Microsoft](https://learn.microsoft.com/en-us/entra/identity-platform/scenario-desktop-app-registration).
3. В **API permissions → Add a permission → Microsoft Graph → Delegated permissions** добавьте **Mail.Read** и **User.Read**. Коннектор также запрашивает **offline_access**, чтобы обновлять доступ, когда это разрешено. Права application, Mail.ReadWrite и Mail.Send не нужны. Mail.Read разрешает чтение ящика на стороне Microsoft; импортёр Second Brain обращается только к входящим. См. [справочник прав](https://learn.microsoft.com/en-us/graph/permissions-reference) и [вход по коду](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code).

### Войти через Second Brain

1. Откройте **Источники → Добавить источник → Outlook / Microsoft 365**, вставьте Client ID в **Client ID приложения**, выберите импорт входящих и период: 30, 90, 365 дней или вся история.
2. Нажмите **Войти через Microsoft**, затем **Открыть страницу Microsoft**. Самостоятельно введите показанный код на странице Microsoft, проверьте аккаунт и запрошенные права. Пароль вводится только у Microsoft. Не передавайте код другим и подтверждайте только начатый вами вход.
3. Вернитесь в Second Brain, проверьте подключённый аккаунт и нажмите **Добавить источник**. Секреты сохраняются в Связке ключей macOS после явного добавления. Если код истёк, начните вход заново.

Если организация запрещает регистрацию приложений, пользовательское согласие или вход по коду, попросите IT зарегистрировать/одобрить приложение и права. Политики организации и Conditional Access могут запрещать такой вход; обхода ограничений нет. Удаление источника очищает локальные секреты и импортированные материалы; отзыв согласия Microsoft выполняется отдельно в аккаунте или администратором. См. [безопасность и обработку данных](security.ru.md).
