#!/bin/bash
set -e

MODEL_DIR=/app/silero/model

if [ "$(id -u)" = "0" ]; then
    # Docker creates a missing bind-mount directory as root, which the
    # silero user cannot write the downloaded models to.
    mkdir -p "$MODEL_DIR"
    if ! chown -R silero:silero "$MODEL_DIR" 2>/dev/null; then
        echo "WARNING: could not change ownership of $MODEL_DIR; model downloads may fail" >&2
    fi
    exec setpriv --reuid=silero --regid=silero --init-groups \
        env HOME=/home/silero python3 -m wyoming_silero "$@"
fi

# Started with an explicit non-root user (e.g. "user:" in docker-compose):
# the model directory must already be writable by that user.
exec python3 -m wyoming_silero "$@"
