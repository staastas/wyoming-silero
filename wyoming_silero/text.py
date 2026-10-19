"""Text preparation: number normalization, SSML helpers and sentence splitting."""
import logging
import re
from typing import List, Optional

from num2words import CONVERTER_CLASSES, num2words

_LOGGER = logging.getLogger(__name__)

TIME_PATTERN = re.compile(r'(\d{1,2}):(\d{2})')

# A number is only matched when it is not glued to a word character or to
# another number group. This keeps "A-1" from reading as a negative number and
# leaves dotted groups such as IP addresses and versions ("192.168.1.1") alone,
# while still matching "-3.5" and the European "1,5".
NUMBER_PATTERN = re.compile(r'(?<![\w.,])-?\d+(?:[.,]\d+)?(?!\d|[.,]\d)')

# Used to normalize only the text nodes of an SSML document, never the markup.
TAG_PATTERN = re.compile(r'<[^>]*>')

# A sentence ends with terminal punctuation (plus closing quotes or brackets)
# followed by whitespace, or with a line break. Requiring the whitespace keeps
# "3.5" and "example.com" in one piece, and in streaming mode it means a chunk
# ending in "3." is not cut before the "5" arrives.
SENTENCE_END_PATTERN = re.compile(r'[.!?…]+[»"”’)\]]*\s+|\n+')

# Places where an over-long sentence can be cut without breaking a word.
CLAUSE_END_PATTERN = re.compile(r'[,;:]\s|\s[—–-]\s')

# Silero refuses to synthesize very long inputs, so text is fed to it in
# pieces no longer than this.
DEFAULT_MAX_CHARS = 500

# Fragments shorter than this ("Да.", "т. е.") are merged with the following
# sentence instead of being synthesized on their own.
DEFAULT_MIN_CHARS = 20

# Slavic languages inflect the unit noun, and some units are feminine, which
# also changes the numeral itself ("одна минута", not "один минута").
_SLAVIC_TIME_FORMS = {
    'ru': {
        'hour': (('час', 'часа', 'часов'), None),
        'minute': (('минута', 'минуты', 'минут'), 'f'),
    },
    'uk': {
        'hour': (('година', 'години', 'годин'), 'f'),
        'minute': (('хвилина', 'хвилини', 'хвилин'), 'f'),
    },
}

# Languages that pluralize the unit noun. Each entry is
# (singular, plural, gender of the noun).
_PLURAL_TIME_FORMS = {
    'en': {
        'hour': ('hour', 'hours', None),
        'minute': ('minute', 'minutes', None),
    },
    'es': {
        'hour': ('hora', 'horas', 'f'),
        'minute': ('minuto', 'minutos', 'm'),
    },
    'fr': {
        'hour': ('heure', 'heures', 'f'),
        'minute': ('minute', 'minutes', 'f'),
    },
}

# Numerals that change shape in front of a unit noun, as
# (last digit, form produced by num2words, form required here).
_NUMERAL_FORMS = {
    'ru': {'f': ((1, 'один', 'одна'), (2, 'два', 'две'))},
    'uk': {'f': ((1, 'один', 'одна'), (2, 'два', 'дві'))},
    'es': {
        'f': ((1, 'veintiuno', 'veintiuna'), (1, 'uno', 'una')),
        'm': ((1, 'veintiuno', 'veintiún'), (1, 'uno', 'un')),
    },
    'fr': {'f': ((1, 'un', 'une'),)},
    'de': {None: ((1, 'eins', 'ein'),)},
}

# Languages already reported as unsupported by num2words, so the warning is
# logged once instead of once per number.
_UNSUPPORTED_LANGS = set()


def supports_number_normalization(lang: Optional[str]) -> bool:
    """Tell whether num2words can spell numbers in this language."""
    return bool(lang) and lang in CONVERTER_CLASSES


def _get_slavic_declension(number: int, one: str, two: str, five: str) -> str:
    """Get the proper declension for Slavic numbers."""
    if number % 10 == 1 and number % 100 != 11:
        return one
    elif 2 <= number % 10 <= 4 and (number % 100 < 10 or number % 100 >= 20):
        return two
    else:
        return five


def _apply_numeral_form(word: str, number: int, lang: str, gender) -> str:
    """Adapt a numeral to the gender the unit noun requires."""
    for digit, source, target in _NUMERAL_FORMS.get(lang, {}).get(gender, ()):
        # The teens keep the plain form: "одиннадцать минут", not "одна...".
        if number % 10 == digit and number % 100 != 10 + digit and word.endswith(source):
            return word[: -len(source)] + target
    return word


