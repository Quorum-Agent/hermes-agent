"""Text-placement safety — Python port of safe-text.test.ts."""

from hermes_routing.safe_text import safe_display_text


def test_removes_c0_c1_controls_that_break_markup():
    assert safe_display_text("load\u0000 failed\u001b[31m\u009d now") == (
        "load failed [31m now"
    )


def test_removes_format_characters_that_let_text_reorder_itself():
    # A right-to-left override reverses everything after it.
    assert safe_display_text("error \u202ednuof ton elif\u202c here") == (
        "error dnuof ton elif here"
    )


def test_collapses_whitespace_into_one_line():
    assert safe_display_text("first\n\n   second\tthird") == "first second third"


def test_bounds_length_at_callers_limit():
    assert safe_display_text("x" * 50, 10) == "x" * 10
    assert len(safe_display_text("x" * 500)) == 240
