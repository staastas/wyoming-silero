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

## Quick Start

### Using Docker Compose

1. Configure `docker-compose.yaml`.

   **Note**: The server requires **Internet Access** for the first run to download the model configuration and the selected model. These are persisted in the `silero-models` volume.

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

## Troubleshooting

- **No Voices in Home Assistant**: Use `Reload` on the Wyoming integration or delete and re-add it. Home Assistant caches device configurations aggressively.
- **Offline Mode**: Ensure the first run has internet access to populate the `silero-models` volume. Subsequent runs can operate offline using cached files.

## Credits

- [Silero Models](https://github.com/snakers4/silero-models)
- [Wyoming Protocol](https://github.com/OHF-Voice/wyoming)
