# Wyoming Silero Configuration

This file documents all available environment variables for configuring Wyoming Silero.

## Silero TTS Configuration

### SILERO_LANGUAGE
- **Description**: Language code for TTS model
- **Default**: `en`
- **Options**: `ru`, `en`, `de`, `es`, `fr`, `ua`, `multi`, etc.
- **Example**: `SILERO_LANGUAGE=ru`

### SILERO_MODEL
- **Description**: TTS model name
- **Default**: `v3_en`
- **Options**:
  - Russian: `v5_1_ru`, `v5_ru`, `v4_ru`, `v5_cis_base`, `v5_cis_ext`
  - English: `v3_en`, `v3_en_indic`
  - German: `v3_de`
  - Spanish: `v3_es`
  - French: `v3_fr`
  - Ukrainian: `v4_ua`, `v3_ua`
  - Multi-language: `multi_v2`
- **Example**: `SILERO_MODEL=v5_1_ru`
- **Note**: `multi_v2` is an older model: it runs at 8000/16000 Hz and has no SSML support, so the global SSML settings below are ignored for it. Models whose package is a `.jit` file (e.g. `v5_cis_base_nostress_jit`) are not supported.

### SILERO_SPEAKER
- **Description**: Default speaker/voice name
- **Default**: Auto-selected (first available speaker)
- **Options**: Depends on model
  - v5_1_ru: `aidar`, `baya`, `kseniya`, `eugene`, `xenia`
  - v5_cis_base: `bel_anatoliy`, `bel_dmitriy`, `bel_larisa`, etc.
- **Example**: `SILERO_SPEAKER=kseniya`
- **Note**: The `random` speaker of v3/v4 models (a different voice on every call) is not offered to Home Assistant, unless it is the configured default.

### SILERO_SAMPLE_RATE
- **Description**: Audio sample rate in Hz
- **Default**: `48000`
- **Options**: `8000`, `16000`, `24000`, `48000` (depends on model)
- **Example**: `SILERO_SAMPLE_RATE=48000`
- **Note**: The value is checked against the rates the chosen model declares in `models.yml`. If it is unsupported, the server logs a warning and uses the highest supported rate instead (e.g. `multi_v2` only accepts `8000` and `16000`).

### SILERO_HA_LANGUAGE
- **Description**: Override the ISO-639-1 language code advertised to Home Assistant
- **Default**: Derived from `SILERO_LANGUAGE` (`ua` maps to `uk`), or per voice from the speaker name / `models.yml`
- **When to use**: Umbrella models such as `multi_v2`, `v4_cyrillic` and `v4_indic` are not single-language. By default each voice is advertised with the language taken from `models.yml` or from its name prefix (`bel_larisa` becomes `be`). Set this to force one language for every voice.
- **Example**: `SILERO_HA_LANGUAGE=ru`

### SILERO_MAX_CHARS
- **Description**: Longest piece of text sent to the model in one call. Text is split into sentences, and sentences longer than this are cut at commas or spaces.
- **Default**: `500`
- **Minimum**: `50`
- **Example**: `SILERO_MAX_CHARS=300`

### SILERO_THREADS
- **Description**: Number of CPU threads torch uses for synthesis. Requests are processed one at a time, so this is the whole CPU budget of the server.
- **Default**: `0` (let torch decide, usually all cores)
- **Example**: `SILERO_THREADS=4`

### SILERO_OFFLINE
- **Description**: Use the cached `models.yml` instead of downloading it on every start. The model itself is downloaded only once anyway. Without this flag, a failed download falls back to the cache after a 10 second timeout.
- **Default**: `false`
- **Example**: `SILERO_OFFLINE=true`

## Global SSML Configuration

These parameters allow you to apply prosody and a trailing pause to all synthesized text automatically. They are optional and can be combined.

The prosody settings require a model with SSML support (v3 and newer); on a model without it, the server logs a warning at startup and ignores them. The pause settings work on every model, because the pause is added as silence after the audio.

### SILERO_PROSODY_RATE
- **Description**: Global speech rate for all synthesis
- **Default**: None (uses model default)
- **Options**: `x-slow`, `slow`, `medium`, `fast`, `x-fast`
- **Example**: `SILERO_PROSODY_RATE=slow`
- **Note**: Local SSML tags in text can override this setting

### SILERO_PROSODY_PITCH
- **Description**: Global pitch/tone for all synthesis
- **Default**: None (uses model default)
- **Options**: `x-low`, `low`, `medium`, `high`, `x-high`
- **Example**: `SILERO_PROSODY_PITCH=high`
- **Note**: Local SSML tags in text can override this setting

### SILERO_BREAK_TIME
- **Description**: Add a pause at the end of all synthesized text
- **Default**: None (no pause added)
- **Format**: Duration with unit (e.g., `500ms`, `1s`, `1.5s`). An invalid value stops the server at startup.
- **Example**: `SILERO_BREAK_TIME=1s`
- **Note**: If both `SILERO_BREAK_TIME` and `SILERO_BREAK_STRENGTH` are set, `SILERO_BREAK_TIME` takes priority

