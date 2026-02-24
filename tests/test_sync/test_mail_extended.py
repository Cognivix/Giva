"""Extended tests for mail sync: helper functions, classification, and sync pipeline."""

from datetime import datetime
from unittest.mock import patch, MagicMock

from giva.sync.mail import (
    _extract_name,
    _mailbox_accessor,
    _make_message_id,
    _parse_date,
    _parse_filter_response,
    sync_mail_jxa,
)


class TestMakeMessageId:

    def test_deterministic(self):
        id1 = _make_message_id("alice@ex.com", "Hello", "2026-01-01")
        id2 = _make_message_id("alice@ex.com", "Hello", "2026-01-01")
        assert id1 == id2

    def test_different_inputs_different_ids(self):
        id1 = _make_message_id("alice@ex.com", "Hello", "2026-01-01")
        id2 = _make_message_id("bob@ex.com", "Hello", "2026-01-01")
        assert id1 != id2

    def test_returns_32_char_hex(self):
        result = _make_message_id("a", "b", "c")
        assert len(result) == 32
        assert all(c in "0123456789abcdef" for c in result)


class TestParseDate:

    def test_iso_format(self):
        result = _parse_date("2026-03-15T10:30:00")
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 15

    def test_iso_with_z(self):
        result = _parse_date("2026-03-15T10:30:00Z")
        assert result.year == 2026

    def test_empty_returns_now(self):
        result = _parse_date("")
        assert (datetime.now() - result).total_seconds() < 5

    def test_invalid_returns_now(self):
        result = _parse_date("not-a-date")
        assert (datetime.now() - result).total_seconds() < 5


class TestExtractName:

    def test_name_angle_brackets(self):
        assert _extract_name("Alice Smith <alice@example.com>") == "Alice Smith"

    def test_name_with_quotes(self):
        assert _extract_name('"Alice Smith" <alice@example.com>') == "Alice Smith"

    def test_plain_email(self):
        assert _extract_name("alice@example.com") == ""

    def test_empty(self):
        assert _extract_name("") == ""


class TestMailboxAccessor:

    def test_builtin_inbox(self):
        assert _mailbox_accessor("INBOX") == "mail.inbox"

    def test_builtin_sent(self):
        assert _mailbox_accessor("Sent") == "mail.sentMailbox"

    def test_builtin_drafts(self):
        assert _mailbox_accessor("Drafts") == "mail.drafts"

    def test_builtin_trash(self):
        assert _mailbox_accessor("Trash") == "mail.trash"

    def test_builtin_junk(self):
        assert _mailbox_accessor("Junk") == "mail.junkMailbox"

    def test_case_insensitive(self):
        assert _mailbox_accessor("inbox") == "mail.inbox"
        assert _mailbox_accessor("SENT") == "mail.sentMailbox"

    def test_custom_mailbox(self):
        assert _mailbox_accessor("Archive") == 'mail.mailboxes.byName("Archive")'

    def test_custom_with_spaces(self):
        assert _mailbox_accessor("My Folder") == 'mail.mailboxes.byName("My Folder")'


class TestParseFilterResponse:

    def test_valid_skip_response(self):
        response = '[{"i": 0, "v": "KEEP"}, {"i": 1, "v": "SKIP"}, {"i": 2, "v": "KEEP"}]'
        result = _parse_filter_response(response, 3)
        assert result == [True, False, True]

    def test_all_keep(self):
        response = '[{"i": 0, "v": "KEEP"}, {"i": 1, "v": "KEEP"}]'
        result = _parse_filter_response(response, 2)
        assert result == [True, True]

    def test_all_skip(self):
        response = '[{"i": 0, "v": "SKIP"}, {"i": 1, "v": "SKIP"}]'
        result = _parse_filter_response(response, 2)
        assert result == [False, False]

    def test_no_json_defaults_to_keep(self):
        result = _parse_filter_response("I cannot classify these emails.", 3)
        assert result == [True, True, True]

    def test_invalid_json_defaults_to_keep(self):
        result = _parse_filter_response("[{invalid json}]", 2)
        assert result == [True, True]

    def test_index_out_of_range_ignored(self):
        response = '[{"i": 0, "v": "SKIP"}, {"i": 99, "v": "SKIP"}]'
        result = _parse_filter_response(response, 2)
        assert result == [False, True]  # Only index 0 is affected

    def test_alternative_key_names(self):
        """Supports 'index'/'verdict' as well as 'i'/'v'."""
        response = '[{"index": 0, "verdict": "SKIP"}]'
        result = _parse_filter_response(response, 2)
        assert result == [False, True]

    def test_case_insensitive_skip(self):
        response = '[{"i": 0, "v": "skip"}]'
        result = _parse_filter_response(response, 2)
        assert result == [False, True]

    def test_json_in_markdown_fence(self):
        response = '```json\n[{"i": 0, "v": "SKIP"}]\n```'
        result = _parse_filter_response(response, 2)
        assert result == [False, True]


class TestSyncMailJxa:

    @patch("giva.sync.mail._fetch_headers_chunk")
    def test_sync_without_filter(self, mock_fetch, tmp_db):
        """Without config, all emails are kept (no LLM filtering)."""
        # Return data on first call, empty on second to stop the chunk loop
        mock_fetch.side_effect = [
            [
                {
                    "messageId": "msg-1@example.com",
                    "subject": "Hello",
                    "sender": "Alice <alice@example.com>",
                    "date": "2026-03-01T10:00:00Z",
                    "toAddrs": ["bob@example.com"],
                    "ccAddrs": [],
                    "isRead": True,
                    "isFlagged": False,
                },
            ],
            [],  # Second chunk returns empty → loop stops
        ]

        synced, filtered = sync_mail_jxa(tmp_db, ["INBOX"], config=None)
        assert synced == 1
        assert filtered == 0

    @patch("giva.sync.mail._fetch_headers_chunk")
    def test_sync_with_empty_mailbox(self, mock_fetch, tmp_db):
        mock_fetch.return_value = []
        synced, filtered = sync_mail_jxa(tmp_db, ["INBOX"], config=None)
        assert synced == 0
        assert filtered == 0

    @patch("giva.sync.mail._fetch_headers_chunk")
    def test_sync_error_in_mailbox_continues(self, mock_fetch, tmp_db):
        """Error in one mailbox shouldn't stop others."""
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call (INBOX first chunk) → error
                raise RuntimeError("INBOX failed")
            if call_count == 2:
                # Second call (Sent first chunk) → data
                return [
                    {
                        "messageId": "msg-1@ex.com",
                        "subject": "Hello",
                        "sender": "a@a.com",
                        "date": "2026-01-01T00:00:00Z",
                        "toAddrs": [],
                        "ccAddrs": [],
                        "isRead": False,
                        "isFlagged": False,
                    },
                ]
            # Third call onwards (Sent second chunk) → empty to stop loop
            return []

        mock_fetch.side_effect = side_effect
        synced, filtered = sync_mail_jxa(tmp_db, ["INBOX", "Sent"], config=None)
        # INBOX fails, Sent succeeds
        assert synced >= 1
