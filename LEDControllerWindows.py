# Must be first lines (helps Bleak's WinRT backend on Windows)
import sys
sys.coinit_flags = 0
try:
    from bleak.backends.winrt.util import uninitialize_sta
    try:
        uninitialize_sta()
    except Exception:
        pass
except Exception:
    pass


import asyncio
from bleak import BleakClient, BleakScanner
from bleak.exc import BleakBluetoothNotAvailableError
import requests


# Windows Bluetooth addresses are real MACs (XX:XX:XX:XX:XX:XX), unlike the
# CoreBluetooth UUIDs macOS uses. Found via testMAC.py while running on Windows.
ADDRESS = "36:46:3F:08:93:13"   # LED 1 MAC (Bleak)
ADDRESS2 = "BE:69:ED:24:E6:06"  # LED 2 MAC (MELK / LotusLight X)
CHAR_UUID = "0000ffd9-0000-1000-8000-00805f9b34fb"       # LED 1 characteristic
MELK_CHAR_UUID = "0000fff3-0000-1000-8000-00805f9b34fb"  # LED 2 characteristic

RETRIES = 3
RETRY_BACKOFF = 2.0
FULL_BRIGHTNESS = 100

# These strips drop an idle connection almost immediately, so the first write
# has to go out the moment connect() returns -- adding a settle delay there
# measurably doubled the failure rate. A pause after disconnecting is fine, and
# gives Windows time to release the handle before the next connect.
DISCONNECT_SETTLE = 1.0


# check if user is home
def isUserHome():
   response = requests.get('https://ipinfo.io/json')
   data = response.json()
   ip = data.get('ip')
   if ip != "207.173.133.110":
       print("Not home, no LED connection")
       return False
   return True


# check if bluetooth is on
async def bluetoothIsOn() -> bool:
   try:
       await BleakScanner.discover(timeout=2.0)
       return True
   except BleakBluetoothNotAvailableError:
       return False


# Windows' WinRT Bluetooth backend hangs during GATT service discovery when
# connecting directly by MAC address for these strips. Connecting with a
# BLEDevice object obtained from a live scan avoids the hang, so every
# connection below resolves the device via BleakScanner first.
#
# find_device_by_address() returns as soon as it spots the target, instead of
# discover()'s fixed-length scan of everything nearby, so this is much faster
# in the common case where the strip is already advertising.
async def resolve_device(address: str, timeout: float = 10.0):
    print(f"Scanning for {address}...")
    device = await BleakScanner.find_device_by_address(address, timeout=timeout)
    if device is None:
        return None
    print(f"  found {address}")
    return device


# LED 1 (QHM) protocol
def qhm_on():
    return bytearray([0xCC, 0x23, 0x33])


def qhm_off():
    return bytearray([0xCC, 0x24, 0x33])


def qhm_color(r, g, b):
    return bytearray([0x56, r, g, b, 0x00, 0xF0, 0xAA])


# LED 2 (MELK / LotusLight X) protocol, 9-byte frames. Spoken directly rather
# than through btledstrip, whose context manager sleeps a full second after
# each of three clock-sync commands on every connect -- 3s of latency per
# command for a handshake that has nothing to do with controlling the light.
def melk_on():
    return bytearray([0x7E, 0x00, 0x04, 0x01, 0x00, 0x00, 0x00, 0x00, 0xEF])


def melk_off():
    return bytearray([0x7E, 0x00, 0x04, 0x00, 0x00, 0x00, 0xFF, 0x00, 0xEF])


def melk_brightness(percent):
    return bytearray([0x7E, 0x04, 0x01, percent, 0x00, 0x00, 0x00, 0x00, 0xEF])


def melk_color(r, g, b):
    return bytearray([0x7E, 0x00, 0x05, 0x03, r, g, b, 0x00, 0xEF])


