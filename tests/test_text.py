from opencode_buddy.text_width import clip_text_by_width, display_width


def test_display_width_counts_cjk_as_double_width():
    assert display_width("abc") == 3
    assert display_width("你好") == 4
    assert display_width("e\u0301") == 1


def test_clip_text_preserves_ascii_behavior():
    assert clip_text_by_width("  alpha   beta  ", 10) == "alpha beta"
    assert clip_text_by_width("alpha beta gamma", 10) == "alpha b..."


def test_clip_text_uses_display_width_for_cjk_and_mixed_content():
    assert clip_text_by_width("你好世界abc", 7) == "你好..."
    assert clip_text_by_width("ab你好cd", 7) == "ab你..."