def format_time(hours: int, minutes: int, lang: str) -> str:
    """
    Format a time of day in the given language.

    Raises ValueError when the language has no rules, so the caller can fall
    back instead of emitting English unit names inside a non-English sentence.
    """
    if lang in _SLAVIC_TIME_FORMS:
        forms = _SLAVIC_TIME_FORMS[lang]

        hour_forms, hour_gender = forms['hour']
        hours_word = _apply_numeral_form(num2words(hours, lang=lang), hours, lang, hour_gender)
        hours_text = f"{hours_word} {_get_slavic_declension(hours, *hour_forms)}"

        if minutes == 0:
            return hours_text

        minute_forms, minute_gender = forms['minute']
        minutes_word = _apply_numeral_form(num2words(minutes, lang=lang), minutes, lang, minute_gender)
        minutes_text = f"{minutes_word} {_get_slavic_declension(minutes, *minute_forms)}"
        return f"{hours_text} {minutes_text}"

    if lang == 'de':
        # German reads 8:45 as "acht Uhr fünfundvierzig".
        hours_word = _apply_numeral_form(num2words(hours, lang='de'), hours, 'de', None)
        if minutes == 0:
            return f"{hours_word} Uhr"
        return f"{hours_word} Uhr {num2words(minutes, lang='de')}"

    if lang in _PLURAL_TIME_FORMS:
        forms = _PLURAL_TIME_FORMS[lang]

        hour_one, hour_many, hour_gender = forms['hour']
        hours_word = _apply_numeral_form(num2words(hours, lang=lang), hours, lang, hour_gender)
        hours_text = f"{hours_word} {hour_one if hours == 1 else hour_many}"

        if minutes == 0:
            return hours_text

        minute_one, minute_many, minute_gender = forms['minute']
        minutes_word = _apply_numeral_form(num2words(minutes, lang=lang), minutes, lang, minute_gender)
        minutes_text = f"{minutes_word} {minute_one if minutes == 1 else minute_many}"
        return f"{hours_text} {minutes_text}"

    raise ValueError(f"No time formatting rules for language '{lang}'")


def escape_ssml_text(text: str) -> str:
    """Escape characters that would otherwise make the SSML document invalid."""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def strip_ssml(text: str) -> str:
    """Drop the markup of an SSML document and keep its text nodes."""
    text = TAG_PATTERN.sub('', text)
    return text.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')


def _normalize_text_node(text: str, lang: str) -> str:
    """Convert times and numbers inside a single run of plain text."""
    if not text:
        return text

    def replace_time(match):
        """Replace time format with natural language."""
        hours = int(match.group(1))
        minutes = int(match.group(2))
        if hours > 23 or minutes > 59:
            # Not a time of day (a ratio or a score), leave it to the
            # number pass.
            return match.group(0)
        try:
            return format_time(hours, minutes, lang)
        except Exception as e:
            _LOGGER.debug("Failed to format time '%s': %s. Using fallback.", match.group(0), e)
            return f"{match.group(1)} {match.group(2)}"

    text = TIME_PATTERN.sub(replace_time, text)

    def replace_number(match):
        """Replace a number with its word representation."""
        number_str = match.group(0)
        try:
            # Normalize comma to period for parsing (European to US notation)
            normalized_str = number_str.replace(',', '.')

            # Convert string to number (int or float)
            if '.' in normalized_str:
                number = float(normalized_str)
            else:
                number = int(normalized_str)

            return num2words(number, lang=lang)
        except Exception as e:
            _LOGGER.warning("⚠️  Failed to convert number '%s' to words: %s. Using original.", number_str, e)
            return number_str

    # Replace all numbers in the text
    return NUMBER_PATTERN.sub(replace_number, text)


