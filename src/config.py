"""Configurazione centralizzata per X-Gemini Bookmarks."""

import os
from pathlib import Path
from typing import Any, Dict
import yaml
from dotenv import load_dotenv

# Directory base del progetto
BASE_DIR = Path(__file__).resolve().parent.parent

# Carica variabili d'ambiente da .env
load_dotenv(BASE_DIR / ".env")


class AppConfig:
    def __init__(self, config_path: Path | None = None):
        self.config_path = config_path or (BASE_DIR / "config" / "config.yaml")
        self._raw_config: Dict[str, Any] = self._load_yaml()

        # Risoluzione percorsi assoluti
        self.data_dir = BASE_DIR / "data"
        self.media_dir = BASE_DIR / self._raw_config.get("storage", {}).get("media_dir", "data/media")
        self.archive_dir = BASE_DIR / self._raw_config.get("storage", {}).get("archive_dir", "data/archive")
        self.logs_dir = BASE_DIR / self._raw_config.get("storage", {}).get("logs_dir", "logs")
        self.db_path = BASE_DIR / self._raw_config.get("storage", {}).get("database_path", "data/bookmarks.db")

        # Assicura esistenza directory
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _load_yaml(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            return {}
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception:
            return {}

    # --- Sezione X ---
    @property
    def x_cookie_source(self) -> str:
        return os.environ.get("X_COOKIE_SOURCE") or self._raw_config.get("x_account", {}).get("cookie_source", "safari")

    @property
    def x_auth_token(self) -> str | None:
        return os.environ.get("X_AUTH_TOKEN")

    @property
    def x_ct0(self) -> str | None:
        return os.environ.get("X_CT0")

    @property
    def x_batch_size(self) -> int:
        return int(self._raw_config.get("x_account", {}).get("batch_size", 20))

    # --- Sezione Motore di Analisi ---
    @property
    def analyzer_engine(self) -> str:
        return os.environ.get("ANALYZER_ENGINE") or self._raw_config.get("engine", "claude")

    @property
    def analyzer_engine_source(self) -> str:
        if os.environ.get("ANALYZER_ENGINE"):
            return "env:ANALYZER_ENGINE"
        return "config/config.yaml:engine"

    @property
    def claude_model(self) -> str:
        return os.environ.get("CLAUDE_MODEL") or self._raw_config.get("claude", {}).get("model", "claude-sonnet-5-5")

    @property
    def claude_model_source(self) -> str:
        if os.environ.get("CLAUDE_MODEL"):
            return "env:CLAUDE_MODEL"
        return "config/config.yaml:claude.model"

    @property
    def claude_effort(self) -> str:
        return os.environ.get("CLAUDE_EFFORT") or self._raw_config.get("claude", {}).get("effort", "high")

    @property
    def claude_weekly_reset_time(self) -> str:
        return str(self._raw_config.get("claude", {}).get("weekly_reset_time", "06:00"))

    @property
    def claude_weekly_reset_tz(self) -> str:
        return str(self._raw_config.get("claude", {}).get("weekly_reset_tz", "Europe/Rome"))

    # --- Ollama locale (opzionale; il default di produzione resta claude) ---
    @property
    def ollama_model(self) -> str:
        return os.environ.get("OLLAMA_MODEL") or str(self._raw_config.get("ollama", {}).get("model") or "")

    @property
    def ollama_base_url(self) -> str:
        return os.environ.get("OLLAMA_BASE_URL") or str(
            self._raw_config.get("ollama", {}).get("base_url") or "http://127.0.0.1:11434"
        )

    @property
    def ollama_timeout_seconds(self) -> int:
        raw = os.environ.get("OLLAMA_TIMEOUT")
        if raw is not None:
            return int(raw)
        return int(self._raw_config.get("ollama", {}).get("timeout_seconds", 120))

    @property
    def ollama_max_attempts(self) -> int:
        return max(1, int(self._raw_config.get("ollama", {}).get("max_attempts", 2)))

    @property
    def ollama_num_ctx(self) -> int:
        raw = os.environ.get("OLLAMA_NUM_CTX")
        if raw is not None:
            return int(raw)
        return int(self._raw_config.get("ollama", {}).get("num_ctx", 4096))

    @property
    def ollama_num_predict(self) -> int:
        return int(self._raw_config.get("ollama", {}).get("num_predict", 512))

    @property
    def ollama_max_input_chars(self) -> int:
        return int(self._raw_config.get("ollama", {}).get("max_input_chars", 2000))

    @property
    def ollama_temperature(self) -> float:
        return float(self._raw_config.get("ollama", {}).get("temperature", 0))

    @property
    def ollama_think(self) -> bool:
        """False disattiva il thinking. L'API accetta bool; il modello lo tiene acceso di default."""
        val = os.environ.get("OLLAMA_THINK")
        if val is not None:
            return val.lower() in ("1", "true", "yes")
        return bool(self._raw_config.get("ollama", {}).get("think", False))

    @property
    def ollama_max_per_cycle(self) -> int:
        return max(1, int(self._raw_config.get("ollama", {}).get("max_per_cycle", 1)))

    # --- Sezione Gemini ---
    @property
    def gemini_api_key(self) -> str | None:
        return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

    @property
    def gemini_model(self) -> str:
        return os.environ.get("GEMINI_MODEL") or self._raw_config.get("gemini", {}).get("model", "gemini-3.5-flash")

    @property
    def gemini_temperature(self) -> float:
        return float(self._raw_config.get("gemini", {}).get("temperature", 0.2))

    @property
    def gemini_max_tokens(self) -> int:
        return int(self._raw_config.get("gemini", {}).get("max_output_tokens", 1024))

    # --- Sezione Sync ---
    @property
    def sync_interval_seconds(self) -> int:
        return int(os.environ.get("SYNC_INTERVAL") or self._raw_config.get("sync", {}).get("interval_seconds", 300))

    @property
    def download_media(self) -> bool:
        val = os.environ.get("DOWNLOAD_MEDIA")
        if val is not None:
            return val.lower() in ("1", "true", "yes")
        return bool(self._raw_config.get("sync", {}).get("download_media", True))

    @property
    def export_markdown(self) -> bool:
        val = os.environ.get("EXPORT_MARKDOWN")
        if val is not None:
            return val.lower() in ("1", "true", "yes")
        return bool(self._raw_config.get("sync", {}).get("export_markdown", True))

    # --- Sezione Analisi ---
    @property
    def analysis_mode(self) -> str:
        return self._raw_config.get("analysis", {}).get("mode", "basic")

    @property
    def analysis_language(self) -> str:
        return self._raw_config.get("analysis", {}).get("language", "it")

    # --- Sezione Telegram ---
    @property
    def telegram_enabled(self) -> bool:
        val = os.environ.get("TELEGRAM_ENABLED")
        if val is not None:
            return val.lower() in ("1", "true", "yes")
        return bool(self._raw_config.get("telegram", {}).get("enabled", True))

    @property
    def telegram_bot_token(self) -> str | None:
        return os.environ.get("TELEGRAM_BOT_TOKEN") or self._raw_config.get("telegram", {}).get("bot_token")

    @property
    def telegram_chat_id(self) -> str | None:
        return os.environ.get("TELEGRAM_CHAT_ID") or str(self._raw_config.get("telegram", {}).get("chat_id") or "")

    @property
    def telegram_send_photos(self) -> bool:
        val = os.environ.get("TELEGRAM_SEND_PHOTOS")
        if val is not None:
            return val.lower() in ("1", "true", "yes")
        return bool(self._raw_config.get("telegram", {}).get("send_photos", True))

    @property
    def telegram_notify_on_new(self) -> bool:
        val = os.environ.get("TELEGRAM_NOTIFY")
        if val is not None:
            return val.lower() in ("1", "true", "yes")
        return bool(self._raw_config.get("telegram", {}).get("notify_on_new_bookmark", True))


# Singleton globale di configurazione
config = AppConfig()
