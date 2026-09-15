import asyncio
import LEDControllerWindows as led


async def main():
    await led.apply_from_gui("on", 0, 255, 255, 100)  # cyan, full brightness


if __name__ == "__main__":
    asyncio.run(main())
