from setuptools import setup, find_packages

setup(
    name="wyoming-silero",
    version="1.0.0",
    description="Wyoming Server for Silero TTS",
    author="staastas",
    packages=find_packages(),
    install_requires=[
        "wyoming>=1.0.0",
        "torch>=2.0.0",
        "torchaudio>=2.0.0",
        "numpy",
        "soundfile",
        "scipy",
        "omegaconf",
        "PyYAML",
    ],
    entry_points={
        "console_scripts": [
            "wyoming-silero = wyoming_silero.__main__:run"
        ]
    },
)
