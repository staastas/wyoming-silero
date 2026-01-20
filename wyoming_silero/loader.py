
import os
import logging
from typing import Any

import torch
from omegaconf import OmegaConf

_LOGGER = logging.getLogger(__name__)

def load_silero_model(
    language: str,
    model_name: str,
    download_path: str,
) -> Any:
    """
    Load a Silero TTS model (v3+ .pt format).
    Downloads models.yml and the model itself if needed.
    """

    # 1. Download or load models.yml
    models_url = "https://raw.githubusercontent.com/snakers4/silero-models/master/models.yml"
    models_file_name = "models.yml"
    models_path = os.path.join(download_path, models_file_name)

    os.makedirs(download_path, exist_ok=True)

    try:
        _LOGGER.info("Downloading latest models.yml from %s", models_url)
        torch.hub.download_url_to_file(models_url, models_path, progress=True)
    except Exception as e:
        _LOGGER.warning("Failed to download models.yml: %s", e)
        if not os.path.exists(models_path):
            raise FileNotFoundError(f"models.yml could not be downloaded and not found in {models_path}. Network required for first run.")
        _LOGGER.warning("Using cached models.yml found at %s", models_path)

    _LOGGER.debug("Loading configuration from %s", models_path)
    models_conf = OmegaConf.load(models_path)

    if language not in models_conf.tts_models:
        raise ValueError(f"Language '{language}' not found in models.yml. Available: {list(models_conf.tts_models.keys())}")

    lang_conf = models_conf.tts_models[language]
    if model_name not in lang_conf:
         raise ValueError(f"Model '{model_name}' not found for language '{language}'. Available: {list(lang_conf.keys())}")

    model_info = lang_conf[model_name].latest

    model_url = model_info.package
    if not model_url:
        raise ValueError(f"Model '{model_name}' (latest) does not have a 'package' url (likely a JIT model which is not supported).")

    model_filename = os.path.basename(model_url)
    model_file_path = os.path.join(download_path, model_filename)

    if not os.path.exists(model_file_path):
        _LOGGER.info("Downloading model from %s to %s", model_url, model_file_path)
        torch.hub.download_url_to_file(model_url, model_file_path, progress=True)
    else:
        _LOGGER.info("Model found at %s", model_file_path)

    _LOGGER.info("Loading model package...")
    importer = torch.package.PackageImporter(model_file_path)
    model = importer.load_pickle("tts_models", "model")

    device = torch.device("cpu")
    model.to(device)

    return model
