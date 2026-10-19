
import os
import logging
import urllib.request
from typing import Any, Tuple

import torch
from omegaconf import OmegaConf

_LOGGER = logging.getLogger(__name__)

MODELS_URL = "https://raw.githubusercontent.com/snakers4/silero-models/master/models.yml"

# models.yml is small; without a network it is better to fail fast and use
# the cached copy than to hang the start-up.
MODELS_YML_TIMEOUT = 10


def _download_small_file(url: str, path: str, timeout: float) -> None:
    """Download a file, replacing the target only once it is complete."""
    tmp_path = f"{path}.tmp"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        data = response.read()
    with open(tmp_path, "wb") as tmp_file:
        tmp_file.write(data)
    os.replace(tmp_path, path)


def load_silero_model(
    language: str,
    model_name: str,
    download_path: str,
    offline: bool = False,
) -> Tuple[Any, Any]:
    """
    Load a Silero TTS model (v3+ .pt format).
    Downloads models.yml and the model itself if needed.

    Returns the model and its models.yml entry, which carries the supported
    sample rates and, for multi-speaker models, the per-speaker languages.
    """

    # 1. Download or load models.yml
    models_path = os.path.join(download_path, "models.yml")

    os.makedirs(download_path, exist_ok=True)

    if offline and os.path.exists(models_path):
        _LOGGER.info("Offline mode: using cached models.yml at %s", models_path)
    else:
        try:
            _LOGGER.info("Downloading latest models.yml from %s", MODELS_URL)
            _download_small_file(MODELS_URL, models_path, MODELS_YML_TIMEOUT)
        except Exception as e:
            _LOGGER.warning("Failed to download models.yml: %s", e)
            if not os.path.exists(models_path):
                raise FileNotFoundError(
                    f"models.yml could not be downloaded and not found in {models_path}. Network required for first run."
                )
            _LOGGER.warning("Using cached models.yml found at %s", models_path)

    _LOGGER.debug("Loading configuration from %s", models_path)
    models_conf = OmegaConf.load(models_path)

    if language not in models_conf.tts_models:
        raise ValueError(f"Language '{language}' not found in models.yml. Available: {list(models_conf.tts_models.keys())}")

    lang_conf = models_conf.tts_models[language]
    if model_name not in lang_conf:
        raise ValueError(f"Model '{model_name}' not found for language '{language}'. Available: {list(lang_conf.keys())}")

    model_info = lang_conf[model_name].latest

    model_url = model_info.get("package")
    if not model_url:
        raise ValueError(f"Model '{model_name}' (latest) does not have a 'package' url.")

    if model_url.endswith(".jit"):
        raise ValueError(
            f"Model '{model_name}' is a TorchScript (.jit) package, which this server does not support. "
            f"Pick a model whose package is a '.pt' file."
        )

    model_filename = os.path.basename(model_url)
    model_file_path = os.path.join(download_path, model_filename)

    if not os.path.exists(model_file_path):
        if offline:
            _LOGGER.warning("Offline mode, but model %s is not cached yet; downloading it", model_filename)
        _LOGGER.info("Downloading model from %s to %s", model_url, model_file_path)
        torch.hub.download_url_to_file(model_url, model_file_path, progress=True)
    else:
        _LOGGER.info("Model found at %s", model_file_path)

    _LOGGER.info("Loading model package...")
    importer = torch.package.PackageImporter(model_file_path)
    model = importer.load_pickle("tts_models", "model")

    device = torch.device("cpu")
    model.to(device)

    return model, model_info