### SILERO_BREAK_STRENGTH
- **Description**: Add a pause with predefined strength at the end of all synthesized text
- **Default**: None (no pause added)
- **Options**: `none` (0), `x-weak` (100ms), `weak` (250ms), `medium` (500ms), `strong` (1s), `x-strong` (2s)
- **Example**: `SILERO_BREAK_STRENGTH=medium`
- **Note**: If both `SILERO_BREAK_TIME` and `SILERO_BREAK_STRENGTH` are set, `SILERO_BREAK_TIME` takes priority

## Wyoming Server Configuration

### WYOMING_HOST
- **Description**: Host address to bind to
- **Default**: `0.0.0.0` (all interfaces)
- **Example**: `WYOMING_HOST=0.0.0.0`

### WYOMING_PORT
- **Description**: Port to listen on
- **Default**: `10200`
- **Example**: `WYOMING_PORT=10200`

### WYOMING_URI
- **Description**: Full URI (overrides host/port)
- **Default**: `tcp://{host}:{port}`
- **Example**: `WYOMING_URI=tcp://0.0.0.0:10200`

### WYOMING_ZEROCONF
- **Description**: Announce the server to Home Assistant over mDNS (`_wyoming._tcp`), so it shows up under **Discovered**
- **Default**: `false`
- **Note**: Works only with `network_mode: host`. In a bridge network the announcement cannot reach Home Assistant; the server keeps working and the integration is added manually. Errors while announcing are logged as warnings and never stop the server.
- **Example**: `WYOMING_ZEROCONF=true`

### WYOMING_ZEROCONF_NAME
- **Description**: Service name announced over mDNS. Must be unique on the network.
- **Default**: `silero-{language}-{port}`, e.g. `silero-ru-10200`
- **Example**: `WYOMING_ZEROCONF_NAME=silero-living-room`

### WYOMING_ZEROCONF_HOST
- **Description**: IP address announced over mDNS
- **Default**: Auto-detected from the default network interface
- **Example**: `WYOMING_ZEROCONF_HOST=192.168.1.10`

### WYOMING_DEBUG
- **Description**: Enable debug logging
- **Default**: `false`
- **Options**: `true`, `false`
- **Example**: `WYOMING_DEBUG=true`

## Example Configurations

### Russian TTS (default)
```yaml
environment:
  SILERO_LANGUAGE: "ru"
  SILERO_MODEL: "v5_1_ru"
  SILERO_SPEAKER: "kseniya"
  SILERO_SAMPLE_RATE: "48000"
```

### Belarusian TTS
```yaml
environment:
  SILERO_LANGUAGE: "ru"
  SILERO_MODEL: "v5_cis_base"
  SILERO_SPEAKER: "bel_larisa"
  SILERO_SAMPLE_RATE: "48000"
```

### English TTS
```yaml
environment:
  SILERO_LANGUAGE: "en"
  SILERO_MODEL: "v3_en"
  SILERO_SAMPLE_RATE: "48000"
```

### Multi-language TTS
```yaml
environment:
  SILERO_LANGUAGE: "multi"
  SILERO_MODEL: "multi_v2"
  SILERO_SPEAKER: "aidar"
  SILERO_SAMPLE_RATE: "16000"   # multi_v2 supports 8000 and 16000 only
```
Each voice is advertised with its own language from `models.yml`. Add `SILERO_HA_LANGUAGE` to force a single language instead.

### Debug Mode
```yaml
environment:
  SILERO_LANGUAGE: "ru"
  SILERO_MODEL: "v5_1_ru"
  SILERO_SPEAKER: "kseniya"
  WYOMING_DEBUG: "true"
```

### With Global SSML - Slow Speech
```yaml
environment:
  SILERO_LANGUAGE: "ru"
  SILERO_MODEL: "v5_1_ru"
  SILERO_SPEAKER: "kseniya"
  SILERO_PROSODY_RATE: "slow"
```

### With Global SSML - High Pitch with Pause
```yaml
environment:
  SILERO_LANGUAGE: "ru"
  SILERO_MODEL: "v5_1_ru"
  SILERO_SPEAKER: "kseniya"
  SILERO_PROSODY_PITCH: "high"
  SILERO_BREAK_TIME: "1s"
```

### With Global SSML - Combined Settings
```yaml
environment:
  SILERO_LANGUAGE: "en"
  SILERO_MODEL: "v3_en"
  SILERO_PROSODY_RATE: "fast"
  SILERO_PROSODY_PITCH: "low"
  SILERO_BREAK_TIME: "500ms"
```

## Using .env File

Create a `.env` file in the same directory as `docker-compose.yaml`:

```env
# Silero Configuration
SILERO_LANGUAGE=ru
SILERO_MODEL=v5_1_ru
SILERO_SPEAKER=kseniya
SILERO_SAMPLE_RATE=48000

# Wyoming Configuration
WYOMING_HOST=0.0.0.0
WYOMING_PORT=10200
# WYOMING_DEBUG=true
```

Then in `docker-compose.yaml`:
```yaml
services:
  wyoming-silero:
    env_file: .env
```

## Command Line Override

You can still override with command-line arguments:
```bash
docker run -e SILERO_SPEAKER=baya wyoming-silero
```

Or:
```bash
docker-compose run -e SILERO_SPEAKER=baya wyoming-silero
```
