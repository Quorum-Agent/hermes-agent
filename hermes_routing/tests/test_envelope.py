"""Public-answer extraction — port of the envelope contract behavior."""

import pytest

from hermes_routing.envelope import (
    UnsafeOutput,
    extract_public_answer,
    strip_reasoning_fields,
)


def test_json_schema_answer():
    assert extract_public_answer('{"answer": "hello"}') == "hello"


def test_envelope_answer():
    assert extract_public_answer("<quorum-final>hello</quorum-final>") == "hello"


def test_envelope_with_outer_whitespace():
    assert extract_public_answer("  <quorum-final>hello</quorum-final>\n") == "hello"


def test_rejects_text_outside_envelope():
    with pytest.raises(UnsafeOutput, match="outside the envelope"):
        extract_public_answer("Sure! Here: <quorum-final>hi</quorum-final>")


def test_rejects_nested_tags():
    # A nested full tag makes the open/close count 2, caught by the count guard
    # before the (defensively unreachable) Nested text check.
    with pytest.raises(UnsafeOutput, match="Expected exactly one envelope"):
        extract_public_answer(
            "<quorum-final>hi <quorum-final>again</quorum-final></quorum-final>"
        )


def test_rejects_missing_close():
    with pytest.raises(UnsafeOutput, match="Expected exactly one envelope"):
        extract_public_answer("<quorum-final>hello")


def test_rejects_visually_blank_body():
    with pytest.raises(UnsafeOutput, match="visually blank"):
        extract_public_answer("<quorum-final>  \u200b  </quorum-final>")


def test_rejects_json_non_answer_shape_then_wrong_envelope():
    with pytest.raises(UnsafeOutput):
        extract_public_answer('{"foo": "bar"}')


def test_json_answer_visually_blank():
    with pytest.raises(UnsafeOutput, match="visually blank"):
        extract_public_answer('{"answer": "   "}')


def test_strip_reasoning_fields():
    chunk = {"content": "x", "thinking": "private", "reasoning_content": "also"}
    assert strip_reasoning_fields(chunk) == {"content": "x"}


def test_rejects_close_before_open():
    with pytest.raises(UnsafeOutput, match="Close tag precedes"):
        extract_public_answer("</quorum-final>hello<quorum-final>")
