class AnkiToolError(Exception):
    """Erro esperado (mensagem para a pessoa). O front-end decide como mostrar/sair."""

class ConfigError(AnkiToolError):
    """Opção/entrada inválida."""

class ProviderUnavailable(AnkiToolError):
    """O serviço de tradução não respondeu."""

class Cancelled(AnkiToolError):
    """A operação foi cancelada."""
