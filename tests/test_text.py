import pytest

from wyoming_silero.text import (
    SentenceSplitter,
    normalize_numbers,
    parse_break_ms,
    split_long,
    split_sentences,
    strip_ssml,
)


class TestNormalizeNumbers:
    def test_russian_time(self):
        assert normalize_numbers("в 21:01", "ru") == "в двадцать один час одна минута"

    def test_russian_number(self):
        assert normalize_numbers("температура 5 градусов", "ru") == "температура пять градусов"

    def test_decimal_with_comma(self):
        assert normalize_numbers("1,5", "ru") == "одна целая пять десятых"

    def test_ip_address_is_left_alone(self):
        assert normalize_numbers("192.168.1.1", "en") == "192.168.1.1"

    def test_ssml_attributes_are_not_touched(self):
        ssml = '<speak>Wait <break time="500ms"/> 3 seconds</speak>'
        assert normalize_numbers(ssml, "en", is_ssml=True) == \
            '<speak>Wait <break time="500ms"/> three seconds</speak>'

    def test_unsupported_language_keeps_digits(self):
        assert normalize_numbers("5", "multi") == "5"

    def test_belarusian(self):
        assert normalize_numbers("5", "be") != "5"


class TestSplitting:
    def test_sentences(self):
        text = "Включаю свет в гостиной. Температура двадцать градусов! Что-нибудь ещё?"
        assert split_sentences(text) == [
            "Включаю свет в гостиной.",
            "Температура двадцать градусов!",
            "Что-нибудь ещё?",
        ]

    def test_short_fragments_are_merged(self):
        assert split_sentences("Да. Конечно, сейчас включу свет.") == ["Да. Конечно, сейчас включу свет."]

    def test_decimal_point_does_not_split(self):
        assert split_sentences("Сейчас на улице 3.5 градуса тепла.") == ["Сейчас на улице 3.5 градуса тепла."]

    def test_long_text_is_cut_at_clauses(self):
        sentence = ", ".join(["очень длинный кусок текста"] * 40) + "."
        pieces = split_long(sentence, 100)
        assert all(len(piece) <= 100 for piece in pieces)
        assert " ".join(pieces) == sentence

    def test_word_without_spaces_is_hard_cut(self):
        assert split_long("a" * 250, 100) == ["a" * 100, "a" * 100, "a" * 50]

    def test_streaming_matches_whole_text(self):
        text = "Первое предложение готово. Второе предложение тоже готово. А это хвост"
        splitter = SentenceSplitter()
        streamed = []
        for i in range(0, len(text), 7):
            streamed.extend(splitter.add(text[i:i + 7]))
        streamed.extend(splitter.finish())
        assert streamed == split_sentences(text)

    def test_streaming_emits_before_finish(self):
        splitter = SentenceSplitter()
        assert splitter.add("Первое предложение готово.") == []
        assert splitter.add(" Второе") == ["Первое предложение готово."]
        assert splitter.finish() == ["Второе"]

    def test_streaming_does_not_split_number_across_chunks(self):
        splitter = SentenceSplitter(min_chars=1)
        assert splitter.add("Ровно 3.") == []
        assert splitter.add("5 градуса.") == []
        assert splitter.finish() == ["Ровно 3.5 градуса."]

    def test_streaming_long_text_without_stops(self):
        splitter = SentenceSplitter(max_chars=60)
        out = []
        for word in ["слово"] * 40:
            out.extend(splitter.add(word + " "))
        out.extend(splitter.finish())
        assert all(len(piece) <= 60 for piece in out)
        assert " ".join(out) == " ".join(["слово"] * 40)


def test_strip_ssml():
    assert strip_ssml("<speak>A &amp; B <break/>C</speak>") == "A & B C"


@pytest.mark.parametrize("time_, strength, expected", [
    (None, None, 0),
    ("500ms", None, 500),
    ("1.5s", None, 1500),
    (None, "strong", 1000),
    ("200ms", "x-strong", 200),
])
def test_parse_break_ms(time_, strength, expected):
    assert parse_break_ms(time_, strength) == expected


@pytest.mark.parametrize("time_, strength", [("fast", None), (None, "loud")])
def test_parse_break_ms_invalid(time_, strength):
    with pytest.raises(ValueError):
        parse_break_ms(time_, strength)
