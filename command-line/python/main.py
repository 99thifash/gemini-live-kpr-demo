import asyncio
import math
import os
import time
from array import array
from pathlib import Path

from google import genai
import pyaudio

# SDK 3.x defaults to api_version "v1", where the Live endpoint returns 404.
client = genai.Client(http_options={"api_version": "v1beta"})

# --- pyaudio config ---
FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024

pya = pyaudio.PyAudio()

# --- Live API config ---
MODEL = "gemini-3.8-live"
CONFIG = {
    "response_modalities": ["AUDIO"],
    "system_instruction": Path("prompt_kpr.txt").read_text(encoding="utf-8"),
    "speech_config": {
        "voice_config": {"prebuilt_voice_config": {"voice_name": "Kore"}}
    },
    "output_audio_transcription": {},
    "input_audio_transcription": {},
    # End-of-speech detection: treat ~0.6 s of silence as the end of the user's turn.
    "realtime_input_config": {
        "automatic_activity_detection": {
            # HIGH = quieter speech still counts as the start of the user's turn
            "start_of_speech_sensitivity": "START_SENSITIVITY_HIGH",
            "end_of_speech_sensitivity": "END_SENSITIVITY_HIGH",
            "silence_duration_ms": 600,
        }
    },
}

# Show the mic level once per second: MIC_DEBUG=1 ./run.sh skenario1
MIC_DEBUG = os.getenv("MIC_DEBUG") == "1"
# Software mic boost for a quiet laptop mic: MIC_GAIN=3 ./run.sh skenario1
MIC_GAIN = float(os.getenv("MIC_GAIN", "1"))


def apply_gain(data):
    """Multiplies 16-bit PCM samples by MIC_GAIN, clipped to the int16 range."""
    if MIC_GAIN == 1:
        return data
    samples = array("h", data)
    for i, s in enumerate(samples):
        samples[i] = max(-32768, min(32767, int(s * MIC_GAIN)))
    return samples.tobytes()

# Price per 1M tokens (USD), Gemini 3.8 Live paid tier, Nov 2026.
PRICE_PER_M = {"AUDIO_IN": 3.00, "TEXT_IN": 0.75, "AUDIO_OUT": 12.00, "TEXT_OUT": 4.50}
token_totals = {"AUDIO_IN": 0, "TEXT_IN": 0, "AUDIO_OUT": 0, "TEXT_OUT": 0, "OTHER": 0}
session_start = None

audio_queue_output = asyncio.Queue()
audio_queue_mic = asyncio.Queue(maxsize=5)
audio_stream = None


def add_usage(details, direction):
    """Adds per-modality token counts from one usage report to the running totals."""
    for d in details or []:
        modality = d.modality.value if hasattr(d.modality, "value") else str(d.modality)
        key = f"{modality}_{direction}"
        token_totals[key if key in token_totals else "OTHER"] += d.token_count or 0


def print_cost_summary():
    """Prints total tokens and the estimated cost of this session."""
    cost = sum(token_totals[k] * PRICE_PER_M[k] / 1_000_000 for k in PRICE_PER_M)
    minutes = (time.monotonic() - session_start) / 60 if session_start else 0
    print("\n===== RINGKASAN BIAYA SESI =====")
    for k, v in token_totals.items():
        print(f"{k:10s}: {v:,} token")
    print(f"Durasi    : {minutes:.2f} menit")
    print(f"Estimasi  : ${cost:.4f}" + (f"  (${cost / minutes:.4f}/menit)" if minutes > 0 else ""))
    print("Cocokkan dengan halaman Usage di AI Studio.")


