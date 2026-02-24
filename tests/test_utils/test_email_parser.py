"""Tests for email MIME parsing utilities."""

from giva.utils.email_parser import parse_mime_bytes, extract_parts


def _build_simple_email(body: str, content_type: str = "text/plain") -> bytes:
    """Build a simple single-part email."""
    return (
        f"From: sender@example.com\r\n"
        f"To: recipient@example.com\r\n"
        f"Subject: Test\r\n"
        f"Content-Type: {content_type}\r\n"
        f"\r\n"
        f"{body}"
    ).encode()


def _build_multipart_email(
    plain: str = "", html: str = "", attachment_name: str | None = None,
) -> bytes:
    """Build a multipart MIME email."""
    boundary = "boundary123"
    parts = []

    if plain:
        parts.append(
            f"--{boundary}\r\n"
            f"Content-Type: text/plain; charset=utf-8\r\n"
            f"\r\n"
            f"{plain}\r\n"
        )
    if html:
        parts.append(
            f"--{boundary}\r\n"
            f"Content-Type: text/html; charset=utf-8\r\n"
            f"\r\n"
            f"{html}\r\n"
        )
    if attachment_name:
        parts.append(
            f"--{boundary}\r\n"
            f"Content-Type: application/pdf\r\n"
            f'Content-Disposition: attachment; filename="{attachment_name}"\r\n'
            f"\r\n"
            f"fake binary content\r\n"
        )

    body = "".join(parts) + f"--{boundary}--\r\n"
    headers = (
        f"From: sender@example.com\r\n"
        f"To: recipient@example.com\r\n"
        f"Subject: Test\r\n"
        f"Content-Type: multipart/mixed; boundary={boundary}\r\n"
        f"\r\n"
    )
    return (headers + body).encode()


def test_parse_plain_text():
    raw = _build_simple_email("Hello world")
    result = parse_mime_bytes(raw)
    assert result["body_plain"] == "Hello world"
    assert result["body_html"] == ""
    assert result["attachments"] == []


def test_parse_html_only():
    raw = _build_simple_email("<p>Hello</p>", "text/html")
    result = parse_mime_bytes(raw)
    assert result["body_html"] == "<p>Hello</p>"
    assert result["body_plain"] == ""


def test_parse_multipart_plain_and_html():
    raw = _build_multipart_email(plain="Plain text", html="<p>HTML</p>")
    result = parse_mime_bytes(raw)
    assert "Plain text" in result["body_plain"]
    assert "<p>HTML</p>" in result["body_html"]


def test_parse_multipart_with_attachment():
    raw = _build_multipart_email(
        plain="See attached", attachment_name="report.pdf",
    )
    result = parse_mime_bytes(raw)
    assert "See attached" in result["body_plain"]
    assert result["attachments"] == ["report.pdf"]


def test_parse_empty_body():
    raw = _build_simple_email("")
    result = parse_mime_bytes(raw)
    assert result["body_plain"] == ""
    assert result["body_html"] == ""


def test_parse_multipart_plain_only():
    raw = _build_multipart_email(plain="Just plain")
    result = parse_mime_bytes(raw)
    assert "Just plain" in result["body_plain"]
    assert result["body_html"] == ""


def test_parse_multipart_multiple_attachments():
    boundary = "boundary456"
    parts = (
        f"--{boundary}\r\n"
        f"Content-Type: text/plain\r\n\r\nBody\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: application/pdf\r\n"
        f'Content-Disposition: attachment; filename="a.pdf"\r\n\r\nfake\r\n'
        f"--{boundary}\r\n"
        f"Content-Type: image/png\r\n"
        f'Content-Disposition: attachment; filename="b.png"\r\n\r\nfake\r\n'
        f"--{boundary}--\r\n"
    )
    headers = (
        f"From: x@x.com\r\nSubject: Test\r\n"
        f"Content-Type: multipart/mixed; boundary={boundary}\r\n\r\n"
    )
    result = parse_mime_bytes((headers + parts).encode())
    assert sorted(result["attachments"]) == ["a.pdf", "b.png"]
