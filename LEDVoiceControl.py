# Imported first, on purpose. Its first lines put the process in the COM
# apartment bleak's WinRT backend needs, and that has to happen before
# anything else initializes COM -- sounddevice does, for audio device
# enumeration. With the imports the other way round LED 2 dropped the link
# mid-write on essentially every command.
import LEDControllerWindows as led

import asyncio
import contextlib
import json
import os
import queue
import sys
import threading
import time

from vosk import KaldiRecognizer, Model, SetLogLevel

import NaviWeb
import PCControl

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

# Nothing is acted on unless it follows the wake word, either in the same
# phrase ("navi, wall lights red") or within WAKE_WINDOW seconds of hearing it
# on its own. "navi" is in the small model's vocabulary and was recognized
# consistently in testing, with no confusion with "navy" or "nobby".
WAKE_WORD = "navi"
WAKE_WINDOW = 6.0

AUDIO_STALL_TIMEOUT = 10.0

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

# Mentioning one of these makes the command about the PC rather than the lights.
PC_WORDS = ["computer", "pc"]
# All of these need the unlock passphrase (see UNLOCK_PASSPHRASE below) -- there's
# no passphrase-free way to wake the PC by voice.
PC_ON_WORDS = ["on", "wake", "start", "boot", "unlock"]
# Shutting down needs one of these word pairs plus a PC word ("shut down my
# computer", "turn my PC off"). A lone "off" or "shut" isn't enough, since a
# false match closes everything on the PC.
PC_SHUTDOWN_PAIRS = [("shut", "down"), ("turn", "off")]

# Locks Navi so it acts on nothing but "Navi, unlock controls" -- for when
# someone else is in the room and shouldn't be able to touch the lights or PC.
# Not password protected: it's meant to block casual use, not a determined
# adversary, and the point is it's quick to say.
LOCKDOWN_WORD = "lockdown"
UNLOCK_CONTROLS_WORDS = ("unlock", "controls")

# A voice shutdown or PC unlock/wake also has to include a passphrase, so
# someone else in the room can't do it. Each lives only on the machine running
# Navi -- this repository is public -- as one line of plain words in its own
# file. Without the file, that voice action is disabled (the web interface's
# token-protected buttons still work).
SHUTDOWN_PASSPHRASE_PATH = os.path.expanduser("~/.config/navi/shutdown_passphrase")
UNLOCK_PASSPHRASE_PATH = os.path.expanduser("~/.config/navi/unlock_passphrase")


def load_passphrase(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().lower().split()
    except OSError:
        return []


SHUTDOWN_PASSPHRASE = load_passphrase(SHUTDOWN_PASSPHRASE_PATH)
UNLOCK_PASSPHRASE = load_passphrase(UNLOCK_PASSPHRASE_PATH)


def has_passphrase(words, passphrase):
    # Compared with spaces removed, since the recognizer may split a word the
    # way it's written in the file ("sunflower" -> "sun flower") or join one.
    # Not anchored to a position in the utterance, so the passphrase can be
    # said before or after the rest of the command.
    return bool(passphrase) and "".join(passphrase) in "".join(words)


def masked(text):
    """Text safe to log: any word that is, or is part of, a passphrase is hidden."""
    secrets = ["".join(p) for p in (SHUTDOWN_PASSPHRASE, UNLOCK_PASSPHRASE) if p]
    if not secrets:
        return text
    return " ".join(
        "***" if len(w) >= 3 and any(w in secret for secret in secrets) else w
        for w in text.split()
    )


# While the PC counts down to shutting down, the bed lights flash red for
# FLASH_SECONDS at each of these points (seconds after the request) and then go
# back to what they were showing. It's the warning you'll notice mid-game,
# where Windows' own notification is hidden.
SHUTDOWN_FLASHES_AT = (0, 15)
FLASH_SECONDS = 2.0

# What Navi last set each strip to, so a flash can put it back. Only Navi's own
# commands are tracked; changes from the phone app aren't seen.
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "navi_state.json")
DEFAULT_STRIP_STATE = {"power": "on", "rgb": [255, 255, 255]}

