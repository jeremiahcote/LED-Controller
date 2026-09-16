import asyncio
import json
import os
import queue
import sys
import threading
import time

import sounddevice as sd
from vosk import KaldiRecognizer, Model, SetLogLevel

import LEDControllerWindows as led

SetLogLevel(-1)

MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "vosk-model-small-en-us-0.15")

# Matched as a substring against input device names, so the same config works
# on Windows and on the Pi's ALSA device list. None falls back to the default.
MIC_NAME_HINT = "TONOR"

# Vosk often finalizes the same utterance twice; without this each one queues a
# separate BLE round trip.
DUPLICATE_WINDOW = 3.0

# These strips need a moment to resume advertising after a disconnect. Without
# a gap, back-to-back commands make them drop off the air entirely.
COMMAND_COOLDOWN = 2.0

COLORS = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "cyan": (0, 255, 255),
    "purple": (255, 0, 255),
    "pink": (255, 45, 214),
    "yellow": (255, 155, 0),
    "orange": (236, 88, 0),
    "white": (255, 255, 255),
}

# Restricting the recognizer to these phrases massively improves accuracy on a
# small model. "[unk]" is what lets everything else fall through as unmatched
# instead of being forced onto the nearest command.
GRAMMAR = (
    ["lights on", "lights off", "lights dim", "lights bright"]
    + [f"lights {name}" for name in COLORS]
    + ["[unk]"]
)

last_color = (255, 255, 255)
last_brightness = 100


def find_input_device(hint):
    if hint:
        for index, device in enumerate(sd.query_devices()):
            if device["max_input_channels"] > 0 and hint.lower() in device["name"].lower():
                return index, device["name"]
    default = sd.query_devices(kind="input")
    return None, default["name"]


def parse(text):
    """Map a recognized phrase to (power, rgb, brightness), or None."""
    global last_color, last_brightness

    if not text.startswith("lights "):
        return None
    word = text[len("lights "):].strip()

    if word == "off":
        return "off", (0, 0, 0), 0

    if word == "on":
        return "on", last_color, last_brightness

    if word == "dim":
        last_brightness = 25
        return "on", last_color, last_brightness

    if word == "bright":
        last_brightness = 100
        return "on", last_color, last_brightness

    if word in COLORS:
        last_color = COLORS[word]
        return "on", last_color, last_brightness

    return None


def led_worker(commands):
    """Runs BLE commands off the audio thread so listening never stalls."""
    while True:
        command = commands.get()
        if command is None:
            return

        # A BLE round trip takes several seconds, so anything spoken meanwhile
        # is already stale. Keep only the newest instruction.
        superseded = 0
        while True:
            try:
                command = commands.get_nowait()
                superseded += 1
            except queue.Empty:
                break
        if superseded:
            print(f"  (skipping {superseded} superseded command(s))")

        power, (r, g, b), brightness = command

        try:
            asyncio.run(led.apply_from_gui(power, r, g, b, brightness))
        except Exception as e:
            print(f"  LED command failed: {e}")

        time.sleep(COMMAND_COOLDOWN)


def main():
    if not os.path.isdir(MODEL_PATH):
        sys.exit(f"Vosk model not found at {MODEL_PATH}")

    device_index, device_name = find_input_device(MIC_NAME_HINT)
    print(f"Microphone: {device_name}")

    samplerate = 16000
    try:
        sd.check_input_settings(device=device_index, samplerate=samplerate, dtype="int16")
    except Exception:
        samplerate = int(sd.query_devices(device_index, "input")["default_samplerate"])
        print(f"  16kHz unavailable, falling back to {samplerate}Hz")

    recognizer = KaldiRecognizer(Model(MODEL_PATH), samplerate, json.dumps(GRAMMAR))

    commands = queue.Queue()
    threading.Thread(target=led_worker, args=(commands,), daemon=True).start()

    audio = queue.Queue()

    def callback(indata, frames, time_info, status):
        audio.put(bytes(indata))

    print("Listening. Say e.g. 'lights on', 'lights cyan', 'lights off'. Ctrl+C to stop.")

    with sd.RawInputStream(
        samplerate=samplerate,
        blocksize=8000,
        device=device_index,
        dtype="int16",
        channels=1,
        callback=callback,
    ):
        last_text = None
        last_text_at = 0.0

        while True:
            data = audio.get()
            if not recognizer.AcceptWaveform(data):
                continue

            text = json.loads(recognizer.Result()).get("text", "")
            if not text:
                continue

            now = time.monotonic()
            if text == last_text and now - last_text_at < DUPLICATE_WINDOW:
                continue
            last_text, last_text_at = text, now

            command = parse(text)
            if command is None:
                print(f"  (ignored: {text!r})")
                continue

            print(f"heard: {text!r} -> {command[0]} rgb={command[1]} brightness={command[2]}")
            commands.put(command)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
