import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import torch

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.error import Error
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.server import AsyncEventHandler
from wyoming.tts import (
    Synthesize,
    SynthesizeChunk,
    SynthesizeStart,
    SynthesizeStop,
    SynthesizeStopped,
    SynthesizeTextFormat,
    SynthesizeVoice,
)

from .text import (
    DEFAULT_MAX_CHARS,
    SentenceSplitter,
    escape_ssml_text,
    normalize_numbers,
    split_sentences,
    strip_ssml,
)

_LOGGER = logging.getLogger(__name__)

# Bytes of 16-bit mono PCM per AudioChunk event.
CHUNK_BYTES = 1024 * 2


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


@dataclass
class _Request:
    """One synthesis request, either a single Synthesize or a whole stream."""

    speaker: str
    language: str
    is_ssml: bool
    started_at: float = field(default_factory=time.time)
    audio_started: bool = False
    sentences_ok: int = 0
    sentences_failed: int = 0
    last_error: Optional[Exception] = None
    audio_seconds: float = 0.0
    # Streaming only: the sentence splitter, or the SSML collected so far.
    splitter: Optional[SentenceSplitter] = None
    ssml_parts: List[str] = field(default_factory=list)


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
        voice_languages: Dict[str, str],
        synthesis_lock: asyncio.Lock,
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
        self.voice_languages = dict(voice_languages or {})
        # The model is shared by every connection; this keeps two requests
        # from running it at the same time.
        self.synthesis_lock = synthesis_lock

        self.text_arg, self.speaker_arg, self.supports_ssml = resolve_tts_api(model)

        # Global SSML parameters
        self.prosody_rate = getattr(cli_args, 'prosody_rate', None)
        self.prosody_pitch = getattr(cli_args, 'prosody_pitch', None)
        # The trailing pause is appended as silence, so it works on every
        # model and is not repeated after each sentence.
        self.break_ms = getattr(cli_args, 'break_ms', 0) or 0
        self.max_chars = getattr(cli_args, 'max_chars', None) or DEFAULT_MAX_CHARS

        self._stream: Optional[_Request] = None

    async def handle_event(self, event: Event) -> bool:
        if Describe.is_type(event.type):
            await self.write_event(self.wyoming_info_event)
            _LOGGER.debug("Sent info")
            return True

        if SynthesizeStart.is_type(event.type):
            start = SynthesizeStart.from_event(event)
            self._stream = self._new_request(start.voice, start.text_format, "")
            if not self._stream.is_ssml:
                self._stream.splitter = SentenceSplitter(self.max_chars)
            _LOGGER.info("📝 Streaming synthesis started: speaker='%s', language='%s'",
                         self._stream.speaker, self._stream.language)
            return True

        if SynthesizeChunk.is_type(event.type):
            if self._stream is None:
                _LOGGER.warning("⚠️  Got synthesize-chunk without synthesize-start; ignoring")
                return True
            chunk = SynthesizeChunk.from_event(event)
            if self._stream.splitter is None:
                # SSML cannot be cut into sentences safely; read it all at
                # the end of the stream.
                self._stream.ssml_parts.append(chunk.text)
                return True
            for sentence in self._stream.splitter.add(chunk.text):
                await self._speak(self._stream, sentence)
            return True

        if SynthesizeStop.is_type(event.type):
            request = self._stream
            self._stream = None
            if request is None:
                _LOGGER.warning("⚠️  Got synthesize-stop without synthesize-start; ignoring")
                await self.write_event(SynthesizeStopped().event())
                return True
            if request.splitter is not None:
                for sentence in request.splitter.finish():
                    await self._speak(request, sentence)
            elif ''.join(request.ssml_parts).strip():
                await self._speak(request, ''.join(request.ssml_parts))
            await self._finish(request)
            await self.write_event(SynthesizeStopped().event())
            return True

        if Synthesize.is_type(event.type):
            if self._stream is not None:
                # Streaming clients also send the full text for servers that
                # cannot stream; it was already handled chunk by chunk.
                _LOGGER.debug("Ignoring synthesize sent alongside a stream")
                return True

            synthesize = Synthesize.from_event(event)
            text = synthesize.text or ""
            request = self._new_request(synthesize.voice, synthesize.text_format, text)

            _LOGGER.info("📝 Synthesis request: text='%s'... (len=%d), speaker='%s', language='%s'",
                         text[:50], len(text), request.speaker, request.language)

            if not text.strip():
                _LOGGER.warning("⚠️  Empty synthesis request, returning no audio")
                await self._finish(request)
                return True

            if request.is_ssml:
                pieces = [text]
            else:
                pieces = split_sentences(text, self.max_chars)
            for piece in pieces:
                await self._speak(request, piece)
            await self._finish(request)
            return True

        return True

    def _new_request(self, voice: Optional[SynthesizeVoice], text_format: Any, text: str) -> _Request:
        """Pick the speaker and language for a request."""
        speaker = self._resolve_speaker(voice)
        language = self.voice_languages.get(speaker)
        if not language and voice is not None and voice.language:
            language = voice.language
        language = language or self.language

        # Honour the client's declared format, falling back to sniffing the
        # <speak> root for clients that do not send text_format.
        is_ssml = text_format == SynthesizeTextFormat.SSML or text.strip().startswith('<speak>')
        return _Request(speaker=speaker, language=language, is_ssml=is_ssml)

    def _resolve_speaker(self, voice: Optional[SynthesizeVoice]) -> str:
        if voice is not None and voice.name:
            if not self.speakers or voice.name in self.speakers:
                return voice.name
            _LOGGER.warning("Requested speaker '%s' not found. Falling back to default '%s'",
                            voice.name, self.speaker)
            return self.speaker

        if voice is not None and voice.language:
            # A voice requested only by language: take the default speaker if
            # it fits, otherwise the first speaker of that language.
            wanted = voice.language.split('-')[0].split('_')[0].lower()
            if self.voice_languages.get(self.speaker) == wanted:
                return self.speaker
            for name, lang in self.voice_languages.items():
                if lang == wanted:
                    return name

        return self.speaker

    def _prepare_text(self, request: _Request, text: str) -> tuple:
        """Turn a piece of request text into what the model should read."""
        is_ssml = request.is_ssml

        if is_ssml and not self.supports_ssml:
            # The model cannot parse SSML, so read out the text nodes
            # rather than failing the whole request.
            _LOGGER.warning("⚠️  Model does not support SSML; stripping markup from request")
            text = strip_ssml(text)
            is_ssml = False

        text = normalize_numbers(text, request.language, is_ssml=is_ssml)
        return self._wrap_with_ssml(text, is_ssml)

    def _wrap_with_ssml(self, text: str, is_ssml: bool = False) -> tuple:
        """Wrap text with the global prosody settings if configured."""
        if not (self.prosody_rate or self.prosody_pitch):
            return text, is_ssml

        if not self.supports_ssml:
            return text, is_ssml

        if is_ssml:
            # Keep the caller's markup, drop only the outer <speak> element.
            text_stripped = text.strip()
            if text_stripped.startswith('<speak>') and text_stripped.endswith('</speak>'):
                inner_content = text_stripped[7:-8].strip()
            else:
                inner_content = text_stripped
        else:
            # Plain text becomes an SSML text node, so it has to be escaped or
            # characters like '<' and '&' make the document invalid.
            inner_content = escape_ssml_text(text)

        prosody_attrs = []
        if self.prosody_rate:
            prosody_attrs.append(f'rate="{self.prosody_rate}"')
        if self.prosody_pitch:
            prosody_attrs.append(f'pitch="{self.prosody_pitch}"')
        prosody_tag = ' '.join(prosody_attrs)
        return f'<speak><prosody {prosody_tag}>{inner_content}</prosody></speak>', True

    async def _speak(self, request: _Request, text: str) -> None:
        """Synthesize one piece of text and stream its audio to the client."""
        if not text.strip():
            return

        model_text, is_ssml = self._prepare_text(request, text)
        _LOGGER.debug("🎤 Synthesizing: %r", model_text)

        synthesis_start = time.time()
        try:
            async with self.synthesis_lock:
                audio_tensor = await asyncio.get_running_loop().run_in_executor(
                    None, self._synthesize, model_text, request.speaker, is_ssml
                )
        except Exception as err:
            # Skip this piece but keep going, so one bad sentence does not
            # silence the whole answer.
            _LOGGER.exception("❌ Synthesis failed after %.2f seconds for text %r",
                              time.time() - synthesis_start, text[:100])
            request.sentences_failed += 1
            request.last_error = err
            return

        audio_int16 = (audio_tensor * 32767).clamp(-32768, 32767).type(torch.int16)
        audio_bytes = audio_int16.numpy().tobytes()
        duration = len(audio_tensor) / self.sample_rate
        _LOGGER.debug("✅ Synthesized %.2fs of audio in %.2fs", duration, time.time() - synthesis_start)

        request.sentences_ok += 1
        request.audio_seconds += duration
        await self._write_audio(request, audio_bytes)

    async def _write_audio(self, request: _Request, audio_bytes: bytes) -> None:
        if not request.audio_started:
            await self.write_event(AudioStart(rate=self.sample_rate, width=2, channels=1).event())
            request.audio_started = True

        for offset in range(0, len(audio_bytes), CHUNK_BYTES):
            await self.write_event(
                AudioChunk(
                    audio=audio_bytes[offset:offset + CHUNK_BYTES],
                    rate=self.sample_rate,
                    width=2,
                    channels=1,
                ).event(),
            )

    async def _finish(self, request: _Request) -> None:
        """Close the audio stream of a request, reporting failure if nothing was spoken."""
        if request.sentences_failed and not request.sentences_ok:
            await self.write_event(
                Error(text=f"Silero synthesis failed: {request.last_error}", code="synthesis_failed").event()
            )
        elif request.sentences_ok and self.break_ms:
            silence_samples = self.sample_rate * self.break_ms // 1000
            await self._write_audio(request, bytes(silence_samples * 2))

        # Always open and close the audio stream, so clients that ignore
        # Error do not wait forever for an AudioStop.
        if not request.audio_started:
            await self.write_event(AudioStart(rate=self.sample_rate, width=2, channels=1).event())
            request.audio_started = True
        await self.write_event(AudioStop().event())

        _LOGGER.info("✨ Request completed in %.2fs: %.2fs of audio, %d piece(s) spoken, %d failed",
                     time.time() - request.started_at, request.audio_seconds,
                     request.sentences_ok, request.sentences_failed)

    def _synthesize(self, text: str, speaker: str, is_ssml: bool = False):
        """Synthesize text using the loaded model. Raises on failure."""
        kwargs = {self.speaker_arg: speaker, "sample_rate": self.sample_rate}
        if is_ssml:
            kwargs["ssml_text"] = text
        else:
            kwargs[self.text_arg] = text

        audio = self.model.apply_tts(**kwargs)

        # Batch-oriented models (multi_v2) return a list of tensors, one per
        # input text, rather than a single waveform.
        if isinstance(audio, (list, tuple)):
            if not audio:
                raise ValueError("Model returned no audio")
            audio = audio[0] if len(audio) == 1 else torch.cat([part.reshape(-1) for part in audio])

        return audio.cpu()