# Phrases that map straight to a scene, addressing both strips. Kept to
# multi-word phrases: a lone "bye" got inserted by the recognizer into a
# command that never said it, and turned the lights off. "goodbye" is matched
# both ways because the model can return it as one word or two.
PHRASES = {
    "i'm home": ("on", COLORS["cyan"]),
    "good morning": ("on", COLORS["cyan"]),
    "good night": ("off", (0, 0, 0)),
    "goodbye": ("off", (0, 0, 0)),
    "good bye": ("off", (0, 0, 0)),
}

# Restricting the recognizer to a known vocabulary massively improves accuracy
# on a small model; "[unk]" lets everything else fall through as unmatched
# instead of being forced onto the nearest word. It's a list of individual
# words rather than whole phrases, so any wording built from them is recognized
# and commands can be picked out by keyword -- "bed lights red" and "turn the
# bed lights on red" both work. FILLERS aren't acted on, they just need to be
# recognizable so they don't force the recognizer to mangle the words around
# them.
FILLERS = ["turn", "the", "to", "make", "set", "please", "all", "light", "lights",
           "my", "up", "down"]

_GRAMMAR_WORDS = (
    [WAKE_WORD, "on", "off", "cancel", LOCKDOWN_WORD]
    + FILLERS
    + PC_WORDS
    + PC_ON_WORDS
    + list(UNLOCK_CONTROLS_WORDS)
    + ["shut"]
    + list(TARGETS)
    + list(COLORS)
    + [word for phrase in COMPOUND_COLORS for word in phrase.split()]
    + list(PHRASES)
    + SHUTDOWN_PASSPHRASE
    + UNLOCK_PASSPHRASE
)
# PC_ON_WORDS/UNLOCK_CONTROLS_WORDS overlap with the base list and each other
# ("on", "unlock"); Vosk's grammar mode wants each word once.
GRAMMAR = list(dict.fromkeys(_GRAMMAR_WORDS)) + ["[unk]"]

last_color = (255, 255, 255)


def find_input_device(sd, hints):
    devices = list(enumerate(sd.query_devices()))
    for hint in hints:
        for index, device in devices:
            if device["max_input_channels"] > 0 and hint.lower() in device["name"].lower():
                return index, device["name"]
    default = sd.query_devices(kind="input")
    return None, default["name"]


def after_wake_word(text):
    """Words following the last wake word, or None if it wasn't said.

    The last occurrence is used because the recognizer merges speech without a
    clear pause into one result ("navi ... navi wall lights red").
    """
    words = text.split()
    if WAKE_WORD not in words:
        return None
    last = len(words) - 1 - words[::-1].index(WAKE_WORD)
    return " ".join(w for w in words[last + 1:] if w != "[unk]")


def parse(text):
    """Map a command (the words after the wake word) to an action, or None.

    Returns ("lights", power, rgb, target), ("pc_on",), ("shutdown",),
    ("cancel",), ("lockdown",) or ("unlock_controls",).
    """
    global last_color

    words = text.split()

    # Checked first, and above the lockdown gate in the caller: it has to work
    # no matter what else was heard, or a lockdown could never be lifted.
    if all(w in words for w in UNLOCK_CONTROLS_WORDS):
        return ("unlock_controls",)

    # Checked next: when in doubt, cancelling is the safe reading.
    if "cancel" in words:
        return ("cancel",)

    if LOCKDOWN_WORD in words:
        return ("lockdown",)

    if any(w in words for w in PC_WORDS):
        if any(a in words and b in words for a, b in PC_SHUTDOWN_PAIRS):
            return ("shutdown",) if has_passphrase(words, SHUTDOWN_PASSPHRASE) else ("shutdown_denied",)
        if "shut" in words or "off" in words:
            # Sounds like a shutdown but doesn't meet the bar above; don't fall
            # through and treat it as "on".
            return None
        if any(w in words for w in PC_ON_WORDS):
            return ("pc_on",) if has_passphrase(words, UNLOCK_PASSPHRASE) else ("pc_on_denied",)
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

    # An explicit colour beats a scene phrase: a stray phrase word is far
    # likelier to be a misrecognition than a colour someone actually said.
    if color is not None:
        last_color = color
        return "lights", "on", color, target

    padded = f" {text} "
    for phrase, (power, rgb) in PHRASES.items():
        if f" {phrase} " in padded:
            if power == "on":
                last_color = rgb
            return "lights", power, rgb, "both"

    if "off" in words:
        return "lights", "off", (0, 0, 0), target

    if "on" in words:
        return "lights", "on", last_color, target

    return None