def normalize_numbers(text: str, lang: str, is_ssml: bool = False) -> str:
    """Convert digits in text to words using num2words."""
    if not supports_number_normalization(lang):
        if lang not in _UNSUPPORTED_LANGS:
            _UNSUPPORTED_LANGS.add(lang)
            _LOGGER.warning("⚠️  num2words does not support language '%s'; numbers will not be spelled out", lang)
        return text

    original_text = text

    if is_ssml:
        # Rewrite text nodes only; touching markup would corrupt attributes
        # such as <break time="500ms"/>.
        pieces = []
        position = 0
        for tag in TAG_PATTERN.finditer(text):
            pieces.append(_normalize_text_node(text[position:tag.start()], lang))
            pieces.append(tag.group(0))
            position = tag.end()
        pieces.append(_normalize_text_node(text[position:], lang))
        normalized_text = ''.join(pieces)
    else:
        normalized_text = _normalize_text_node(text, lang)

    if original_text != normalized_text:
        _LOGGER.debug("📊 Normalized text: '%s' -> '%s'", original_text, normalized_text)

    return normalized_text


def split_long(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> List[str]:
    """Cut a sentence into pieces no longer than max_chars, preferring clause boundaries."""
    pieces = []
    text = text.strip()
    while len(text) > max_chars:
        window = text[:max_chars]
        cut = None
        for match in CLAUSE_END_PATTERN.finditer(window):
            cut = match.start() + 1
        if cut is None:
            space = window.rfind(' ')
            cut = space if space > 0 else max_chars
        pieces.append(text[:cut].strip())
        text = text[cut:].strip()
    if text:
        pieces.append(text)
    return pieces


class SentenceSplitter:
    """
    Split text into sentences as it arrives.

    Feed text with add() as many times as needed, then call finish() to get
    whatever is left. Every returned piece is at most max_chars long.
    """

    def __init__(self, max_chars: int = DEFAULT_MAX_CHARS, min_chars: int = DEFAULT_MIN_CHARS) -> None:
        self.max_chars = max_chars
        self.min_chars = min(min_chars, max_chars)
        # Complete sentences held back because they are still too short.
        self._pending = ''
        # Text after the last sentence boundary.
        self._buffer = ''

    def add(self, text: str) -> List[str]:
        """Add text and return the sentences it completed."""
        self._buffer += text
        sentences: List[str] = []

        position = 0
        for match in SENTENCE_END_PATTERN.finditer(self._buffer):
            self._pending += self._buffer[position:match.end()]
            position = match.end()
            if len(self._pending.strip()) >= self.min_chars:
                sentences.extend(split_long(self._pending, self.max_chars))
                self._pending = ''
        self._buffer = self._buffer[position:]

        # Text without any full stop cannot be held back forever: once it is
        # too long, emit all but the tail and keep waiting for the rest.
        combined = self._pending + self._buffer
        if len(combined.strip()) > self.max_chars:
            pieces = split_long(combined, self.max_chars)
            sentences.extend(pieces[:-1])
            self._pending = ''
            # Keep the trailing whitespace, so the next chunk is not glued
            # to the last word.
            self._buffer = pieces[-1] + combined[len(combined.rstrip()):]

        return sentences

    def finish(self) -> List[str]:
        """Return the remaining text and reset the splitter."""
        rest = self._pending + self._buffer
        self._pending = ''
        self._buffer = ''
        return split_long(rest, self.max_chars)


def split_sentences(text: str, max_chars: int = DEFAULT_MAX_CHARS, min_chars: int = DEFAULT_MIN_CHARS) -> List[str]:
    """Split a complete text into sentences."""
    splitter = SentenceSplitter(max_chars, min_chars)
    return splitter.add(text) + splitter.finish()


_BREAK_STRENGTH_MS = {
    'none': 0,
    'x-weak': 100,
    'weak': 250,
    'medium': 500,
    'strong': 1000,
    'x-strong': 2000,
}

_BREAK_TIME_PATTERN = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*(ms|s)\s*$', re.IGNORECASE)


def parse_break_ms(break_time: Optional[str], break_strength: Optional[str]) -> int:
    """
    Turn the global break settings into a pause length in milliseconds.

    break_time wins over break_strength, like in SSML. Raises ValueError on
    a value that cannot be understood.
    """
    if break_time:
        match = _BREAK_TIME_PATTERN.match(break_time)
        if not match:
            raise ValueError(f"Invalid break time '{break_time}', expected e.g. '500ms' or '1s'")
        value = float(match.group(1))
        return int(value if match.group(2).lower() == 'ms' else value * 1000)

    if break_strength:
        key = break_strength.strip().lower()
        if key not in _BREAK_STRENGTH_MS:
            raise ValueError(
                f"Invalid break strength '{break_strength}', expected one of {', '.join(_BREAK_STRENGTH_MS)}"
            )
        return _BREAK_STRENGTH_MS[key]

    return 0
