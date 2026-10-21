from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
import os
import time

from ankitool.core.errors import ConfigError
from ankitool.core.paths import SCRIPT_DIR
from ankitool.core.text import TextTools


@dataclass(frozen=True)
class Language:
    code: str                                  # código na grafia do próprio provedor
    name: str

@dataclass(frozen=True)
class TranslationResult:
    text: str
    detected_source_lang: str = None           # idioma detectado (grafia do provedor)

def load_secret(env_name, label):
    """Procura a chave na variável de ambiente <env_name>; se não achar, lê o arquivo .env
    que fica na mesma pasta do script (linha no formato NOME=valor)."""
    key = os.environ.get(env_name, "").strip()
    if not key:
        env_file = SCRIPT_DIR / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                if name.replace("export", "").strip() == env_name:
                    key = value.strip().strip('"').strip("'")
                    break
    if not key:
        raise ConfigError(f"Chave do {label} não encontrada. Defina a variável de ambiente "
                          f"{env_name} ou crie um arquivo .env ao lado do script com:\n"
                          f"{env_name}=sua-chave-aqui")
    return key

PROVIDERS = {}                                 # nome -> classe; preenchido por @register_provider

def register_provider(cls):
    PROVIDERS[cls.name] = cls
    return cls

def make_provider(name):
    if name not in PROVIDERS:
        raise ConfigError(f"Serviço de tradução desconhecido: {name}. "
                          f"Disponíveis: {', '.join(sorted(PROVIDERS))}")
    return PROVIDERS[name]()

class TranslationProvider(ABC):
    """Contrato de um serviço de tradução. O restante do programa só conhece esta interface.

    Para adicionar outro serviço (Google, Azure, LibreTranslate, ...):
      1. crie uma subclasse com name, label e key_env;
      2. implemente connect, fetch_target_languages, fetch_source_languages e _translate
         (e, se houver, usage e detect);
      3. decore a classe com @register_provider.
    Ele passa a valer em --provider e no menu interativo, com cache próprio
    (cache_<name>.json). Se o serviço não entende HTML com translate="no", sobrescreva
    mask/unmask para proteger [sound:...] e {{c1::...}} de outra forma."""

    name = ""                                  # identificador usado em --provider
    label = ""                                 # nome para exibição
    key_env = ""                               # variável de ambiente / .env com a chave
    default_target = "PT-BR"                   # código sugerido como idioma de saída
    batch_size = 40                            # textos por chamada
    retries = 3
    transient_errors = (ConnectionError, TimeoutError)   # erros que valem nova tentativa
    cache_tag = None                           # None -> "_<name>" nos nomes dos arquivos de cache
    hidden = False                             # True: só vale com --provider, fora do menu

    def __init__(self, key=None):
        self.key = key or load_secret(self.key_env, self.label)
        self.connect(self.key)
        self.target_langs = self.fetch_target_languages()
        self.source_langs = self.fetch_source_languages()
        self.target_codes = {l.code for l in self.target_langs}
        self.source_names = {l.code: l.name for l in self.source_langs}
        self._target_by_norm = {l.code.upper(): l.code for l in self.target_langs}

    # ---- a implementar em cada serviço ----
    @abstractmethod
    def connect(self, key):
        """Cria o cliente do serviço (e ajusta transient_errors, se preciso)."""

    @abstractmethod
    def fetch_target_languages(self):
        """-> lista de Language disponíveis como saída."""

    @abstractmethod
    def fetch_source_languages(self):
        """-> lista de Language reconhecidos como origem."""

    @abstractmethod
    def _translate(self, texts, target_lang, source_lang, html):
        """Uma chamada ao serviço. Devolve uma TranslationResult por texto, na mesma ordem.
        html=True: os textos contêm tags <span translate="no"> que não podem ser traduzidas."""

    def usage(self):
        """Consumo atual (para exibir), ou None se o serviço não informa."""
        return None

    def detect(self, texts, target_lang):
        """Idioma de cada texto (None se o serviço estiver fora do ar). Por padrão traduz e
        lê o idioma detectado; sobrescreva se o serviço tiver um endpoint de detecção."""
        res = self.translate(texts, target_lang)
        return None if res is None else [r.detected_source_lang for r in res]

    # ---- comum a todos ----
    def mask(self, t):
        return TextTools.mask(t)

    def unmask(self, t):
        return TextTools.unmask(t)

    def translate(self, texts, target_lang, source_lang=None, protect_tags=False):
        """Traduz com retentativa; devolve None se o serviço não responder.
        protect_tags=True protege [sound:...] e {{c1::...}} (restaurados no resultado)."""
        payload = [self.mask(t) for t in texts] if protect_tags else list(texts)
        for _ in range(self.retries):
            try:
                res = self._translate(payload, target_lang, source_lang, protect_tags)
                break
            except self.transient_errors:
                time.sleep(3)
        else:
            return None
        if protect_tags:
            res = [TranslationResult(self.unmask(r.text), r.detected_source_lang) for r in res]
        return res

    def resolve_target(self, raw):
        """Texto digitado -> código de saída válido (grafia do serviço), ou None."""
        return self._target_by_norm.get((raw or "").strip().upper())

    def cache_file(self, stem, cache_dir):
        tag = f"_{self.name}" if self.cache_tag is None else self.cache_tag
        return Path(cache_dir) / f"{stem}{tag}.json"
