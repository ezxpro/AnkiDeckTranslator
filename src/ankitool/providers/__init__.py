"""Importing this package registers every built-in provider (see @register_provider).
To add a service, create providers/<name>.py and import it here."""
from ankitool.providers import base, deepl, fake  # noqa: F401  (imports register the classes)