async def listen_audio():
    """Listens for audio and puts it into the mic audio queue."""
    global audio_stream
    mic_info = pya.get_default_input_device_info()
    print(f"Mic: {mic_info['name']}")
    audio_stream = await asyncio.to_thread(
        pya.open,
        format=FORMAT,
        channels=CHANNELS,
        rate=SEND_SAMPLE_RATE,
        input=True,
        input_device_index=mic_info["index"],
        frames_per_buffer=CHUNK_SIZE,
    )
    kwargs = {"exception_on_overflow": False} if __debug__ else {}
    last_level_print = 0.0
    while True:
        data = await asyncio.to_thread(audio_stream.read, CHUNK_SIZE, **kwargs)
        data = apply_gain(data)
        if MIC_DEBUG and time.monotonic() - last_level_print > 1:
            samples = array("h", data)
            rms = math.sqrt(sum(s * s for s in samples) / len(samples)) if samples else 0
            print(f"[mic] level={int(rms)}", flush=True)
            last_level_print = time.monotonic()
        await audio_queue_mic.put({"data": data, "mime_type": "audio/pcm"})


async def send_realtime(session):
    """Sends audio from the mic audio queue to the GenAI session."""
    while True:
        msg = await audio_queue_mic.get()
        await session.send_realtime_input(audio=msg)


async def receive_audio(session):
    """Receives responses from GenAI and puts audio data into the speaker audio queue."""
    last_was_input = False
    while True:
        turn = session.receive()
        async for response in turn:
            # Token usage, for per-call cost calculation
            if response.usage_metadata:
                u = response.usage_metadata
                add_usage(u.prompt_tokens_details, "IN")
                add_usage(u.response_tokens_details, "OUT")

            sc = response.server_content
            if not sc:
                continue
            if sc.interrupted:
                # User spoke over Lia: drop the audio that has not been played yet
                while not audio_queue_output.empty():
                    audio_queue_output.get_nowait()
                print("\n[dipotong oleh user]", flush=True)
            if sc.model_turn:
                for part in sc.model_turn.parts:
                    if part.inline_data and isinstance(part.inline_data.data, bytes):
                        audio_queue_output.put_nowait(part.inline_data.data)
            if sc.output_transcription:
                if last_was_input:
                    print()
                    last_was_input = False
                t = sc.output_transcription.text
                print(t, end="", flush=True)
                if t.rstrip()[-1:] in ".!?":
                    print()
            if sc.input_transcription:
                if not last_was_input:
                    print()
                    last_was_input = True
                t = sc.input_transcription.text
                print(f"\033[3m{t}\033[0m", end="", flush=True)
                if t.rstrip()[-1:] in ".!?":
                    print()
            if sc.turn_complete:
                print("\n[giliran Lia selesai, silakan bicara]", flush=True)


async def play_audio():
    """Plays audio from the speaker audio queue."""
    stream = await asyncio.to_thread(
        pya.open,
        format=FORMAT,
        channels=CHANNELS,
        rate=RECEIVE_SAMPLE_RATE,
        output=True,
    )
    while True:
        bytestream = await audio_queue_output.get()
        await asyncio.to_thread(stream.write, bytestream)


async def run():
    """Main function to run the audio loop."""
    global session_start
    try:
        async with client.aio.live.connect(model=MODEL, config=CONFIG) as live_session:
            session_start = time.monotonic()
            print("Connected to Gemini. Lia akan menyapa duluan...", flush=True)
            # Like a real outbound call: the agent opens as soon as the call is picked up.
            await live_session.send_realtime_input(
                text="[Telepon baru saja diangkat oleh nasabah. Mulai dari LANGKAH 1 sekarang.]"
            )
            async with asyncio.TaskGroup() as tg:
                tg.create_task(send_realtime(live_session))
                tg.create_task(listen_audio())
                tg.create_task(receive_audio(live_session))
                tg.create_task(play_audio())
    except asyncio.CancelledError:
        pass
    finally:
        if audio_stream:
            audio_stream.close()
        pya.terminate()
        try:
            print_cost_summary()
            print("\nConnection closed.", flush=True)
        except BrokenPipeError:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("Interrupted by user.")