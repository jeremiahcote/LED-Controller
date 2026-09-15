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
from btledstrip import BTLedStrip, MELKController


# Windows Bluetooth addresses are real MACs (XX:XX:XX:XX:XX:XX), unlike the
# CoreBluetooth UUIDs macOS uses. Found via testMAC.py while running on Windows.
ADDRESS = "36:46:3F:08:93:13"   # LED 1 MAC (Bleak)
ADDRESS2 = "BE:69:ED:24:E6:06"  # LED 2 MAC (MELK / LotusLight X)
CHAR_UUID = "0000ffd9-0000-1000-8000-00805f9b34fb"  # LED 1 characteristic


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
async def resolve_device(address: str, timeout: float = 15.0):
    print(f"Scanning for {address}...")
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    if address not in devices:
        return None
    device, adv = devices[address]
    print(f"  found {address} rssi={adv.rssi}")
    return device


# turn light on or off user input
def onOrOffIO():
   onOrOff = "test"
   while onOrOff.lower() not in ("on", "off", "e"):
       onOrOff = input("ON or OFF: ")


   if onOrOff.lower() == "e":
       print("Bye Bye")
       sys.exit(0)


   if onOrOff.lower() == "off":
       cmd = bytearray([0xCC, 0x24, 0x33])  # OFF
       return onOrOff, cmd, 0, 0, 0, None


   # ON
   cmd = bytearray([0xCC, 0x23, 0x33])  # ON


   def rgb_command(r, g, b):
       return bytearray([0x56, r, g, b, 0x00, 0xF0, 0xAA])


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
       color = input("(r/g/b/p/y/c/w): ").lower()


   if color == "m":
       print("Manual (0-255)")
       r = int(input("r: "))
       g = int(input("g: "))
       b = int(input("b: "))
   else:
       r, g, b = colors[color]


   colorCommand = rgb_command(r, g, b)
   return onOrOff, cmd, r, g, b, colorCommand


def to_unit_float(v_0_255: int) -> float:
   v = max(0, min(255, int(v_0_255)))
   return (v / 255.0) * 100.0


async def apply_from_gui(onOrOff: str, r: int, g: int, b: int, brightness: int = 100):
    onOrOff = onOrOff.lower().strip()

    # Clamp values
    r = max(0, min(255, int(r)))
    g = max(0, min(255, int(g)))
    b = max(0, min(255, int(b)))
    brightness = max(0, min(100, int(brightness)))

    print(f"GUI request: power={onOrOff}, RGB=({r}, {g}, {b}), brightness={brightness}")

    # ==============================
    # Bluetooth availability check
    # ==============================
    if not await bluetoothIsOn():
        raise RuntimeError("Bluetooth not available or turned off.")

    # ==============================
    # LED 1 - QHM / Bleak
    # ==============================
    color_command = bytearray([
        0x56,
        r,
        g,
        b,
        0x00,
        0xF0,
        0xAA
    ])

    on_command = bytearray([0xCC, 0x23, 0x33])
    off_command = bytearray([0xCC, 0x24, 0x33])

    async def led1_sequence():
        device = await resolve_device(ADDRESS)
        if device is None:
            raise RuntimeError(f"LED 1 ({ADDRESS}) not found in scan.")

        async with BleakClient(device, timeout=10.0) as client:
            if onOrOff == "off":
                print("LED 1: sending OFF")

                await client.write_gatt_char(
                    CHAR_UUID,
                    off_command,
                    response=False
                )

                await asyncio.sleep(0.3)

            else:
                print(f"LED 1: sending color ({r}, {g}, {b})")

                # Send color first
                await client.write_gatt_char(
                    CHAR_UUID,
                    color_command,
                    response=False
                )

                await asyncio.sleep(0.2)

                print("LED 1: sending ON")

                # Make sure strip is powered on
                await client.write_gatt_char(
                    CHAR_UUID,
                    on_command,
                    response=False
                )

                await asyncio.sleep(0.2)

                print(f"LED 1: sending color again ({r}, {g}, {b})")

                # Send color again after power-on
                await client.write_gatt_char(
                    CHAR_UUID,
                    color_command,
                    response=False
                )

                await asyncio.sleep(0.2)

    led1_max_retries = 3
    for led1_attempt in range(1, led1_max_retries + 1):
        try:
            print(f"LED 1: attempt {led1_attempt}")

            await asyncio.wait_for(
                led1_sequence(),
                timeout=30.0
            )

            print("LED 1: finished")
            break

        except asyncio.TimeoutError:
            print(f"LED 1 timeout on attempt {led1_attempt}")

            if led1_attempt == led1_max_retries:
                raise RuntimeError(
                    "LED 1 timed out after multiple attempts."
                )

            await asyncio.sleep(2)

        except Exception as e:
            print(f"LED 1 error on attempt {led1_attempt}: {e}")

            if led1_attempt == led1_max_retries:
                raise RuntimeError(
                    f"LED 1 failed after {led1_max_retries} attempts: {e}"
                )

            await asyncio.sleep(2)

    # ==============================
    # LED 2 - MELKController
    # ==============================
    controller = MELKController()
    max_retries = 3

    async def led2_sequence():
        device2 = await resolve_device(ADDRESS2)
        if device2 is None:
            raise RuntimeError(f"LED 2 ({ADDRESS2}) not found in scan.")

        async with BTLedStrip(controller, device2) as led:
            if onOrOff == "off":
                print("LED 2: sending OFF")

                await led.exec.turn_off()

                await asyncio.sleep(0.2)

            else:
                print("LED 2: sending ON")

                # Turn on first, matching working non-GUI code
                await led.exec.turn_on()

                await asyncio.sleep(0.4)

                print(f"LED 2: brightness {brightness}")

                await led.exec.brightness(
                    percentage=brightness
                )

                await asyncio.sleep(0.2)

                print(f"LED 2: sending color ({r}, {g}, {b})")

                await led.exec.color(
                    red=to_unit_float(r),
                    green=to_unit_float(g),
                    blue=to_unit_float(b),
                )

                await asyncio.sleep(0.3)

                # Keep connection alive briefly so BLE writes flush
                await asyncio.sleep(0.6)

    for attempt in range(1, max_retries + 1):
        try:
            print(f"LED 2: attempt {attempt}")

            await asyncio.wait_for(
                led2_sequence(),
                timeout=35.0
            )

            print("LED 2: finished")
            return

        except asyncio.TimeoutError:
            print(f"LED 2 timeout on attempt {attempt}")

            if attempt == max_retries:
                raise RuntimeError(
                    "LED 2 timed out after multiple attempts."
                )

            await asyncio.sleep(2)

        except Exception as e:
            print(f"LED 2 error on attempt {attempt}: {e}")

            if attempt == max_retries:
                raise RuntimeError(
                    f"LED 2 failed after {max_retries} attempts: {e}"
                )

            await asyncio.sleep(2)




