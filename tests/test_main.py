import pytest

from wyoming_silero.__main__ import describe_voice, resolve_voice_language


@pytest.mark.parametrize("name, lang, expected", [
    ("kseniya", "ru", "Kseniya (ru)"),
    ("bel_larisa", "be", "Larisa (be)"),
    ("en_12", "en", "en_12 (en)"),
    ("v3_speaker", "ru", "V3 Speaker (ru)"),
])
def test_describe_voice(name, lang, expected):
    assert describe_voice(name, lang) == expected


def test_voice_language_from_prefix():
    assert resolve_voice_language("ua_mykyta", {}, "ru") == "uk"


def test_voice_language_from_models_yml():
    assert resolve_voice_language("aidar", {"speakers": {"aidar": {"lang": "ru"}}}, "multi") == "ru"


def test_voice_language_override():
    assert resolve_voice_language("bel_larisa", {}, "ru", "ru") == "ru"
