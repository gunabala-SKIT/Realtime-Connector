import asyncio, json, subprocess, sys
from pathlib import Path
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "pylib"))
import edge_tts
VOICE, RATE, PITCH = "en-US-ChristopherNeural", "-8%", "-3Hz"

async def one(it):
    mp3 = HERE / f"{it['id']}.mp3"
    await edge_tts.Communicate(it["say"], VOICE, rate=RATE, pitch=PITCH).save(str(mp3))
    subprocess.run([str(HERE / "ffmpeg.exe"), "-y", "-loglevel", "error", "-i", str(mp3), "-ar", "44100", "-ac", "1", "-sample_fmt", "s16", str(HERE / f"{it['id']}.wav")], check=True)
    mp3.unlink()

async def main(items):
    for it in items:
        await one(it)
        print("ok", it["id"], flush=True)

if __name__ == "__main__":
    asyncio.run(main(json.load(open(sys.argv[1], encoding="utf8"))))