def load_strip_states():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


strip_states = load_strip_states()
# The web interface reads these from its own thread.
strip_states_lock = threading.Lock()


def strip_state(strip):
    with strip_states_lock:
        state = strip_states.get(strip, DEFAULT_STRIP_STATE)
        return state["power"], tuple(state["rgb"])


def snapshot_strip_states():
    with strip_states_lock:
        return {name: dict(state) for name, state in strip_states.items()}


def remember_strip_state(target, power, rgb):
    with strip_states_lock:
        for strip in (("led1", "led2") if target == "both" else (target,)):
            # last_rgb survives turning off, so the web page can show (and "on"
            # can restore) the colour a strip had before.
            previous = strip_states.get(strip, DEFAULT_STRIP_STATE)
            last_rgb = (list(rgb) if power == "on" and any(rgb)
                        else previous.get("last_rgb", previous["rgb"]))
            strip_states[strip] = {"power": power, "rgb": list(rgb), "last_rgb": last_rgb}
        snapshot = json.dumps(strip_states)
    tmp = STATE_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(snapshot)
        os.replace(tmp, STATE_PATH)
    except OSError as e:
        print(f"  couldn't save light state: {e}")


class BleGate:
    """One Bluetooth operation at a time, spaced COMMAND_COOLDOWN apart."""

    class Slot:
        cooldown = True

    def __init__(self):
        self._lock = asyncio.Lock()
        self._ready_at = 0.0

    @contextlib.asynccontextmanager
    async def use(self):
        async with self._lock:
            wait = self._ready_at - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            slot = BleGate.Slot()
            try:
                yield slot
            finally:
                self._ready_at = time.monotonic() + (COMMAND_COOLDOWN if slot.cooldown else 0)


def qhm_state_commands(power, rgb):
    if power == "on":
        return [(led.qhm_color(*rgb), 0.2), (led.qhm_on(), 0.2), (led.qhm_color(*rgb), 0.3)]
    return [(led.qhm_off(), 0.3)]


FLASH_COLOR = (255, 0, 0)


async def flash_bed_lights(ble):
    """Flash the bed lights for FLASH_SECONDS, then restore their last known state.

    The flash is red, or off if the lights are already red, so it's visible
    either way.
    """
    power, rgb = strip_state("led1")
    hold_writes = 4
    hold = (FLASH_SECONDS - 0.2) / hold_writes
    if power == "on" and rgb == FLASH_COLOR:
        off = led.qhm_off()
        commands = [(off, 0.1), (off, 0.1)]
        # Repeated writes rather than a sleep: these strips drop an idle link
        # quickly, and losing it here would leave the lights stuck off.
        commands += [(off, hold)] * hold_writes
    else:
        red = led.qhm_color(*FLASH_COLOR)
        commands = [(red, 0.1), (led.qhm_on(), 0.1)]
        # Same reason: keep the link busy so it can't drop while red.
        commands += [(red, hold)] * hold_writes
    commands += qhm_state_commands(power, rgb)

    async with ble.use():
        try:
            await led.send_with_retries("LED 1 flash", led.ADDRESS, led.CHAR_UUID, commands)
            return
        except Exception as e:
            print(f"  flash failed ({e}); restoring bed lights")
        try:
            await led.send_with_retries("LED 1 restore", led.ADDRESS, led.CHAR_UUID,
                                        qhm_state_commands(power, rgb))
        except Exception as e:
            print(f"  couldn't restore bed lights: {e}")


