import asyncio
from bleak import BleakScanner

async def main():
    print("Scanning for Bluetooth devices for 10 seconds...")

    devices = await BleakScanner.discover(timeout=10.0)

    for device in devices:
        print(f"{device.name} -> {device.address}")

asyncio.run(main())