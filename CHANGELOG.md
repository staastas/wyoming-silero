# Changelog

## 1.1.0

### Highlights

- **Streaming synthesis.** The server now supports Wyoming text streaming. With an LLM conversation agent, Home Assistant starts speaking the first sentence while the rest of the answer is still being generated.
- **Long answers no longer fail.** Text is split into sentences before synthesis, and over-long sentences are cut at commas or spaces, so Silero's input length limit is never hit. Audio is sent sentence by sentence, so playback starts sooner for normal requests too.
- **Works with bind mounts out of the box.** On Linux, Docker creates `./silero-models` as root, and the first start used to fail with a permission error. The container now fixes the ownership itself and then runs as the unprivileged `silero` user.

### New

- Optional Zeroconf (mDNS) discovery: set `WYOMING_ZEROCONF=true` and Home Assistant lists the server under **Discovered**. It needs `network_mode: host`. In the default bridge network nothing changes: discovery does not happen and the integration is added manually as before. `WYOMING_ZEROCONF_NAME` and `WYOMING_ZEROCONF_HOST` override the announced name and IP.
- `SILERO_THREADS`: number of CPU threads used for synthesis.
- `SILERO_MAX_CHARS`: longest piece of text sent to the model at once (default `500`).
- `SILERO_OFFLINE=true`: use the cached `models.yml` instead of downloading it on every start.
- Voices are shown with readable names in Home Assistant, e.g. `Kseniya (ru)`, `Larisa (be)`.
- A voice requested only by language now picks a speaker of that language.

### Changed

- **The trailing pause (`SILERO_BREAK_TIME` / `SILERO_BREAK_STRENGTH`) is added as silence after the speech** instead of an SSML `<break>` tag. It is still added once, at the end of the whole answer, and it now works on models without SSML support (`multi_v2`). Strengths map to `x-weak` 100ms, `weak` 250ms, `medium` 500ms, `strong` 1s, `x-strong` 2s. An invalid value now stops the server at startup with a clear message.
- **A sentence that fails to synthesize is skipped** and logged; the rest of the answer is still spoken. An `Error` event is sent only if nothing could be synthesized.
- Numbers are spelled out in the language of the selected voice. On multi-language models such as `v5_cis_base` or `multi_v2`, digits used to be left as-is.
- The `random` speaker of v3/v4 models is no longer offered in Home Assistant (it sounds different on every call), unless it is the configured default.
- Requests are synthesized one at a time, so two simultaneous requests (e.g. an announcement during an Assist answer) no longer compete for the CPU inside one model.
- Downloading `models.yml` times out after 10 seconds and falls back to the cached copy, instead of delaying start-up without a network.
- The Docker health check now sends a real Wyoming `Describe` request; the container becomes `healthy` only when the server answers. The start period is 5 minutes to cover the first model download.
- `.env.example` no longer enables debug logging and all SSML settings by default.

### Upgrade notes

- No configuration changes are required.
- If you set `SILERO_BREAK_TIME` or `SILERO_BREAK_STRENGTH`, check that the value is valid (`500ms`, `1s`, `medium`, …): the server now refuses to start with an invalid one.
- If you run the container with an explicit `user:`, the models directory must be writable by that user; the automatic ownership fix only runs when the container starts as root.
- Plain text is split automatically, but SSML requests are still sent to the model as a whole and must stay within Silero's length limit.
- After upgrading, use **Reload** on the Wyoming integration in Home Assistant to pick up the new voice names and streaming support.

## 1.0.2

- Fixed invalid SSML when the text contains `<`, `>` or `&` and global SSML settings are enabled.
- Synthesis failures are reported to the client as a Wyoming `Error` event instead of silent audio.
- Number normalization: times of day, decimals with a comma, and no longer mangling IP addresses, versions or SSML attributes.
- Support for the legacy `multi_v2` model: per-voice languages, 8/16 kHz, no SSML.
- The sample rate is checked against the rates the model supports.
- `SILERO_HA_LANGUAGE` to override the language advertised to Home Assistant.

## 1.0.1

- Numbers and digits are spelled out before synthesis, since Silero models skip them otherwise ([#1](https://github.com/staastas/wyoming-silero/issues/1)).

## 1.0.0

- Initial release.
