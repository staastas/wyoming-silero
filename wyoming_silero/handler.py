import asyncio
import logging
import torch
import math
from typing import Any, Dict, Optional

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.server import AsyncEventHandler
from wyoming.tts import Synthesize

_LOGGER = logging.getLogger(__name__)

class SileroEventHandler(AsyncEventHandler):
    def __init__(
        self,
        wyoming_info: Info,
        cli_args: Any,
        model: Any,
        start_method: Any,
        sample_rate: int,
        speaker: str,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.wyoming_info_event = wyoming_info.event()
        self.cli_args = cli_args
        self.model = model
        self.start_method = start_method
        self.sample_rate = sample_rate
        self.speaker = speaker

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
            import time
            start_time = time.time()

            synthesize = Synthesize.from_event(event)
            text = synthesize.text

            # Extract requested speaker from voice.name if present
            requested_speaker = None
            if synthesize.voice is not None:
                requested_speaker = synthesize.voice.name

            _LOGGER.info("📝 Synthesis request: text='%s' (length=%d chars), requested_speaker='%s', default_speaker='%s'",
                        text[:100] + ('...' if len(text) > 100 else ''), len(text), requested_speaker, self.speaker)

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

    def _wrap_with_ssml(self, text: str) -> str:
        """Wrap text with global SSML tags if configured."""
        # If no global SSML parameters are set, return text as-is
        if not (self.prosody_rate or self.prosody_pitch or self.break_time or self.break_strength):
            return text

        # Check if text already contains SSML
        text_stripped = text.strip()
        has_ssml = text_stripped.startswith('<speak>') and text_stripped.endswith('</speak>')

        # If text has SSML, we need to unwrap it, apply global tags, and rewrap
        if has_ssml:
            # Remove outer <speak> tags
            inner_content = text_stripped[7:-8].strip()
            _LOGGER.debug("📋 Detected existing SSML, wrapping with global parameters")
        else:
            inner_content = text
            _LOGGER.debug("📋 Plain text detected, applying global SSML parameters")

        # Build the wrapped content
        wrapped = inner_content

        # Apply prosody if configured
        if self.prosody_rate or self.prosody_pitch:
            prosody_attrs = []
            if self.prosody_rate:
                prosody_attrs.append(f'rate="{self.prosody_rate}"')
            if self.prosody_pitch:
                prosody_attrs.append(f'pitch="{self.prosody_pitch}"')
            prosody_tag = ' '.join(prosody_attrs)
            wrapped = f'<prosody {prosody_tag}>{wrapped}</prosody>'
            _LOGGER.debug("📋 Applied global prosody: %s", prosody_tag)

        # Apply break at the end if configured
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
        _LOGGER.debug("📋 Final SSML: %s", result[:200] + ('...' if len(result) > 200 else ''))
        return result

    def _synthesize(self, text: str, speaker: Optional[str] = None):
        _LOGGER.debug("🔧 _synthesize called with speaker='%s'", speaker)
        is_ssml = text.strip().startswith('<speak>')

        if callable(self.start_method):
           if speaker:
               _LOGGER.debug("🎯 Calling synthesis with custom speaker: '%s'", speaker)
               return self.start_method(text, speaker=speaker)
           _LOGGER.debug("🎯 Calling synthesis with default speaker")
           return self.start_method(text)
        _LOGGER.warning("⚠️  No callable start_method, returning empty audio")
        return torch.zeros(0)
