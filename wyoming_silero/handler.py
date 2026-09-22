import asyncio
import inspect
import logging
import math
import re
import time
from typing import Any, Optional

import torch
from num2words import num2words

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.error import Error
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.server import AsyncEventHandler
from wyoming.tts import Synthesize, SynthesizeTextFormat

_LOGGER = logging.getLogger(__name__)

TIME_PATTERN = re.compile(r'(\d{1,2}):(\d{2})')

# A number is only matched when it is not glued to a word character or to
# another number group. This keeps "A-1" from reading as a negative number and
# leaves dotted groups such as IP addresses and versions ("192.168.1.1") alone,
# while still matching "-3.5" and the European "1,5".
NUMBER_PATTERN = re.compile(r'(?<![\w.,])-?\d+(?:[.,]\d+)?(?!\d|[.,]\d)')

# Used to normalize only the text nodes of an SSML document, never the markup.
TAG_PATTERN = re.compile(r'<[^>]*>')

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


def _format_time(hours: int, minutes: int, lang: str) -> str:
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


def resolve_tts_api(model: Any) -> tuple:
    """
    Work out how this model's apply_tts wants to be called.

    v3+ models take (text, speaker) and accept ssml_text; multi_v2 takes
    (texts, speakers) and has no SSML support at all.
    """
    try:
        params = inspect.signature(model.apply_tts).parameters
    except (TypeError, ValueError):
        params = {}

    text_arg = 'texts' if 'texts' in params and 'text' not in params else 'text'
    speaker_arg = 'speakers' if 'speakers' in params and 'speaker' not in params else 'speaker'
    supports_ssml = 'ssml_text' in params
    return text_arg, speaker_arg, supports_ssml


def _escape_ssml_text(text: str) -> str:
    """Escape characters that would otherwise make the SSML document invalid."""
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


