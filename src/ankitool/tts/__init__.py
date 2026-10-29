"""Importing this package registers every built-in speech service (see @register_speech).
To add a service, create tts/<name>.py and import it here."""
from ankitool.tts import azure, base  # noqa: F401  (imports register the classes)
