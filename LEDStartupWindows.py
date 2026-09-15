import asyncio
import os
import sys
import traceback
from datetime import datetime

LOG_PATH = os.path.join(os.path.dirname(__file__), "LEDStartupWindows.log")

# pythonw.exe has no console. When launched with no inherited stdio handles
# (e.g. from Task Scheduler) sys.stdout/sys.stderr can be None, and any
# print() call in LEDControllerWindows would crash with AttributeError.
# Redirect both to the log file up front so that's never a problem.
_log_file = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
sys.stdout = _log_file
sys.stderr = _log_file


def log(msg: str):
    print(f"{datetime.now().isoformat()} {msg}", flush=True)


async def main():
    import LEDControllerWindows as led
    await led.apply_from_gui("on", 0, 255, 255, 100)  # cyan, full brightness


if __name__ == "__main__":
    try:
        log("startup script launched")
        asyncio.run(main())
        log("startup script finished OK")
    except Exception:
        log("startup script FAILED:\n" + traceback.format_exc())
        sys.exit(1)
