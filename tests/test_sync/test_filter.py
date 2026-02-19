"""Tests for the email filter response parser."""

from giva.sync.mail import _parse_filter_response


def test_parse_valid_response():
    response = '[{"i":0,"v":"KEEP"},{"i":1,"v":"SKIP"},{"i":2,"v":"KEEP"}]'
    result = _parse_filter_response(response, 3)
    assert result == [True, False, True]


def test_parse_with_markdown_fences():
    response = '```json\n[{"i":0,"v":"KEEP"},{"i":1,"v":"SKIP"}]\n```'
    result = _parse_filter_response(response, 2)
    assert result == [True, False]


def test_parse_with_extra_text():
    response = 'Here is my classification:\n[{"i":0,"v":"SKIP"},{"i":1,"v":"KEEP"}]\nDone.'
    result = _parse_filter_response(response, 2)
    assert result == [False, True]


def test_parse_full_key_names():
    response = '[{"index":0,"verdict":"KEEP"},{"index":1,"verdict":"SKIP"}]'
    result = _parse_filter_response(response, 2)
    assert result == [True, False]


def test_parse_invalid_json_defaults_to_keep():
    response = "I can't parse this email"
    result = _parse_filter_response(response, 3)
    assert result == [True, True, True]


def test_parse_empty_response_defaults_to_keep():
    response = ""
    result = _parse_filter_response(response, 2)
    assert result == [True, True]


def test_parse_partial_verdicts_fills_with_keep():
    # Only 2 verdicts for 4 emails — missing ones default to KEEP
    response = '[{"i":0,"v":"SKIP"},{"i":2,"v":"SKIP"}]'
    result = _parse_filter_response(response, 4)
    assert result == [False, True, False, True]


def test_parse_out_of_range_index_ignored():
    response = '[{"i":0,"v":"KEEP"},{"i":99,"v":"SKIP"}]'
    result = _parse_filter_response(response, 2)
    assert result == [True, True]  # index 99 is out of range, ignored


def test_parse_case_insensitive():
    response = '[{"i":0,"v":"skip"},{"i":1,"v":"keep"}]'
    result = _parse_filter_response(response, 2)
    assert result == [False, True]
