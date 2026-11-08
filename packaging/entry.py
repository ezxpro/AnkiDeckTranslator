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


def _use_system_certificates() -> None:
    """The bundled OpenSSL doesn't know where the distro keeps its CA certificates,
    so HTTPS fails. Point it at the system bundle."""
    if not (getattr(sys, "frozen", False) and sys.platform.startswith("linux")):
        return
    if os.environ.get("SSL_CERT_FILE"):
        return
    for path in ("/etc/ssl/certs/ca-certificates.crt",      # Debian, Ubuntu, Arch, Gentoo, Void
                 "/etc/pki/tls/certs/ca-bundle.crt",        # Fedora, RHEL
                 "/etc/ssl/ca-bundle.pem",                  # openSUSE
                 "/etc/ssl/certs/ca-bundle.crt",            # NixOS
                 "/etc/pki/tls/cert.pem",                   # older RHEL
                 "/etc/ssl/cert.pem"):                      # Alpine
        if os.path.exists(path):
            os.environ["SSL_CERT_FILE"] = path
            os.environ.setdefault("REQUESTS_CA_BUNDLE", path)
            return
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
    except ImportError:
        pass


def _pause() -> None:
    try:
        input("\nPress Enter to close...")
    except EOFError:
        pass


if __name__ == "__main__":
    _use_system_certificates()
    pause = bool(os.environ.get("ANKITOOL_PAUSE")) or _double_clicked_on_windows()
    try:
        main()
    finally:
        if pause:
            _pause()
