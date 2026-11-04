"""Provider presets for app-password mail connections; no account/network operations."""

import re

from .errors import AppError
from .mail import validate_imap_host, validate_mail_password, validate_mail_username

MAIL_HOSTS = {"gmail": "imap.gmail.com", "yandex": "imap.yandex.ru"}


def mail_connection(provider: str, host: str | None, username: str | None,
                    password: str) -> tuple[str, str, str]:
    if provider == "custom":
        return (validate_imap_host(host), validate_mail_username(username),
                validate_mail_password(password))
    expected = MAIL_HOSTS.get(provider)
    if expected is None:
        raise AppError("invalid_mail_provider", "Выберите Gmail или Яндекс Почту.")
    if host is not None and validate_imap_host(host) != expected:
        raise AppError("invalid_mail_host", "Сервер выбранной почты настраивается автоматически.")
    account = validate_mail_username(username).lower()
    if not re.fullmatch(r"[A-Za-z0-9.!#$&'+/=?^_`|~\-]+@[A-Za-z0-9](?:[A-Za-z0-9.\-]*"
                        r"[A-Za-z0-9])?\.[A-Za-z]{2,63}", account):
        raise AppError("invalid_mail_email",
                       "Введите полный адрес почты, например name@example.com.")
    secret = password.replace(" ", "") if provider == "gmail" else password
    secret = validate_mail_password(secret)
    if provider == "gmail" and len(secret) != 16:
        raise AppError("invalid_mail_password", "Введите пароль приложения Google из 16 символов. "
                       "Обычный пароль аккаунта не подходит.")
    return expected, account, secret


def mail_access_error(provider: str, error: AppError) -> AppError:
    if error.code != "mail_auth_failed":
        return error
    if provider == "gmail":
        return AppError(error.code, "Gmail отклонил вход. Используйте пароль приложения Google, "
                        "созданный после включения двухэтапной аутентификации. "
                        "В рабочем аккаунте доступ может ограничивать администратор.", 401)
    if provider == "yandex":
        return AppError(error.code, "Яндекс Почта отклонила вход. Создайте пароль приложения "
                        "типа «Почта» и разрешите IMAP и пароли приложений "
                        "в настройках почты.", 401)
    return error
