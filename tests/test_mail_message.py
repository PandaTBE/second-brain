"""Synthetic RFC822 parsing; attachments and remote content never become memory."""

from email.message import EmailMessage

import pytest

from brain_settings.errors import AppError
from brain_settings.mail_message import parse_message


def message(text="SQLite decision from synthetic email."):
    mail = EmailMessage()
    mail["Subject"] = "Решение по SQLite"
    mail["From"] = "Synthetic <reader@example.com>"
    mail["To"] = "Owner <owner@example.com>"
    mail["Date"] = "Wed, 04 Nov 2026 12:00:00 +0300"
    mail["Message-ID"] = "<synthetic-1@example.com>"
    mail.set_content(text)
    return mail


def test_plain_email_preserves_headers_and_date():
    result = parse_message(message().as_bytes())
    assert result["title"] == "Решение по SQLite"
    assert "reader@example.com" in result["text"]
    assert "SQLite decision" in result["text"]
    assert result["timestamp"] == "2026-11-04T09:00:00+00:00"
    assert result["message_id"] == "<synthetic-1@example.com>"


def test_plain_alternative_preferred_and_attachments_not_extracted():
    mail = message("Primary plain text")
    mail.add_alternative("<p>Secondary HTML</p>", subtype="html")
    mail.add_attachment(b"PRIVATE ATTACHMENT CONTENT", maintype="text", subtype="plain",
                        filename="private.txt")
    text = parse_message(mail.as_bytes())["text"]
    assert "Primary plain text" in text
    assert "Secondary HTML" not in text
    assert "PRIVATE ATTACHMENT" not in text


def test_html_removes_scripts_styles_and_remote_images():
    mail = message()
    mail.set_content("<style>private-style</style><script>private-script</script>"
                     "<p>Hello &amp; readable</p><img src='https://tracking.invalid/pixel'>",
                     subtype="html")
    text = parse_message(mail.as_bytes())["text"]
    assert "Hello & readable" in text
    assert "private-script" not in text and "private-style" not in text
    assert "tracking.invalid" not in text


def test_inline_text_attachments_and_nested_message_are_not_body():
    mail = message("Real body")
    mail.add_attachment("INLINE PRIVATE", subtype="plain", filename="private.txt",
                        disposition="inline", cid="<private>")
    mail.add_attachment(message("FORWARDED ATTACHMENT"))
    result = parse_message(mail.as_bytes())
    assert "Real body" in result["text"]
    assert "INLINE PRIVATE" not in result["text"]
    assert "FORWARDED ATTACHMENT" not in result["text"]


def test_missing_subject_and_invalid_date_have_safe_fallbacks():
    mail = message()
    del mail["Subject"]
    mail.replace_header("Date", "invalid")
    result = parse_message(mail.as_bytes())
    assert result["title"] == "Без темы"
    assert result["timestamp"] is None


@pytest.mark.parametrize("raw", [b"x" * (1_048_576 + 1), b"x\0secret"])
def test_oversize_or_binary_input_rejected(raw):
    with pytest.raises(AppError):
        parse_message(raw)


def test_excessive_mime_parts_are_explicit_error():
    mail = message()
    for index in range(101):
        mail.add_attachment(str(index), subtype="plain", filename=f"{index}.txt")
    with pytest.raises(AppError):
        parse_message(mail.as_bytes())


def test_related_html_root_with_content_id_is_real_body():
    mail = message()
    mail.set_content("<p>Readable related root</p>", subtype="html")
    mail["Content-ID"] = "<body@example.test>"
    mail.make_related()
    mail.set_param("start", "<body@example.test>")
    mail.add_related(b"IMAGE PRIVATE", maintype="image", subtype="png", cid="<tracking>")
    text = parse_message(mail.as_bytes())["text"]
    assert "Readable related root" in text
    assert "IMAGE PRIVATE" not in text


@pytest.mark.parametrize("disposition", ["inline", "attachment"])
def test_related_inline_root_filename_is_body_but_explicit_attachment_is_not(disposition):
    mail = message()
    mail.set_content("<p>Actual related body</p>", subtype="html")
    mail["Content-ID"] = "<body@example.test>"
    mail.add_header("Content-Disposition", disposition, filename="body.html")
    mail.make_related()
    mail.set_param("start", "<body@example.test>")
    if disposition == "attachment":
        with pytest.raises(AppError):
            parse_message(mail.as_bytes())
    else:
        assert "Actual related body" in parse_message(mail.as_bytes())["text"]


def test_related_without_start_uses_first_child_not_cidless_attachment():
    mail = message()
    mail.set_content("<p>Default first HTML root</p>", subtype="html")
    mail["Content-ID"] = "<body@example.test>"
    mail.make_related()
    mail.add_related("PRIVATE INLINE ATTACHMENT", subtype="plain", filename="private.txt",
                     disposition="inline")
    text = parse_message(mail.as_bytes())["text"]
    assert "Default first HTML root" in text
    assert "PRIVATE INLINE ATTACHMENT" not in text


def test_related_missing_explicit_start_is_not_guessed():
    mail = message()
    mail.set_content("<p>Body</p>", subtype="html")
    mail.make_related()
    mail.set_param("start", "<missing@example.test>")
    with pytest.raises(AppError):
        parse_message(mail.as_bytes())
