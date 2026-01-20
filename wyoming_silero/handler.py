import asyncio
import logging
import math
import re
import time
from typing import Any, Dict, Optional
import torch
from num2words import num2words

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.server import AsyncEventHandler
from wyoming.tts import Synthesize

_LOGGER = logging.getLogger(__name__)

# Compile regex patterns once
TIME_PATTERN = re.compile(r'(\d{1,2}):(\d{2})')
NUMBER_PATTERN = re.compile(r'-?\d+(?:[.,]\d+)?')

def _get_russian_declension(number: int, one: str, two: str, five: str) -> str:
    """Get the proper declension for Russian numbers."""
    if number % 10 == 1 and number % 100 != 11:
        return one
    elif 2 <= number % 10 <= 4 and (number % 100 < 10 or number % 100 >= 20):
        return two
    else:
        return five


def _format_time(hours: int, minutes: int, lang: str) -> str:
    """Format time with proper declensions."""
    if lang == 'ru':
        # Convert numbers to words
        hours_word = num2words(hours, lang='ru')

        # Get proper declension for hours
        hours_text = f"{hours_word} {_get_russian_declension(hours, 'час', 'часа', 'часов')}"

        # Handle minutes
        if minutes == 0:
            return hours_text
        else:
            minutes_word = num2words(minutes, lang='ru')
            # Fix gender for minutes (feminine)
            # один -> одна (1, 21, 31, 41, 51)
            if minutes % 10 == 1 and minutes % 100 != 11:
                    minutes_word = re.sub(r'один$', 'одна', minutes_word)
            # два -> две (2, 22, 32, 42, 52)
            elif minutes % 10 == 2 and minutes % 100 != 12:
                    minutes_word = re.sub(r'два$', 'две', minutes_word)

            minutes_text = f"{minutes_word} {_get_russian_declension(minutes, 'минута', 'минуты', 'минут')}"
            return f"{hours_text} {minutes_text}"

    elif lang == 'uk':  # Ukrainian
        hours_word = num2words(hours, lang='uk')

        if minutes == 0:
            hours_text = f"{hours_word} {_get_russian_declension(hours, 'година', 'години', 'годин')}"
            return hours_text
        else:
            minutes_word = num2words(minutes, lang='uk')
            hours_text = f"{hours_word} {_get_russian_declension(hours, 'година', 'години', 'годин')}"
            minutes_text = f"{minutes_word} {_get_russian_declension(minutes, 'хвилина', 'хвилини', 'хвилин')}"
            return f"{hours_text} {minutes_text}"

    else:  # English and other languages
        hours_word = num2words(hours, lang=lang)
        hour_unit = "hour" if hours == 1 else "hours"

        if minutes == 0:
            return f"{hours_word} {hour_unit}"
        else:
            minutes_word = num2words(minutes, lang=lang)
            minute_unit = "minute" if minutes == 1 else "minutes"
            return f"{hours_word} {hour_unit} {minutes_word} {minute_unit}"


class SileroEventHandler(AsyncEventHandler):
    def __init__(
        self,
        wyoming_info: Info,
        cli_args: Any,
        model: Any,
        sample_rate: int,
        speaker: str,
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
        self.language = language

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
            text = synthesize.text

            # Extract requested speaker from voice.name if present
            requested_speaker = None
            if synthesize.voice is not None:
                requested_speaker = synthesize.voice.name

            _LOGGER.info("📝 Synthesis request: text='%s'... (len=%d), requested_speaker='%s', default_speaker='%s'",
                        text[:50], len(text), requested_speaker, self.speaker)

            # Normalize numbers to words before SSML wrapping
            text = self._normalize_numbers(text)

            # Apply global SSML wrapping if configured
            text = self._wrap_with_ssml(text)

            # Run synthesis in executor to avoid blocking the loop
            _LOGGER.info("🎤 Starting synthesis...")
            synthesis_start = time.time()
            audio_tensor = await asyncio.get_event_loop().run_in_executor(
                None, self._synthesize, text, requested_speaker
            )
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

    def _normalize_numbers(self, text: str) -> str:
        """Convert digits in text to words using num2words."""
        original_text = text
        num2words_lang = self.language

        def replace_time(match):
            """Replace time format with natural language."""
            try:
                hours = int(match.group(1))
                minutes = int(match.group(2))
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
        normalized_text = NUMBER_PATTERN.sub(replace_number, text)

        if original_text != normalized_text:
            _LOGGER.info("📊 Normalized text: '%s' -> '%s'",
                        original_text[:50] + ('...' if len(original_text) > 50 else ''),
                        normalized_text[:50] + ('...' if len(normalized_text) > 50 else ''))

        return normalized_text

    def _wrap_with_ssml(self, text: str) -> str:
        """Wrap text with global SSML tags if configured."""
        if not (self.prosody_rate or self.prosody_pitch or self.break_time or self.break_strength):
            return text

        text_stripped = text.strip()
        has_ssml = text_stripped.startswith('<speak>') and text_stripped.endswith('</speak>')

        if has_ssml:
            # Remove outer <speak> tags
            inner_content = text_stripped[7:-8].strip()
            _LOGGER.debug("📋 Detected existing SSML, wrapping with global parameters")
        else:
            inner_content = text
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
        result = f'<speak>{wrapped}</speak>'
        return result

    def _synthesize(self, text: str, speaker: Optional[str] = None):
        """Synthesize text using the loaded model."""
        effective_speaker = speaker if speaker else self.speaker

        # Fallback if speaker invalid
        if hasattr(self.model, 'speakers') and effective_speaker not in self.model.speakers:
             _LOGGER.warning("Requested speaker '%s' not found. Falling back to default '%s'",
                             effective_speaker, self.speaker)
             effective_speaker = self.speaker

        _LOGGER.debug("🔧 _synthesize called with speaker='%s'", effective_speaker)

        try:
             is_ssml = text.strip().startswith('<speak>')
             if is_ssml:
                 _LOGGER.debug("🔖 Using SSML synthesis")
                 audio = self.model.apply_tts(ssml_text=text, speaker=effective_speaker, sample_rate=self.sample_rate)
             else:
                 _LOGGER.debug("📝 Using plain text synthesis")
                 audio = self.model.apply_tts(text=text, speaker=effective_speaker, sample_rate=self.sample_rate)

             return audio.cpu()

        except Exception as e:
            _LOGGER.error("Synthesis failed: %s", e)
             # Return silent audio on error to avoid crashing
            return torch.zeros(0)

