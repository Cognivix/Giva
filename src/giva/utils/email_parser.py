"""Email MIME parsing utilities."""

from __future__ import annotations

import email
from email.parser import BytesParser
from email.policy import default as default_policy


def parse_mime_bytes(raw: bytes) -> dict:
    """Parse raw MIME bytes into a structured dict."""
    msg = BytesParser(policy=default_policy).parsebytes(raw)
    return extract_parts(msg)


def extract_parts(msg: email.message.Message) -> dict:
    """Extract text and HTML parts from an email message."""
    plain = ""
    html = ""
    attachments = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in disposition:
                filename = part.get_filename()
                if filename:
                    attachments.append(filename)
                continue
            if content_type == "text/plain" and not plain:
                plain = part.get_content()
            elif content_type == "text/html" and not html:
                html = part.get_content()
    else:
        content_type = msg.get_content_type()
        if content_type == "text/plain":
            plain = msg.get_content()
        elif content_type == "text/html":
            html = msg.get_content()

    return {
        "body_plain": plain or "",
        "body_html": html or "",
        "attachments": attachments,
    }
