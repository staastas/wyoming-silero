
import argparse
import asyncio
import logging
from functools import partial
import sys
import os

# Suppress TypedStorage deprecation warning from torch.package
import warnings
warnings.filterwarnings('ignore', message='TypedStorage is deprecated')
warnings.filterwarnings('ignore', category=SyntaxWarning)

from wyoming.info import Attribution, Info, TtsProgram, TtsVoice
from wyoming.server import AsyncServer

from .handler import SileroEventHandler
from . import __version__

_LOGGER = logging.getLogger(__name__)

async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("WYOMING_HOST", "0.0.0.0"), help="Host to listen on")
    parser.add_argument("--port", type=int, default=int(os.getenv("WYOMING_PORT", "10200")), help="Port to listen on")
    parser.add_argument("--uri", default=os.getenv("WYOMING_URI"), help="unix:// or tcp://")
    parser.add_argument("--language", default=os.getenv("SILERO_LANGUAGE", "en"), help="Language code (en, ru, de, es, fr)")
    parser.add_argument("--model", default=os.getenv("SILERO_MODEL", "v3_en"), help="Model name (e.g. v3_en, v4_ru, multi_v2)")
    parser.add_argument("--speaker", default=os.getenv("SILERO_SPEAKER"), help="Speaker name (depends on model, e.g. en_0, aidar, kseniya)")
    parser.add_argument("--sample-rate", type=int, default=int(os.getenv("SILERO_SAMPLE_RATE", "48000")), help="Sample rate (default 48000 for v3/v4)")

    # Global SSML parameters
    parser.add_argument("--prosody-rate", default=os.getenv("SILERO_PROSODY_RATE"), help="Global prosody rate (x-slow, slow, medium, fast, x-fast)")
    parser.add_argument("--prosody-pitch", default=os.getenv("SILERO_PROSODY_PITCH"), help="Global prosody pitch (x-low, low, medium, high, x-high)")
    parser.add_argument("--break-time", default=os.getenv("SILERO_BREAK_TIME"), help="Global break time (e.g., 500ms, 1s)")
    parser.add_argument("--break-strength", default=os.getenv("SILERO_BREAK_STRENGTH"), help="Global break strength (x-weak, weak, medium, strong, x-strong)")

    parser.add_argument("--debug", action="store_true", default=os.getenv("WYOMING_DEBUG", "false").lower() == "true", help="Log DEBUG messages")

    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    if not args.uri:
        args.uri = f"tcp://{args.host}:{args.port}"

    _LOGGER.info("="*60)
    _LOGGER.info("🚀 Starting Wyoming Silero TTS Server")
    _LOGGER.info("="*60)
    _LOGGER.info("📋 Configuration:")
    _LOGGER.info("   Language: %s", args.language)
    _LOGGER.info("   Model: %s", args.model)
    _LOGGER.info("   Speaker: %s", args.speaker or "auto")
    _LOGGER.info("   Sample Rate: %d Hz", args.sample_rate)
    if args.prosody_rate or args.prosody_pitch or args.break_time or args.break_strength:
        _LOGGER.info("   Global SSML:")
        if args.prosody_rate:
            _LOGGER.info("      Prosody Rate: %s", args.prosody_rate)
        if args.prosody_pitch:
            _LOGGER.info("      Prosody Pitch: %s", args.prosody_pitch)
        if args.break_time:
            _LOGGER.info("      Break Time: %s", args.break_time)
        if args.break_strength:
            _LOGGER.info("      Break Strength: %s", args.break_strength)

    _LOGGER.info("   URI: %s", args.uri)
    _LOGGER.info("="*60)
    _LOGGER.info("📦 Loading model...")


    from .loader import load_silero_model

    silero_lang = args.language
    if args.model == 'multi_v2':
        silero_lang = 'multi'

    try:
        model = load_silero_model(
            language=silero_lang,
            model_name=args.model,
            download_path=os.path.join(os.path.dirname(__file__), "..", "silero", "model")
        )
    except Exception as e:
        _LOGGER.error(f"Failed to load model: {e}")
        sys.exit(1)



    synthesize_fn = None
    sample_rate = args.sample_rate

    if hasattr(model, 'apply_tts'):
         _LOGGER.debug(f"Model loaded successfully. Attributes: {dir(model)}")

         if hasattr(model, 'speakers'):
             model.speakers = list(model.speakers)
             _LOGGER.info(f"Available speakers: {model.speakers}")
             if args.speaker is None:
                 args.speaker = model.speakers[0]
                 _LOGGER.info(f"No speaker specified, using first available: '{args.speaker}'")
             elif args.speaker not in model.speakers:
                 _LOGGER.warning(f"Speaker '{args.speaker}' not found in model speakers: {model.speakers}")
                 if model.speakers:
                     args.speaker = model.speakers[0]
                     _LOGGER.warning(f"Falling back to speaker '{args.speaker}'")

         def wrapper(text, speaker=None):
             effective_speaker = speaker if speaker else args.speaker
             if hasattr(model, 'speakers') and effective_speaker not in model.speakers:
                 _LOGGER.warning(f"Requested speaker '{effective_speaker}' not found during synthesis.")
                 effective_speaker = args.speaker

             _LOGGER.info(f"🎙️  Synthesizing with speaker='{effective_speaker}'")
             try:
                 is_ssml = text.strip().startswith('<speak>')

                 if is_ssml:
                     _LOGGER.debug("🔖 Using SSML synthesis")
                     ret = model.apply_tts(ssml_text=text, speaker=effective_speaker, sample_rate=sample_rate)
                 else:
                     _LOGGER.debug("📝 Using plain text synthesis")
                     ret = model.apply_tts(text=text, speaker=effective_speaker, sample_rate=sample_rate)
                 return ret.cpu()
             except Exception as e:
                 _LOGGER.error(f"Synthesis failed: {e}")
                 raise e

         synthesize_fn = wrapper

    else:
        _LOGGER.error("Loaded model does not support 'apply_tts'. Is this an old JIT model? Only new .pt models are supported.")
        sys.exit(1)

    SILERO_TO_HA_LANG = {'ua': 'uk'}
    ha_language = SILERO_TO_HA_LANG.get(args.language, args.language)

    voices_list = []
    if hasattr(model, 'speakers') and model.speakers:
        for speaker_name in model.speakers:
            voices_list.append(
                TtsVoice(
                    name=speaker_name,
                    description=speaker_name,
                    installed=True,
                    languages=[ha_language],
                    attribution=Attribution(name="Silero", url="https://github.com/snakers4/silero-models"),
                    version=None,
                )
            )
        _LOGGER.info(f"Advertising {len(voices_list)} voices: {[v.name for v in voices_list]}")
    else:
        voices_list.append(
            TtsVoice(
                name=args.speaker if args.speaker else "default",
                description=f"Silero {ha_language} {args.model}",
                installed=True,
                languages=[ha_language],
                attribution=Attribution(name="Silero", url="https://github.com/snakers4/silero-models"),
                version=None,
            )
        )
        _LOGGER.warning("Could not determine available speakers, advertising only default voice")

    wyoming_info = Info(
        tts=[
            TtsProgram(
                name=f"silero-{ha_language}",
                description=f"Silero TTS {ha_language}",
                attribution=Attribution(name="Silero", url="https://github.com/snakers4/silero-models"),
                installed=True,
                voices=voices_list,
                version=__version__,
            )
        ],
    )

    server = AsyncServer.from_uri(args.uri)
    _LOGGER.info("="*60)
    _LOGGER.info("✅ Server ready!")
    _LOGGER.info(f"🌐 Listening on {args.uri}")
    _LOGGER.info("="*60)

    await server.run(
        partial(
            SileroEventHandler,
            wyoming_info,
            args,
            model,
            synthesize_fn,
            sample_rate,
            args.speaker
        )
    )

def run():
    asyncio.run(main())

if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        pass
