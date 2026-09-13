from opencode_buddy.text_width import clip_text_by_width


def test_clip_text_by_width_counts_cjk_as_double_width():
    assert clip_text_by_width("你" * 25, 43, ellipsis="...") == ("你" * 20) + "..."


def test_clip_text_by_width_handles_mixed_ascii_and_cjk():
    assert clip_text_by_width("AB你CD界EF", 8, ellipsis="...") == "AB你C..."


def test_clip_text_by_width_preserves_ascii_compaction_and_truncation():
    assert clip_text_by_width("hello   world", 20, ellipsis="...") == "hello world"
    assert clip_text_by_width("abcdefghij", 7, ellipsis="...") == "abcd..."