class ShutdownCountdown:
    """Runs the PC shutdown request and its warning flashes; supports cancel."""

    def __init__(self, ble):
        self.ble = ble
        self.task = None
        self.cancelled = asyncio.Event()
        self.requested_at = None

    def pending(self):
        """Whether the PC is counting down to a shutdown Navi started."""
        return (self.requested_at is not None
                and not self.cancelled.is_set()
                and time.monotonic() - self.requested_at < PCControl.SHUTDOWN_DELAY)

    def start(self):
        if self.task and not self.task.done():
            print("  shutdown already counting down")
            return
        self.cancelled = asyncio.Event()
        self.requested_at = None
        self.task = asyncio.create_task(self._run(self.cancelled))

    def cancel(self):
        # Setting the event stops future flashes. A flash already running is
        # left to finish: interrupting a BLE session midway is what used to
        # wedge the strips, and the flash restores the lights on its own.
        self.cancelled.set()
        asyncio.create_task(self._send_cancel())

    async def _send_cancel(self):
        ok, message = await asyncio.to_thread(PCControl.cancel_pc_shutdown)
        print(f"  cancel: {message}" if ok else f"  cancel FAILED: {message}")

    async def _run(self, cancelled):
        started = time.monotonic()
        ok, message = await asyncio.to_thread(PCControl.shutdown_pc)
        if not ok:
            print(f"  PC shutdown FAILED: {message}")
            return
        print(f"  PC shutdown: {message}")
        self.requested_at = started
        if cancelled.is_set():
            # "cancel" arrived while the request was still in flight, so its own
            # abort may have run before there was anything to abort.
            await self._send_cancel()
            return

        for at in SHUTDOWN_FLASHES_AT:
            wait = at - (time.monotonic() - started)
            if wait > 0:
                try:
                    await asyncio.wait_for(cancelled.wait(), wait)
                    return
                except asyncio.TimeoutError:
                    pass
            if cancelled.is_set():
                return
            await flash_bed_lights(self.ble)


def listen_loop(commands):
    try:
        _listen(commands)
    except Exception as e:
        # This runs on a daemon thread, so an uncaught error would leave the
        # process alive but deaf. Exit instead, so it gets restarted.
        print(f"  listener crashed: {e!r}")
        os._exit(1)


def _listen(commands):
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
        awake_until = 0.0
        # While set, every command but "Navi, unlock controls" is ignored.
        locked_down = False

        while True:
            try:
                data = audio.get(timeout=AUDIO_STALL_TIMEOUT)
            except queue.Empty:
                # A live stream delivers a block several times a second, so
                # silence here means the mic went away (e.g. the controller was
                # unplugged). PortAudio doesn't raise in that case, so bail out
                # and let the service manager restart us against a fresh device.
                print("  no audio from the mic, exiting so the service can restart")
                os._exit(1)

            if not recognizer.AcceptWaveform(data):
                continue

            text = json.loads(recognizer.Result()).get("text", "")
            if not text:
                continue

            now = time.monotonic()
            if text == last_text and now - last_text_at < DUPLICATE_WINDOW:
                continue
            last_text, last_text_at = text, now

            request = after_wake_word(text)
            if request is None:
                if now > awake_until:
                    print(f"  (no wake word, ignored: {masked(text)!r})")
                    continue
                # Follow-up to a bare "navi" said moments ago.
                request = " ".join(w for w in text.split() if w != "[unk]")

            if not request:
                awake_until = now + WAKE_WINDOW
                print(f"{WAKE_WORD}: listening...")
                continue

            awake_until = 0.0
            command = parse(request)
            if command is None:
                print(f"  (not a command: {masked(text)!r})")
                continue

            if command[0] == "unlock_controls":
                # Handled here regardless of locked_down: it's the only way out.
                was_locked, locked_down = locked_down, False
                print(f"heard: {masked(text)!r} -> "
                      f"{'lockdown lifted' if was_locked else 'unlock controls (not locked)'}")
                continue

            if locked_down:
                print(f"  (locked down, ignored: {masked(text)!r})")
                continue

            if command[0] == "lockdown":
                locked_down = True
                print(f"heard: {masked(text)!r} -> lockdown engaged")
                continue

            if command[0] == "shutdown_denied":
                # Deliberately doesn't echo what was heard, so the log doesn't
                # help anyone guess the passphrase.
                print("heard a PC shutdown request without the passphrase; ignored")
                continue

            if command[0] == "pc_on_denied":
                print("heard a PC wake/unlock request without the passphrase; ignored")
                continue

            if command[0] == "pc_on":
                # Handled right here: it's a single UDP send, no Bluetooth.
                PCControl.wake_pc()
                print(f"heard: {masked(text)!r} -> sent wake packet to the PC")
                continue

            if command[0] == "lights":
                _, power, rgb, target = command
                print(f"heard: {masked(text)!r} -> {power} rgb={rgb} target={target}")
            else:
                print(f"heard: {masked(text)!r} -> {command[0]}")
            commands.put(command)


