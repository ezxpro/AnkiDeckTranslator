import os
import sys

from ankitool.cli.main import main


def _double_clicked_on_windows() -> bool:
    """True when this process owns its console window (launched by double-click)."""
    if sys.platform != "win32":
        return False
    import ctypes

    pids = (ctypes.c_uint * 4)()
    # onefile = bootloader + app; a terminal session adds the shell as a third.
    return ctypes.windll.kernel32.GetConsoleProcessList(pids, 4) <= 2


def _pause() -> None:
    try:
        input("\nPress Enter to close...")
    except EOFError:
        pass


if __name__ == "__main__":
    pause = bool(os.environ.get("ANKITOOL_PAUSE")) or _double_clicked_on_windows()
    try:
        main()
    finally:
        if pause:
            _pause()
