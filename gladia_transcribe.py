#!/usr/bin/env python3
"""
Gladia Speech-to-Text — Microphone Recorder
--------------------------------------------
Records from your mic and transcribes via Gladia API.
Free tier: 10 hours/month, no credit card required.

Install:
    pip install requests sounddevice soundfile numpy

Set your API key (Windows PowerShell):
    $env:GLADIA_API_KEY="your_key_here"

Run:
    python gladia_transcribe.py                  # press Enter to stop
    python gladia_transcribe.py --duration 10    # fixed 10-second recording
    python gladia_transcribe.py --lang hi        # Hindi (default: auto-detect)
"""

import argparse
import json
import os
import sys
import tempfile
import time
import threading

import numpy as np
import requests
import sounddevice as sd
import soundfile as sf

from dotenv import load_dotenv
load_dotenv()

# ── Configuration ─────────────────────────────────────────────────────────────
API_KEY  = os.environ.get("GLADIA_API_KEY")
BASE_URL = "https://api.gladia.io"

SAMPLE_RATE = 16_000
CHANNELS    = 1


# ── Recording ─────────────────────────────────────────────────────────────────
def record_until_enter() -> np.ndarray:
    chunks = []

    def _callback(indata, frames, t, status):
        if status:
            print(f"  [audio warning] {status}", file=sys.stderr)
        chunks.append(indata.copy())

    print("Recording ... press Enter to stop.")
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                        dtype="int16", callback=_callback):
        input()

    print("  Recording stopped.")
    return np.concatenate(chunks) if chunks else np.array([], dtype="int16")


def record_fixed(duration: int) -> np.ndarray:
    print(f"Recording for {duration}s ... speak now!")
    audio = sd.rec(int(duration * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                   channels=CHANNELS, dtype="int16")
    for i in range(duration, 0, -1):
        print(f"  {i}s ...", end="\r", flush=True)
        time.sleep(1)
    sd.wait()
    print("  Done.          ")
    return audio


def save_wav(audio: np.ndarray) -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    sf.write(tmp.name, audio, SAMPLE_RATE, subtype="PCM_16")
    return tmp.name


# ── Gladia API ────────────────────────────────────────────────────────────────
def upload_audio(wav_path: str) -> str:
    """Upload local WAV to Gladia and return the hosted audio_url."""
    print("Uploading audio to Gladia ...")
    with open(wav_path, "rb") as f:
        response = requests.post(
            f"{BASE_URL}/v2/upload",
            headers={"x-gladia-key": API_KEY},
            files={"audio": ("recording.wav", f, "audio/wav")},
        )
    if not response.ok:
        print(f"  Upload failed [{response.status_code}]: {response.text}")
        response.raise_for_status()
    audio_url = response.json()["audio_url"]
    print("  Upload complete.")
    return audio_url


def create_transcription_job(audio_url: str, language: str = None) -> tuple[str, str]:
    """Submit a transcription job. Returns (job_id, result_url)."""
    print("Submitting transcription job ...")

    payload = {
        "audio_url": audio_url,
        "diarization": False,         # set True to identify multiple speakers
        # Optional features (uncomment to enable):
        # "summarization": True,
        # "sentiment_analysis": True,
        # "named_entity_recognition": True,
    }

    # Language config
    if language:
        payload["language_config"] = {
            "languages": [language],
            "code_switching": False,
        }
    else:
        # Auto-detect language (Gladia's default)
        payload["language_config"] = {
            "languages": [],
            "code_switching": True,   # handles mid-sentence language switches
        }

    response = requests.post(
        f"{BASE_URL}/v2/pre-recorded",
        headers={
            "x-gladia-key": API_KEY,
            "Content-Type": "application/json",
        },
        json=payload,
    )
    if not response.ok:
        print(f"  Job submission failed [{response.status_code}]: {response.text}")
        response.raise_for_status()

    data = response.json()
    job_id     = data["id"]
    result_url = data["result_url"]
    print(f"  Job ID: {job_id}")
    return job_id, result_url


def poll_result(result_url: str, interval: int = 2) -> dict:
    """Poll result_url until the transcription status is 'done'."""
    print("Waiting for transcription", end="", flush=True)
    while True:
        response = requests.get(
            result_url,
            headers={"x-gladia-key": API_KEY},
        )
        response.raise_for_status()
        data   = response.json()
        status = data.get("status")

        if status == "done":
            print(" done!")
            return data
        elif status == "error":
            print()
            raise RuntimeError(f"Transcription error: {data.get('message', data)}")
        else:
            print(".", end="", flush=True)
            time.sleep(interval)


def display_results(data: dict) -> None:
    result     = data.get("result", {})
    transcript = result.get("transcription", {})
    full_text  = transcript.get("full_transcript", "")
    utterances = transcript.get("utterances", [])

    print("\n" + "=" * 60)
    print("  TRANSCRIPT  (Gladia)")
    print("=" * 60)
    print(full_text or "(no speech detected)")
    print("=" * 60)

    if utterances:
        print(f"\n  Utterances : {len(utterances)}")
        # Show detected language from the first utterance
        lang = utterances[0].get("language", "")
        if lang:
            print(f"  Language   : {lang}")

        print("\n  Utterance timestamps:")
        for u in utterances[:8]:
            start = u.get("start", 0)
            end   = u.get("end", 0)
            text  = u.get("text", "").strip()
            print(f"    [{start:.2f}s – {end:.2f}s]  {text}")
        if len(utterances) > 8:
            print(f"    ... and {len(utterances) - 8} more")

    # Save transcript
    out_txt = "transcript.txt"
    with open(out_txt, "w", encoding="utf-8") as f:
        f.write(full_text)
    print(f"\n  Transcript saved -> {out_txt}")

    # Save full JSON
    out_json = "transcript_full.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  Full JSON saved  -> {out_json}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Record your mic and transcribe with Gladia (10 hrs/month free)."
    )
    parser.add_argument("--duration", "-d", type=int, default=None, metavar="SECONDS",
                        help="Fixed recording seconds (default: press Enter to stop)")
    parser.add_argument("--lang", "-l", type=str, default=None, metavar="CODE",
                        help="Language code, e.g. en, hi, fr (default: auto-detect)")
    args = parser.parse_args()

    if API_KEY == "YOUR_GLADIA_API_KEY_HERE":
        print("Error: GLADIA_API_KEY not set.")
        print()
        print("  1. Sign up free (no credit card): https://app.gladia.io")
        print("  2. Copy your API key from the dashboard")
        print("  3. Set it:")
        print('     Windows PowerShell : $env:GLADIA_API_KEY="your_key_here"')
        print('     Mac/Linux          : export GLADIA_API_KEY="your_key_here"')
        print()
        print("  Or paste it directly into the API_KEY variable at the top of this file.")
        sys.exit(1)

    # 1. Record
    audio = record_fixed(args.duration) if args.duration else record_until_enter()
    if audio.size == 0:
        print("No audio captured. Exiting.")
        sys.exit(0)

    duration_sec = audio.shape[0] / SAMPLE_RATE
    print(f"  Captured {duration_sec:.1f}s of audio.")
    wav_path = save_wav(audio)

    try:
        # 2. Upload
        audio_url = upload_audio(wav_path)

        # 3. Submit job
        _, result_url = create_transcription_job(audio_url, language=args.lang)

        # 4. Poll for result
        data = poll_result(result_url)

        # 5. Display
        display_results(data)

    except requests.HTTPError as e:
        print(f"\nHTTP Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)
    finally:
        os.unlink(wav_path)


if __name__ == "__main__":
    main()