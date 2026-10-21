class AnkiToolError(Exception):
    """Expected error (message shown to the user). The front end decides how to show it and whether to exit."""

class ConfigError(AnkiToolError):
    """Invalid option or input."""

class ProviderUnavailable(AnkiToolError):
    """The translation service did not respond."""

class Cancelled(AnkiToolError):
    """The operation was cancelled."""