async def run_light_command(command, ble, should_abort):
    _, power, (r, g, b), target = command
    async with ble.use() as slot:
        try:
            await led.apply_from_gui(power, r, g, b, target, should_abort=should_abort)
        except led.Aborted:
            print("  interrupted by a newer command")
            # The interrupted command already disconnected cleanly, so go
            # straight to the new one instead of sitting out the cooldown.
            slot.cooldown = False
            return
        except Exception as e:
            print(f"  LED command failed: {e}")
            return
    remember_strip_state(target, power, (r, g, b))


async def command_loop(commands, shared):
    """Runs everything that touches Bluetooth, on the main thread.

    Light commands: newest wins, and a newer one interrupts the one in flight.
    Shutdown and cancel are never dropped or interrupted by light commands.
    Commands arrive from the voice listener and the web interface alike.
    """
    ble = BleGate()
    shutdown = ShutdownCountdown(ble)
    shared["shutdown"] = shutdown
    pending = {"light": None}
    light_task = None

    while True:
        while True:
            try:
                command = commands.get_nowait()
            except queue.Empty:
                break
            if command[0] == "shutdown":
                shutdown.start()
            elif command[0] == "cancel":
                shutdown.cancel()
            else:
                if pending["light"] is not None:
                    print("  (skipping a superseded light command)")
                pending["light"] = command

        if pending["light"] is not None and (light_task is None or light_task.done()):
            command, pending["light"] = pending["light"], None
            light_task = asyncio.create_task(run_light_command(
                command, ble,
                # A newer light command waiting means this one is stale.
                should_abort=lambda: pending["light"] is not None,
            ))

        await asyncio.sleep(0.05)


def _last_shown_color(strips, names):
    for name in names:
        state = strips.get(name, DEFAULT_STRIP_STATE)
        rgb = tuple(state.get("last_rgb", state["rgb"]))
        if any(rgb):
            return rgb
    return tuple(DEFAULT_STRIP_STATE["rgb"])


def web_state(shared):
    strips = snapshot_strip_states()
    shutdown = shared.get("shutdown")
    # Read once: a cancel can clear it from another thread at any moment.
    requested_at = shutdown.requested_at if shutdown else None
    pending = requested_at is not None and bool(shutdown.pending())
    return {
        "strips": {
            "bed": strips.get("led1", DEFAULT_STRIP_STATE),
            "wall": strips.get("led2", DEFAULT_STRIP_STATE),
        },
        "shutdown_pending": pending,
        "shutdown_seconds_left": (
            max(0, round(PCControl.SHUTDOWN_DELAY - (time.monotonic() - requested_at)))
            if pending else None),
        # What "on" with no colour should use for each target.
        "last_color": {
            "led1": _last_shown_color(strips, ["led1"]),
            "led2": _last_shown_color(strips, ["led2"]),
            "both": _last_shown_color(strips, ["led2", "led1"]),
        },
    }


def main():
    if not os.path.isdir(MODEL_PATH):
        sys.exit(f"Vosk model not found at {MODEL_PATH}")

    commands = queue.Queue()
    shared = {}
    threading.Thread(target=listen_loop, args=(commands,), daemon=True).start()
    NaviWeb.start(
        submit=commands.put,
        get_state=lambda: web_state(shared),
        wake_pc=PCControl.wake_pc,
        colors=COLORS,
    )

    print(f"Listening. Say e.g. '{WAKE_WORD}, wall lights red' or '{WAKE_WORD}, good night'. Ctrl+C to stop.")

    asyncio.run(command_loop(commands, shared))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