class SileroEventHandler(AsyncEventHandler):
    def __init__(
        self,
        wyoming_info: Info,
        cli_args: Any,
        model: Any,
        sample_rate: int,
        speaker: str,
        speakers: list,
        language: str,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.wyoming_info_event = wyoming_info.event()
        self.cli_args = cli_args
        self.model = model
        self.sample_rate = sample_rate
        self.speaker = speaker
        self.speakers = list(speakers or [])
        self.language = language

        self.text_arg, self.speaker_arg, self.supports_ssml = resolve_tts_api(model)

        # Global SSML parameters
        self.prosody_rate = getattr(cli_args, 'prosody_rate', None)
        self.prosody_pitch = getattr(cli_args, 'prosody_pitch', None)
        self.break_time = getattr(cli_args, 'break_time', None)
        self.break_strength = getattr(cli_args, 'break_strength', None)

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(self.wyoming_info_event)
            _LOGGER.debug("Sent info")
            return True

        if Synthesize.is_type(event.type):
            start_time = time.time()

            synthesize = Synthesize.from_event(event)
            text = synthesize.text or ""

            # Extract requested speaker from voice.name if present
            requested_speaker = None
            if synthesize.voice is not None:
                requested_speaker = synthesize.voice.name

            _LOGGER.info("📝 Synthesis request: text='%s'... (len=%d), requested_speaker='%s', default_speaker='%s'",
                        text[:50], len(text), requested_speaker, self.speaker)

            if not text.strip():
                _LOGGER.warning("⚠️  Empty synthesis request, returning no audio")
                await self._write_empty_audio()
                return True

            # Honour the client's declared format, falling back to sniffing the
            # <speak> root for clients that do not send text_format.
            is_ssml = (
                synthesize.text_format == SynthesizeTextFormat.SSML
                or text.strip().startswith('<speak>')
            )

            if is_ssml and not self.supports_ssml:
                # The model cannot parse SSML, so read out the text nodes
                # rather than failing the whole request.
                _LOGGER.warning("⚠️  Model does not support SSML; stripping markup from request")
                text = TAG_PATTERN.sub('', text)
                is_ssml = False

            # Normalize numbers to words before SSML wrapping
            text = self._normalize_numbers(text, is_ssml=is_ssml)

            # Apply global SSML wrapping if configured
            text, is_ssml = self._wrap_with_ssml(text, is_ssml)

            # Run synthesis in executor to avoid blocking the loop
            _LOGGER.info("🎤 Starting synthesis...")
            synthesis_start = time.time()
            try:
                audio_tensor = await asyncio.get_event_loop().run_in_executor(
                    None, self._synthesize, text, requested_speaker, is_ssml
                )
            except Exception as err:
                _LOGGER.exception("❌ Synthesis failed after %.2f seconds", time.time() - synthesis_start)
                await self.write_event(
                    Error(text=f"Silero synthesis failed: {err}", code="synthesis_failed").event()
                )
                # Still close the audio stream so clients that ignore Error do
                # not wait forever for an AudioStop.
                await self._write_empty_audio()
                return True

            synthesis_time = time.time() - synthesis_start
            _LOGGER.info("✅ Synthesis completed in %.2f seconds", synthesis_time)

            _LOGGER.debug("🔊 Converting audio tensor to PCM bytes...")
            # Convert to int16
            audio_int16 = (audio_tensor * 32767).clamp(-32768, 32767).type(torch.int16)
            audio_bytes = audio_int16.numpy().tobytes()
            audio_duration = len(audio_tensor) / self.sample_rate

            _LOGGER.info("🎵 Generated audio: duration=%.2fs, size=%d bytes, sample_rate=%d Hz",
                        audio_duration, len(audio_bytes), self.sample_rate)

            # Send AudioStart
            await self.write_event(
                AudioStart(
                    rate=self.sample_rate,
                    width=2,
                    channels=1,
                ).event(),
            )

            # Send AudioChunk(s)
            chunk_size = 1024 * 2
            num_chunks = int(math.ceil(len(audio_bytes) / chunk_size))
            _LOGGER.debug("📦 Sending %d audio chunks...", num_chunks)

            for i in range(num_chunks):
                offset = i * chunk_size
                chunk = audio_bytes[offset : offset + chunk_size]
                await self.write_event(
                    AudioChunk(
                        audio=chunk,
                        rate=self.sample_rate,
                        width=2,
                        channels=1,
                    ).event(),
                )

            # Send AudioStop
            await self.write_event(AudioStop().event())

            total_time = time.time() - start_time
            _LOGGER.info("✨ Request completed in %.2f seconds (synthesis: %.2fs, streaming: %.2fs)",
                        total_time, synthesis_time, total_time - synthesis_time)

            return True

        return True

    async def _write_empty_audio(self) -> None:
        """Open and immediately close an audio stream, so the client can finish."""
        await self.write_event(
            AudioStart(rate=self.sample_rate, width=2, channels=1).event()
        )
        await self.write_event(AudioStop().event())

    def _normalize_numbers(self, text: str, is_ssml: bool = False) -> str:
        """Convert digits in text to words using num2words."""
        original_text = text

        if is_ssml:
            # Rewrite text nodes only; touching markup would corrupt attributes
            # such as <break time="500ms"/>.
            pieces = []
            position = 0
            for tag in TAG_PATTERN.finditer(text):
                pieces.append(self._normalize_text_node(text[position:tag.start()]))
                pieces.append(tag.group(0))
                position = tag.end()
            pieces.append(self._normalize_text_node(text[position:]))
            normalized_text = ''.join(pieces)
        else:
            normalized_text = self._normalize_text_node(text)

        if original_text != normalized_text:
            _LOGGER.info("📊 Normalized text: '%s' -> '%s'",
                        original_text[:50] + ('...' if len(original_text) > 50 else ''),
                        normalized_text[:50] + ('...' if len(normalized_text) > 50 else ''))

        return normalized_text

    def _normalize_text_node(self, text: str) -> str:
        """Convert times and numbers inside a single run of plain text."""
        if not text:
            return text

        num2words_lang = self.language

        def replace_time(match):
            """Replace time format with natural language."""
            hours = int(match.group(1))
            minutes = int(match.group(2))
            if hours > 23 or minutes > 59:
                # Not a time of day (a ratio or a score), leave it to the
                # number pass.
                return match.group(0)
            try:
                return _format_time(hours, minutes, num2words_lang)
            except Exception as e:
                _LOGGER.warning("⚠️  Failed to format time '%s': %s. Using fallback.", match.group(0), e)
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

                return num2words(number, lang=num2words_lang)
            except Exception as e:
                _LOGGER.warning("⚠️  Failed to convert number '%s' to words: %s. Using original.", number_str, e)
                return number_str

        # Replace all numbers in the text
        return NUMBER_PATTERN.sub(replace_number, text)

    def _wrap_with_ssml(self, text: str, is_ssml: bool = False) -> tuple:
        """Wrap text with global SSML tags if configured."""
        if not (self.prosody_rate or self.prosody_pitch or self.break_time or self.break_strength):
            return text, is_ssml

        if not self.supports_ssml:
            _LOGGER.warning("⚠️  Global SSML parameters are set but this model does not support SSML; ignoring them")
            return text, is_ssml

        if is_ssml:
            # Keep the caller's markup, drop only the outer <speak> element.
            text_stripped = text.strip()
            if text_stripped.startswith('<speak>') and text_stripped.endswith('</speak>'):
                inner_content = text_stripped[7:-8].strip()
            else:
                inner_content = text_stripped
            _LOGGER.debug("📋 Detected existing SSML, wrapping with global parameters")
        else:
            # Plain text becomes an SSML text node, so it has to be escaped or
            # characters like '<' and '&' make the document invalid.
            inner_content = _escape_ssml_text(text)
            _LOGGER.debug("📋 Plain text detected, applying global SSML parameters")

        wrapped = inner_content

        # Apply prosody
        if self.prosody_rate or self.prosody_pitch:
            prosody_attrs = []
            if self.prosody_rate:
                prosody_attrs.append(f'rate="{self.prosody_rate}"')
            if self.prosody_pitch:
                prosody_attrs.append(f'pitch="{self.prosody_pitch}"')
            prosody_tag = ' '.join(prosody_attrs)
            wrapped = f'<prosody {prosody_tag}>{wrapped}</prosody>'
            _LOGGER.debug("📋 Applied global prosody: %s", prosody_tag)

        # Apply break
        if self.break_time or self.break_strength:
            break_attrs = []
            if self.break_time:
                break_attrs.append(f'time="{self.break_time}"')
            if self.break_strength:
                break_attrs.append(f'strength="{self.break_strength}"')
            break_tag = ' '.join(break_attrs)
            wrapped = f'{wrapped}<break {break_tag}/>'
            _LOGGER.debug("📋 Applied global break: %s", break_tag)

        # Wrap in <speak> tags
        return f'<speak>{wrapped}</speak>', True

    def _synthesize(self, text: str, speaker: Optional[str] = None, is_ssml: bool = False):
        """Synthesize text using the loaded model. Raises on failure."""
        effective_speaker = speaker if speaker else self.speaker

        # Fallback if speaker invalid
        if self.speakers and effective_speaker not in self.speakers:
            _LOGGER.warning("Requested speaker '%s' not found. Falling back to default '%s'",
                            effective_speaker, self.speaker)
            effective_speaker = self.speaker

        _LOGGER.debug("🔧 _synthesize called with speaker='%s'", effective_speaker)

        kwargs = {self.speaker_arg: effective_speaker, "sample_rate": self.sample_rate}
        if is_ssml:
            _LOGGER.debug("🔖 Using SSML synthesis")
            kwargs["ssml_text"] = text
        else:
            _LOGGER.debug("📝 Using plain text synthesis")
            kwargs[self.text_arg] = text

        audio = self.model.apply_tts(**kwargs)

        # Batch-oriented models (multi_v2) return a list of tensors, one per
        # input text, rather than a single waveform.
        if isinstance(audio, (list, tuple)):
            if not audio:
                raise ValueError("Model returned no audio")
            audio = audio[0] if len(audio) == 1 else torch.cat([part.reshape(-1) for part in audio])

        return audio.cpu()
