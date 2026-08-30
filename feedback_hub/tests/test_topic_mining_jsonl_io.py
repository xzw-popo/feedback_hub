import json

import pytest

from feedback_hub.jsonl_io import (
    jsonl_text_records,
    load_jsonl_objects,
)


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
def test_jsonl_parser_preserves_unicode_line_separator_inside_string(separator):
    expected = {"item_id": "f1", "text": f"第一段{separator}第二段"}
    raw = (json.dumps(expected, ensure_ascii=False) + "\n").encode("utf-8")

    assert load_jsonl_objects(raw) == [expected]


def test_jsonl_text_records_split_only_on_lf():
    raw = b'{"value":"a\xe2\x80\xa8b"}\n{"value":"c"}\n'

    assert jsonl_text_records(raw) == (
        '{"value":"a\u2028b"}',
        '{"value":"c"}',
    )


@pytest.mark.parametrize("raw", [b"{broken\n", b"[]\n"])
def test_strict_jsonl_object_loader_rejects_invalid_records(raw):
    with pytest.raises((json.JSONDecodeError, ValueError)):
        load_jsonl_objects(raw)