# connect to bluetooth plus conditions
async def main():
   if isUserHome():
       onOrOff, cmd, r, g, b, colorCommand = onOrOffIO()
       if onOrOff.lower() == "on":
           #brightness = input("Brightness of secondary LED (0-100): ")


           #if brightness.strip() == "":
               brightness = 100
           #else:
               #brightness = int(brightness)


       if not await bluetoothIsOn():
           print("Bluetooth is off, no LED connection")
           return


       # LED 1 (Bleak / QHM)
       device = await resolve_device(ADDRESS)
       if device is None:
           print(f"LED 1 ({ADDRESS}) not found in scan, skipping")
           return

       async with BleakClient(device, timeout=10.0) as client:

        if onOrOff.lower() == "off":
            off_command = bytearray([0xCC, 0x24, 0x33])

            await client.write_gatt_char(
                CHAR_UUID,
                off_command,
                response=False
            )

        else:
            on_command = bytearray([0xCC, 0x23, 0x33])

            # Send color first
            await client.write_gatt_char(
                CHAR_UUID,
                colorCommand,
                response=False
            )

            await asyncio.sleep(0.2)

            # Make sure the strip is powered on
            await client.write_gatt_char(
                CHAR_UUID,
                on_command,
                response=False
            )

            await asyncio.sleep(0.2)

            # Send color again after power-on
            await client.write_gatt_char(
                CHAR_UUID,
                colorCommand,
                response=False
            )


       # LED 2 (MELKController via btledstrip) - retry up to 3 times
        controller = MELKController()
        max_retries = 3


        for attempt in range(1, max_retries + 1):
           try:
               device2 = await resolve_device(ADDRESS2)
               if device2 is None:
                   print(f"LED 2 ({ADDRESS2}) not found in scan (attempt {attempt} of {max_retries})")
                   if attempt < max_retries:
                       await asyncio.sleep(2)
                       continue
                   else:
                       break

               async with BTLedStrip(controller, device2) as led:
                   if onOrOff.lower() == "off":
                       await led.exec.turn_off()
                       await asyncio.sleep(0.2)
                   else:
                       # Many controllers need to be ON before color/brightness "stick"
                       await led.exec.turn_on()
                       await asyncio.sleep(0.4)


                       await led.exec.brightness(percentage=brightness)
                       await asyncio.sleep(0.2)


                       await led.exec.color(
                           red=to_unit_float(r),
                           green=to_unit_float(g),
                           blue=to_unit_float(b),
                       )
                       await asyncio.sleep(0.3)


                       # keep the connection alive briefly so BLE writes flush
                       await asyncio.sleep(0.6)


                   break
           except Exception as e:
               print(f"LED 2 error (attempt {attempt} of {max_retries}): {e}")
               if attempt < max_retries:
                   await asyncio.sleep(2)
               else:
                   print("LED 2 failed after 3 attempts")


if __name__ == "__main__":
   asyncio.run(main())