async def send_commands(label, address, char_uuid, commands, gap=0.2):
    """Connect, write each command, then always disconnect.

    The disconnect is the part that matters for reliability: if it's skipped,
    Windows holds a half-open handle and the strip stops advertising entirely
    until it's power cycled. That's also why nothing here is wrapped in
    asyncio.wait_for -- a timeout there cancels the coroutine mid-flight and
    can abort the disconnect, wedging the device.
    """
    device = await resolve_device(address)
    if device is None:
        raise RuntimeError(f"{label} ({address}) not found in scan.")

    client = BleakClient(device, timeout=15.0)
    await client.connect()
    try:
        for command in commands:
            await client.write_gatt_char(char_uuid, command, response=False)
            await asyncio.sleep(gap)
    finally:
        try:
            await client.disconnect()
        except Exception as e:
            print(f"  {label}: disconnect failed: {e}")
        await asyncio.sleep(DISCONNECT_SETTLE)


async def send_with_retries(label, address, char_uuid, commands, gap=0.2):
    for attempt in range(1, RETRIES + 1):
        try:
            print(f"{label}: attempt {attempt}")
            await send_commands(label, address, char_uuid, commands, gap)
            print(f"{label}: finished")
            return
        except Exception as e:
            print(f"{label} error on attempt {attempt}: {e}")
            if attempt == RETRIES:
                raise RuntimeError(f"{label} failed after {RETRIES} attempts: {e}")
            await asyncio.sleep(RETRY_BACKOFF)


# turn light on or off user input
def onOrOffIO():
   onOrOff = "test"
   while onOrOff.lower() not in ("on", "off", "e"):
       onOrOff = input("ON or OFF: ")


   if onOrOff.lower() == "e":
       print("Bye Bye")
       sys.exit(0)


   if onOrOff.lower() == "off":
       return onOrOff, 0, 0, 0


   colors = {
       "r": (255, 0, 0),
       "g": (0, 255, 0),
       "b": (0, 0, 255),
       "p": (255, 0, 255),
       "y": (255, 155, 0),
       "c": (0, 255, 255),
       "w": (255, 255, 255),
       "m": None,
   }


   color = ""
   while color not in colors:
       color = input("(r/g/b/p/y/c/w/m): ").lower()


   if color == "m":
       print("Manual (0-255)")
       r = int(input("r: "))
       g = int(input("g: "))
       b = int(input("b: "))
   else:
       r, g, b = colors[color]


   return onOrOff, r, g, b


async def apply_from_gui(onOrOff: str, r: int, g: int, b: int):
    onOrOff = onOrOff.lower().strip()

    # Clamp values
    r = max(0, min(255, int(r)))
    g = max(0, min(255, int(g)))
    b = max(0, min(255, int(b)))

    print(f"request: power={onOrOff}, RGB=({r}, {g}, {b})")

    if not await bluetoothIsOn():
        raise RuntimeError("Bluetooth not available or turned off.")

    if onOrOff == "off":
        led1_commands = [qhm_off()]
        led2_commands = [melk_off()]
    else:
        # Colour first, then power on, then colour again -- the strip ignores
        # colour writes made while it's still powered off.
        led1_commands = [qhm_color(r, g, b), qhm_on(), qhm_color(r, g, b)]
        led2_commands = [melk_on(), melk_brightness(FULL_BRIGHTNESS), melk_color(r, g, b)]

    led1_error = None
    try:
        await send_with_retries("LED 1", ADDRESS, CHAR_UUID, led1_commands)
    except Exception as e:
        # Don't let one strip being unreachable stop the other from responding.
        led1_error = e
        print(f"  {e}")

    await send_with_retries("LED 2", ADDRESS2, MELK_CHAR_UUID, led2_commands)

    if led1_error:
        raise led1_error


# connect to bluetooth plus conditions
async def main():
    if not isUserHome():
        return

    onOrOff, r, g, b = onOrOffIO()
    await apply_from_gui(onOrOff, r, g, b)


if __name__ == "__main__":
   asyncio.run(main())
