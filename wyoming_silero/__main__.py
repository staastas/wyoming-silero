
import argparse
import asyncio
import logging
from functools import partial
import sys
import os
from urllib.parse import urlparse

# Suppress TypedStorage deprecation warning from torch.package
import warnings
warnings.filterwarnings('ignore', message='TypedStorage is deprecated')
warnings.filterwarnings('ignore', category=SyntaxWarning)

import torch
from wyoming.info import Attribution, Info, TtsProgram, TtsVoice
from wyoming.server import AsyncServer

from .handler import SileroEventHandler, resolve_tts_api
from .loader import load_silero_model
from .text import DEFAULT_MAX_CHARS, parse_break_ms
from . import __version__

_LOGGER = logging.getLogger(__name__)

# Silero uses a few language codes that are not ISO-639-1.
SILERO_TO_HA_LANG = {"ua": "uk", "bel": "be"}

# Models that bundle several languages under one entry; their real language
# lives per speaker, not per model.
UMBRELLA_LANGUAGES = {"multi", "cyrillic", "indic"}

# Multi-language models prefix speaker names with a language code.
SPEAKER_PREFIX_TO_LANG = {
    "ru": "ru", "bel": "be", "ua": "uk", "uz": "uz", "tt": "tt",
    "xal": "xal", "ba": "ba", "en": "en", "de": "de", "es": "es", "fr": "fr",
}

# v3/v4 models offer a "random" speaker that sounds different on every call,
# which makes no sense as a voice to pick in Home Assistant.
HIDDEN_SPEAKERS = {"random"}


