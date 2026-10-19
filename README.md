# Wyoming Silero TTS

Wyoming protocol server for Silero Text-to-Speech models.
This server integrates [Silero TTS](https://github.com/snakers4/silero-models) with [Home Assistant](https://www.home-assistant.io/) via the [Wyoming protocol](https://github.com/OHF-Voice/wyoming).

## Features

- 🎙️ **Multiple Languages**: Supports Russian, English, German, Spanish, French, Ukrainian, Belarusian, and CIS languages.
- 🗣️ **Multiple Voices**: Automatically advertised to Home Assistant.
- 🔄 **Dynamic Voice Switching**: Change voices per synthesis request via Home Assistant UI.
- 🌍 **Language Mapping**: Automatic mapping of internal codes (e.g., `ua`) to ISO-639-1 codes (`uk`) for correct Home Assistant integration.
- 📦 **Dynamic Model Loading**: Automatically downloads the latest `models.yml` configuration and models from the official Silero repository.
- 🧱 **Multi-Container Support**: Runs as a unique service per language (e.g., `silero-ru`, `silero-de`), allowing multiple instances on the same host.
- 🚀 **CPU Optimized**: The Docker image is optimized for performance on CPU, using a specific CPU-only build of PyTorch.
- ⚡ **Streaming Synthesis**: Supports Wyoming text streaming, so Home Assistant starts speaking an LLM answer while it is still being generated. Long texts are split into sentences, so they never hit Silero's input length limit.
- 📡 **Optional Zeroconf Discovery**: Can announce itself to Home Assistant over mDNS (host networking only).

## Quick Start

### Using Docker Compose

1. Configure `docker-compose.yaml`.

   **Note**: The server requires **Internet Access** for the first run to download the model configuration and the selected model. They are stored in the `./silero-models` directory, which the container makes writable for its own user on start.

```yaml
services:
  wyoming-silero:
    image: ghcr.io/staastas/wyoming-silero:latest
    container_name: wyoming-silero
    restart: unless-stopped
    ports:
      - "10200:10200"
    environment:
      # Silero TTS Configuration
      SILERO_LANGUAGE: "ru"           # Internal code (ru, en, de, ua, etc.)
      SILERO_MODEL: "v5_1_ru"         # Model name
      SILERO_SPEAKER: "kseniya"       # Default speaker
      SILERO_SAMPLE_RATE: "48000"     # Sample rate
      # Global SSML Configuration (optional)
      # SILERO_PROSODY_RATE: "medium"    # Speech rate: x-slow, slow, medium, fast, x-fast
      # SILERO_PROSODY_PITCH: "medium"   # Pitch: x-low, low, medium, high, x-high
      # SILERO_BREAK_TIME: "1s"          # Pause duration (e.g., "500ms", "1s")
      # SILERO_BREAK_STRENGTH: "medium"  # Pause strength: x-weak, weak, medium, strong, x-strong
      # Wyoming Server Configuration
      WYOMING_PORT: "10200"    # Port
      # WYOMING_DEBUG: "true"  # Uncomment for debug logging
    volumes:
      - ./silero-models:/app/silero/model  # Persist models and config
```

2. Start the server:
```bash
docker-compose up -d
```

3. In Home Assistant, go to **Settings → Devices & services → Add integration → Wyoming Protocol** and enter the host's IP address and port `10200`.

### Automatic Discovery (optional)

With `WYOMING_ZEROCONF: "true"` the server announces itself over mDNS and Home Assistant offers to add it under **Discovered**.
mDNS does not cross a Docker bridge network, so this only works with `network_mode: host` (then drop the `ports:` section). With the default bridge network nothing breaks: discovery simply does not happen and you add the integration manually as above.

If the host has several network interfaces, set `WYOMING_ZEROCONF_HOST` to the IP Home Assistant should connect to.

### Running Multiple Languages

To support multiple languages simultaneously (e.g., Russian and German), run separate containers for each on different ports.
Home Assistant will detect them as distinct services (`silero-ru`, `silero-de`).

```yaml
services:
  silero-ru:
    image: ghcr.io/staastas/wyoming-silero:latest
    ports:
      - "10200:10200"
    environment:
      SILERO_LANGUAGE: "ru"
      SILERO_MODEL: "v5_1_ru"

  silero-de:
    image: ghcr.io/staastas/wyoming-silero:latest
    ports:
      - "10201:10200"
    environment:
      SILERO_LANGUAGE: "de"
      SILERO_MODEL: "v3_de"
```

## Supported Models

Models are dynamically defined in the upstream [models.yml](https://github.com/snakers4/silero-models/blob/master/models.yml). Common examples:

- **Russian (`ru`)**: `v5_1_ru`, `v5_ru`, `v4_ru`
- **English (`en`)**: `v3_en`
- **German (`de`)**: `v3_de`
- **Ukrainian (`ua`)**: `v4_ua` (Advertised as `uk` to Home Assistant)
- **CIS Multi (`multi`)**: `v5_cis_base` (Supports `bel`, `ru`, `ua` voices)
- **Legacy multi-language (`multi`)**: `multi_v2` (16 kHz, no SSML; each voice is advertised with its own language)

Models distributed as `.jit` packages are rejected at startup with a clear message.

## Troubleshooting

- **No Voices in Home Assistant**: Use `Reload` on the Wyoming integration or delete and re-add it. Home Assistant caches device configurations aggressively.
- **Offline Mode**: Ensure the first run has internet access to populate the `silero-models` volume. Subsequent runs can operate offline using cached files.
- **Unsupported Sample Rate**: The server checks `SILERO_SAMPLE_RATE` against the rates the model declares and falls back to the highest supported one, logging a warning.
- **Synthesis Errors**: A sentence that fails is logged with a traceback and skipped, so the rest of the answer is still spoken. If nothing could be synthesized, the client gets a Wyoming `Error` event instead of silent audio.
- **Permission denied in the models directory**: The container fixes the ownership of `./silero-models` on start. If you run it with an explicit `user:`, make sure that user can write to the directory.
- **Long SSML documents**: Plain text is split into sentences automatically, but SSML is sent to the model as a whole and must stay within Silero's length limit.
- **Health check**: `docker ps` shows the container as `healthy` once it answers a Wyoming `Describe` request. The first start can take a few minutes while the model downloads.

## Credits

- [Silero Models](https://github.com/snakers4/silero-models)
- [Wyoming Protocol](https://github.com/OHF-Voice/wyoming)
