"""Docker health check: the server is healthy when it answers Describe with Info."""
import asyncio
import os
import sys
from urllib.parse import urlparse

from wyoming.client import AsyncClient
from wyoming.info import Describe, Info

TIMEOUT = 5


def server_uri() -> str:
    """Address of the local server, built from the same settings it uses."""
    uri = os.getenv("WYOMING_URI") or f"tcp://127.0.0.1:{os.getenv('WYOMING_PORT', '10200')}"
    parsed = urlparse(uri)
    if parsed.scheme == "tcp" and parsed.hostname in ("0.0.0.0", "::", None):
        uri = f"tcp://127.0.0.1:{parsed.port}"
    return uri


async def check(uri: str) -> bool:
    async with AsyncClient.from_uri(uri) as client:
        await client.write_event(Describe().event())
        while True:
            event = await client.read_event()
            if event is None:
                return False
            if Info.is_type(event.type):
                return bool(Info.from_event(event).tts)


def main() -> int:
    try:
        healthy = asyncio.run(asyncio.wait_for(check(server_uri()), TIMEOUT))
    except Exception as e:
        print(f"Unhealthy: {e!r}", file=sys.stderr)
        return 1
    if not healthy:
        print("Unhealthy: server did not describe a TTS program", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