def _env_flag(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in ("1", "true", "yes", "on")


def describe_voice(speaker_name: str, language: str) -> str:
    """Build a readable voice label, e.g. "bel_larisa" -> "Larisa (be)"."""
    prefix, _, rest = speaker_name.partition("_")
    if rest.isdigit():
        # Numbered speakers ("en_12") have no name to tidy up.
        return f"{speaker_name} ({language})"
    if rest and prefix in SPEAKER_PREFIX_TO_LANG:
        speaker_name = rest
    return f"{speaker_name.replace('_', ' ').title()} ({language})"


def _supported_sample_rates(model_info) -> list:
    """Read the sample rates a model accepts from its models.yml entry."""
    rates = model_info.get("sample_rate")
    if rates is None:
        return []
    if isinstance(rates, int):
        return [rates]
    try:
        return sorted(int(rate) for rate in rates)
    except (TypeError, ValueError):
        return []


def resolve_speakers(model, model_info) -> list:
    """Collect the speaker names a model exposes; the families differ."""
    names = getattr(model, "speakers", None)
    if names:
        return list(names)

    # multi_v2 keeps them in a name -> id mapping instead.
    mapping = getattr(model, "speaker_to_id", None)
    if mapping:
        return list(mapping)

    declared = model_info.get("speakers")
    if declared:
        return list(declared)

    return []


def resolve_voice_language(speaker_name: str, model_info, default_lang: str, override: str = None) -> str:
    """Work out which language a single speaker actually speaks."""
    if override:
        return override

    # Some models (e.g. multi_v2) declare the language of every speaker.
    speakers_conf = model_info.get("speakers")
    if speakers_conf is not None and speaker_name in speakers_conf:
        declared = speakers_conf[speaker_name].get("lang")
        if declared:
            return SILERO_TO_HA_LANG.get(declared, declared)

    # Otherwise fall back to the speaker-name prefix ("bel_larisa" -> "be").
    prefix = speaker_name.split("_")[0]
    if prefix in SPEAKER_PREFIX_TO_LANG:
        return SPEAKER_PREFIX_TO_LANG[prefix]

    return default_lang


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("WYOMING_HOST", "0.0.0.0"), help="Host to listen on")
    parser.add_argument("--port", type=int, default=int(os.getenv("WYOMING_PORT", "10200")), help="Port to listen on")
    parser.add_argument("--uri", default=os.getenv("WYOMING_URI"), help="unix:// or tcp://")
    parser.add_argument("--language", default=os.getenv("SILERO_LANGUAGE", "en"), help="Language code (en, ru, de, es, fr)")
    parser.add_argument("--model", default=os.getenv("SILERO_MODEL", "v3_en"), help="Model name (e.g. v3_en, v4_ru, multi_v2)")
    parser.add_argument("--speaker", default=os.getenv("SILERO_SPEAKER"), help="Speaker name (depends on model, e.g. en_0, aidar, kseniya)")
    parser.add_argument("--sample-rate", type=int, default=int(os.getenv("SILERO_SAMPLE_RATE", "48000")), help="Sample rate (default 48000 for v3/v4)")
    parser.add_argument("--ha-language", default=os.getenv("SILERO_HA_LANGUAGE"), help="Override the ISO-639-1 language advertised to clients (useful for umbrella models like multi_v2)")

    # Global SSML parameters
    parser.add_argument("--prosody-rate", default=os.getenv("SILERO_PROSODY_RATE"), help="Global prosody rate (x-slow, slow, medium, fast, x-fast)")
    parser.add_argument("--prosody-pitch", default=os.getenv("SILERO_PROSODY_PITCH"), help="Global prosody pitch (x-low, low, medium, high, x-high)")
    parser.add_argument("--break-time", default=os.getenv("SILERO_BREAK_TIME"), help="Global break time (e.g., 500ms, 1s)")
    parser.add_argument("--break-strength", default=os.getenv("SILERO_BREAK_STRENGTH"), help="Global break strength (x-weak, weak, medium, strong, x-strong)")

    parser.add_argument("--max-chars", type=int, default=int(os.getenv("SILERO_MAX_CHARS", str(DEFAULT_MAX_CHARS))), help="Longest piece of text sent to the model at once")
    parser.add_argument("--threads", type=int, default=int(os.getenv("SILERO_THREADS", "0")), help="CPU threads used by torch (0 = torch default)")
    parser.add_argument("--offline", action="store_true", default=_env_flag("SILERO_OFFLINE"), help="Do not download models.yml when a cached copy exists")

    # Zeroconf discovery (needs host networking to reach Home Assistant)
    parser.add_argument("--zeroconf", action="store_true", default=_env_flag("WYOMING_ZEROCONF"), help="Announce the server to Home Assistant over mDNS")
    parser.add_argument("--zeroconf-name", default=os.getenv("WYOMING_ZEROCONF_NAME"), help="Service name announced over mDNS")
    parser.add_argument("--zeroconf-host", default=os.getenv("WYOMING_ZEROCONF_HOST"), help="IP address announced over mDNS (auto-detected by default)")

    parser.add_argument("--debug", action="store_true", default=_env_flag("WYOMING_DEBUG"), help="Log DEBUG messages")

    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    if not args.uri:
        args.uri = f"tcp://{args.host}:{args.port}"

    try:
        args.break_ms = parse_break_ms(args.break_time, args.break_strength)
    except ValueError as e:
        _LOGGER.error("%s", e)
        sys.exit(1)

    if args.max_chars < 50:
        _LOGGER.error("SILERO_MAX_CHARS must be at least 50, got %d", args.max_chars)
        sys.exit(1)

    if args.threads > 0:
        torch.set_num_threads(args.threads)

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

    silero_lang = args.language
    if args.model == 'multi_v2':
        silero_lang = 'multi'

    try:
        model, model_info = load_silero_model(
            language=silero_lang,
            model_name=args.model,
            download_path=os.path.join(os.path.dirname(__file__), "..", "silero", "model"),
            offline=args.offline,
        )
    except Exception as e:
        _LOGGER.error(f"Failed to load model: {e}")
        sys.exit(1)

    if not hasattr(model, 'apply_tts'):
        _LOGGER.error("Loaded model does not support 'apply_tts'. Is this an old JIT model? Only new .pt models are supported.")
        sys.exit(1)

    _LOGGER.debug(f"Model loaded successfully. Attributes: {dir(model)}")

    speakers = resolve_speakers(model, model_info)
    if speakers:
        _LOGGER.info(f"Available speakers: {speakers}")
        first_speaker = next((name for name in speakers if name not in HIDDEN_SPEAKERS), speakers[0])
        if args.speaker is None:
            args.speaker = first_speaker
            _LOGGER.info(f"No speaker specified, using first available: '{args.speaker}'")
        elif args.speaker not in speakers:
            _LOGGER.warning(f"Speaker '{args.speaker}' not found in model speakers: {speakers}")
            args.speaker = first_speaker
            _LOGGER.warning(f"Falling back to default speaker '{args.speaker}'")
    else:
        _LOGGER.warning("Could not determine available speakers")

    text_arg, speaker_arg, supports_ssml = resolve_tts_api(model)
    _LOGGER.debug("Model TTS API: text arg=%r, speaker arg=%r, SSML=%s", text_arg, speaker_arg, supports_ssml)
    if not supports_ssml and (args.prosody_rate or args.prosody_pitch):
        _LOGGER.warning(
            "Model '%s' does not support SSML; the global prosody parameters will be ignored.", args.model
        )

    supported_rates = _supported_sample_rates(model_info)
    if supported_rates and args.sample_rate not in supported_rates:
        corrected = max(supported_rates)
        _LOGGER.warning(
            "Sample rate %d Hz is not supported by model '%s' (supported: %s). Using %d Hz instead.",
            args.sample_rate, args.model, supported_rates, corrected,
        )
        args.sample_rate = corrected

    ha_language = args.ha_language or SILERO_TO_HA_LANG.get(args.language, args.language)
    if ha_language in UMBRELLA_LANGUAGES:
        _LOGGER.warning(
            "Language '%s' is not an ISO-639-1 code; per-voice languages will be derived from speaker names. "
            "Set SILERO_HA_LANGUAGE to override.", ha_language,
        )

    voice_languages = {
        speaker_name: resolve_voice_language(speaker_name, model_info, ha_language, args.ha_language)
        for speaker_name in speakers
    }

    voices_list = []
    for speaker_name in speakers:
        if speaker_name in HIDDEN_SPEAKERS and speaker_name != args.speaker:
            continue
        voice_language = voice_languages[speaker_name]
        voices_list.append(
            TtsVoice(
                name=speaker_name,
                description=describe_voice(speaker_name, voice_language),
                installed=True,
                languages=[voice_language],
                attribution=Attribution(name="Silero", url="https://github.com/snakers4/silero-models"),
                version=None,
            )
        )
    if voices_list:
        _LOGGER.info(f"Advertising {len(voices_list)} voices: {[(v.name, v.languages[0]) for v in voices_list]}")
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
                supports_synthesize_streaming=True,
            )
        ],
    )

    server = AsyncServer.from_uri(args.uri)

    if args.zeroconf:
        await start_zeroconf(args, ha_language)
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
            args.sample_rate,
            args.speaker,
            speakers,
            ha_language,
            voice_languages,
            asyncio.Lock(),
        )
    )


async def start_zeroconf(args, ha_language: str) -> None:
    """
    Announce the server over mDNS so Home Assistant offers to add it.

    Never fatal: in a Docker bridge network the announcement cannot reach
    Home Assistant anyway, and the integration can still be added by hand.
    """
    parsed = urlparse(args.uri)
    if parsed.scheme != "tcp" or parsed.port is None:
        _LOGGER.warning("Zeroconf discovery needs a tcp:// URI, not %s; skipping", args.uri)
        return

    name = args.zeroconf_name or f"silero-{ha_language}-{parsed.port}"
    try:
        from wyoming.zeroconf import HomeAssistantZeroconf

        zeroconf = HomeAssistantZeroconf(name=name, port=parsed.port, host=args.zeroconf_host)
        await zeroconf.register_server()
    except Exception as e:
        _LOGGER.warning(
            "Zeroconf discovery is unavailable (%s). Add the Wyoming integration in Home Assistant manually.", e
        )
        return

    _LOGGER.info("📡 Zeroconf: announced '%s' at %s:%d", name, zeroconf.host, parsed.port)

def run():
    asyncio.run(main())

if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        pass
