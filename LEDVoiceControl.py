# Imported first, on purpose. Its first lines put the process in the COM
# apartment bleak's WinRT backend needs, and that has to happen before
# anything else initializes COM -- sounddevice does, for audio device
# enumeration. With the imports the other way round LED 2 dropped the link
# mid-write on essentially every command.
import LEDControllerWindows as led

import asyncio
import json
import os
import queue
import sys
import threading
import time

from vosk import KaldiRecognizer, Model, SetLogLevel

# NOTE: sounddevice is deliberately *not* imported here. Importing it
# initializes COM as STA on whichever thread does the import, and bleak's
# WinRT backend requires MTA on the thread running BLE. Importing it at module
# level left the main thread as MAIN_STA, so bleak refused to work there
# ("Thread is configured for Windows GUI but callbacks are not working") and
# was unreliable from a worker thread. It's imported inside listen_loop so
# PortAudio's COM init lands on that thread instead.

SetLogLevel(-1)

MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "vosk-model-small-en-us-0.15")

# Tried in order, each matched as a substring against input device names, so
# the same config works on Windows and on the Pi's ALSA device list. Falls back
# to the system default if none are connected.
MIC_NAME_HINTS = ["DualSense", "TONOR"]

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

# Checked before the single words, so "light blue" doesn't match plain "blue".
COMPOUND_COLORS = {
    "light blue": (0, 255, 255),
    "sky blue": (0, 255, 255),
}

# Which strip a phrase refers to. Anything else addresses both.
TARGETS = {"bed": "led1", "wall": "led2"}

# Restricting the recognizer to these phrases massively improves accuracy on a
# small model. "[unk]" is what lets everything else fall through as unmatched
# instead of being forced onto the nearest command.
# Phrases that map straight to a scene, addressing both strips.
PHRASES = {
    "i'm home": ("on", COLORS["cyan"]),
    "good morning": ("on", COLORS["cyan"]),
    "good night": ("off", (0, 0, 0)),
    "bye": ("off", (0, 0, 0)),
}

# A grammar of individual words rather than whole phrases, so any wording
# built from them is recognized and commands can be picked out by keyword --
# "bed lights red" and "turn the bed lights on red" both work. FILLERS aren't
# acted on, they just need to be recognizable so they don't force the
# recognizer to mangle the words around them.
FILLERS = ["turn", "the", "to", "make", "set", "please", "all", "light"]

GRAMMAR = (
    ["lights", "on", "off"]
    + FILLERS
    + list(TARGETS)
    + list(COLORS)
    + [word for phrase in COMPOUND_COLORS for word in phrase.split()]
    + list(PHRASES)
    + ["[unk]"]
)

last_color = (255, 255, 255)


def find_input_device(sd, hints):
    devices = list(enumerate(sd.query_devices()))
    for hint in hints:
        for index, device in devices:
            if device["max_input_channels"] > 0 and hint.lower() in device["name"].lower():
                return index, device["name"]
    default = sd.query_devices(kind="input")
    return None, default["name"]


def parse(text):
    """Map recognized speech to (power, rgb, target), or None."""
    global last_color

    if text in PHRASES:
        power, rgb = PHRASES[text]
        if power == "on":
            last_color = rgb
        return power, rgb, "both"

    words = text.split()

    # Require an explicit mention of the lights. Without it, this loose a
    # grammar would fire on ordinary conversation containing "on" or "red".
    if "lights" not in words:
        return None

    target = "both"
    for word, name in TARGETS.items():
        if word in words:
            target = name
            break

    color = None
    for phrase, rgb in COMPOUND_COLORS.items():
        if phrase in text:
            color = rgb
            break
    if color is None:
        for name, rgb in COLORS.items():
            if name in words:
                color = rgb
                break

    if color is not None:
        last_color = color
        return "on", color, target

    if "off" in words:
        return "off", (0, 0, 0), target

    if "on" in words:
        return "on", last_color, target

    return None


def listen_loop(commands):
    """Capture audio and recognize speech, queueing commands for the main thread.

    Listening runs here rather than the BLE work because of the COM apartment
    constraint described at the imports: sounddevice has to initialize COM on
    some thread, and it must not be the one bleak uses.
    """
    import sounddevice as sd

    device_index, device_name = find_input_device(sd, MIC_NAME_HINTS)
    print(f"Microphone: {device_name}")

    samplerate = 16000
    try:
        sd.check_input_settings(device=device_index, samplerate=samplerate, dtype="int16")
    except Exception:
        samplerate = int(sd.query_devices(device_index, "input")["default_samplerate"])
        print(f"  16kHz unavailable, falling back to {samplerate}Hz")

    recognizer = KaldiRecognizer(Model(MODEL_PATH), samplerate, json.dumps(GRAMMAR))

    audio = queue.Queue()

    def callback(indata, frames, time_info, status):
        audio.put(bytes(indata))

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

            print(f"heard: {text!r} -> {command[0]} rgb={command[1]} target={command[2]}")
            commands.put(command)


async def command_loop(commands):
    """Drives the strips on the main thread, newest command wins."""
    while True:
        try:
            command = commands.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.05)
            continue

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

        power, (r, g, b), target = command

        try:
            # A queued command means something newer was said, so stop the
            # one in flight rather than making the user wait it out.
            await led.apply_from_gui(
                power, r, g, b, target,
                should_abort=lambda: not commands.empty(),
            )
        except led.Aborted:
            print("  interrupted by a newer command")
            # The interrupted command already disconnected cleanly, so go
            # straight to the new one instead of sitting out the cooldown.
            continue
        except Exception as e:
            print(f"  LED command failed: {e}")

        await asyncio.sleep(COMMAND_COOLDOWN)


def main():
    if not os.path.isdir(MODEL_PATH):
        sys.exit(f"Vosk model not found at {MODEL_PATH}")

    commands = queue.Queue()
    threading.Thread(target=listen_loop, args=(commands,), daemon=True).start()

    print("Listening. Say e.g. 'lights on', 'lights cyan', 'lights off'. Ctrl+C to stop.")

    asyncio.run(command_loop(commands))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
