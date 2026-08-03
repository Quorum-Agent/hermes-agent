"""Software taxonomy — port of software-taxonomy.test.ts key cases."""

from hermes_routing.software_taxonomy import (
    detect_software_reference,
    is_named_software_follow_up,
)


def test_strong_language():
    assert detect_software_reference("write a typescript function") == "strong"


def test_strong_web_application_terms():
    assert detect_software_reference("deploy a node.js app") == "strong"


def test_contextual_phrase_disambiguates():
    assert detect_software_reference("build a docker image") == "contextual"
    assert detect_software_reference("python script") == "contextual"


def test_contextual_term_alone_not_software():
    # "spring" alone (as a season) must not route to coding.
    assert detect_software_reference("run in the spring") is None


def test_no_software():
    assert detect_software_reference("what is the weather like") is None


def test_named_software_follow_up():
    assert is_named_software_follow_up("what about typescript?") is True


def test_named_software_follow_up_shell_as_bowl_is_not():
    assert is_named_software_follow_up("use the shell as a bowl") is False
