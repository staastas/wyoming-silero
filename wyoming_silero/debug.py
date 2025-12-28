
import asyncio
import json
from wyoming.client import AsyncClient
from wyoming.info import Describe, Info

async def main():
    uri = "tcp://0.0.0.0:10200"
    print(f"Connecting to {uri}...")
    try:
        async with AsyncClient.from_uri(uri) as client:
            print("Connected. Sending Describe...")
            await client.write_event(Describe().event())

            while True:
                event = await client.read_event()
                if event is None:
                    print("Disconnected.")
                    break

                if Info.is_type(event.type):
                    info = Info.from_event(event)
                    print("\nReceived Info:")
                    print(json.dumps(event.data, indent=2, ensure_ascii=False))

                    if info.tts:
                        print(f"\nTTS Programs: {len(info.tts)}")
                        for prog in info.tts:
                            print(f"  Program: {prog.name}")
                            print(f"  Voices: {len(prog.voices)}")
                            for voice in prog.voices:
                                print(f"    - Name: {voice.name}, Lang: {voice.languages}")
                    else:
                        print("No TTS programs found in Info.")
                    break
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
