"""Client per l'analisi dei post tramite Claude CLI (Claude Code), configurato per Bookmarks-X."""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("bookmarks_x_claude")

CLAUDE_WORKDIR = Path(tempfile.gettempdir()) / "bookmarksx-claude"


def build_claude_env(source: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Ambiente del subprocess CLI.

    È uno snapshot: `main.py` importa questo modulo prima di `load_dotenv`,
    quindi una chiave presente solo in `.env` non viene iniettata e non
    scavalca il login Pro già verificato. Il PATH del LaunchAgent non include
    `~/.local/bin`; lo aggiungiamo qui, prima di risolvere il binario.
    """
    base = os.environ if source is None else source
    env = {
        key: value
        for key, value in base.items()
        if key != "CLAUDECODE" and (not key.startswith("CLAUDE_CODE_") or key == "CLAUDE_CODE_OAUTH_TOKEN")
    }
    env["PATH"] = os.pathsep.join([
        str(Path(__file__).resolve().parent.parent.parent / ".venv" / "bin"),
        str(Path.home() / ".local" / "bin"),
        str(base.get("PATH", "")),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
    ])
    return env


def cli_failure_detail(stderr: Optional[str], stdout: Optional[str], limit: int = 500) -> str:
    """Testo utile quando la CLI esce non-zero.

    Con `--output-format json` il motivo sta spesso su stdout e stderr è vuoto.
    """
    err = (stderr or "").strip()
    out = (stdout or "").strip()
    if err and out:
        detail = f"{err} | stdout: {out}"
    else:
        detail = err or out
    if not detail:
        detail = "(stderr e stdout vuoti)"
    if len(detail) > limit:
        return detail[:limit] + "…"
    return detail


ENV = build_claude_env()

CLAUDE_CMD = shutil.which("claude", path=ENV["PATH"]) or "claude"

# ID verificato, non un alias. Documentazione:
# https://code.claude.com/docs/en/model-config (alias sonnet -> Sonnet 5.5 sull'API Anthropic)
# https://docs.anthropic.com/en/docs/about-claude/models/overview (API ID claude-sonnet-5-5)
# Richiede Claude Code >= 2.1.284. Sul mini è installata la 2.1.317.
SONNET_MODEL_ID = "claude-sonnet-5-5"
SONNET_EFFORT = "high"
MAX_ATTEMPTS = 2
RETRY_DELAYS = (5,)
CLAUDE_TIMEOUT = 180
# Il reset settimanale Pro è per-account (Settings > Usage). 06:00 Europe/Rome è l'orario riportato
# per questo account, non una regola universale di Anthropic.
DEFAULT_WEEKLY_RESET_TIME = "06:00"
DEFAULT_WEEKLY_RESET_TZ = "Europe/Rome"

CATEGORIES = [
    "🤖 Agenti & Coding AI",
    "💡 Prompt & Guide",
    "🛠️ Strumenti & App",
    "🔬 Modelli & Ricerca",
    "⚡ Risorse & GitHub",
    "📰 News & Trend",
]

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "autore": {
            "type": "string",
            "description": "L'autore del post nel formato 'Nome (@username)'."
        },
        "titolo": {
            "type": "string",
            "description": "Titolo chiaro, accattivante ed esplicativo del post (massimo 10-15 parole, in lingua italiana) che ne riassuma l'argomento centrale."
        },
        "categoria": {
            "type": "string",
            "enum": CATEGORIES,
            "description": "Macro-categoria più pertinente."
        },
        "il_succo": {
            "type": "string",
            "description": "L'intuizione principale o la novità concreta in 1-2 frasi (TL;DR concreto, eliminando l'hype da social)."
        },
        "punti_chiave": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Elenco di 3-4 punti salienti, caratteristiche tecniche o passaggi logici emersi dal post."
        },
        "caso_d_uso": {
            "type": "string",
            "description": "Spiegazione sintetica (1 frase) di quando e perché questo post è utile (es. 'Costruire agenti locali senza dipendere da API cloud')."
        },
        "tag": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3-5 hashtag tematici in minuscolo con prefisso # (es. ['#claude', '#agenti', '#ai'])."
        }
    },
    "required": ["autore", "titolo", "categoria", "il_succo", "punti_chiave", "caso_d_uso", "tag"],
    "additionalProperties": False
}

SYSTEM_PROMPT = """Sei un analista editoriale esperto per post tecnici e innovativi di X (Twitter).
Il tuo obiettivo è estrarre valore reale da ogni post salvato per un archivio personale su iPhone.
NON estrarre prompt lunghi o codice riga per riga. Concentrati invece sulla sostanza, sugli strumenti, sui vantaggi pratici e sull'architettura.

Estrai con precisione:
1. 'autore': 'Nome (@username)'
2. 'titolo': titolo descrittivo ed esplicativo (max 10-15 parole, italiano).
3. 'categoria': scegli esattamente una tra:
   - "🤖 Agenti & Coding AI"
   - "💡 Prompt & Guide"
   - "🛠️ Strumenti & App"
   - "🔬 Modelli & Ricerca"
   - "⚡ Risorse & GitHub"
   - "📰 News & Trend"
4. 'il_succo': sintesi essenziale in 1-2 frasi (senza toni promozionali o clickbait).
5. 'punti_chiave': esattamente 3 o 4 punti salienti.
6. 'caso_d_uso': perché e per chi è utile in pratica.
7. 'tag': 3-5 tag pertinenti (es. ['#claude', '#agenti', '#sviluppo']).

Rispondi rigorosamente rispettando lo schema JSON fornito."""


class ClaudeQuotaPause(RuntimeError):
    """Limite di piano raggiunto. Il ciclo non deve riprovare subito."""

    def __init__(self, message: str, *, resume_at: datetime, reason: str):
        super().__init__(message)
        self.stop_cycle = True
        self.resume_at = resume_at
        self.reason = reason


def child_env(effort: str) -> Dict[str, str]:
    """Copia l'ambiente già fissato all'import e forza l'effort solo nel figlio.

    ``CLAUDE_CODE_EFFORT_LEVEL`` ha precedenza sul flag ``--effort``.
    Non si modifica ``os.environ`` del processo del servizio.
    """
    env = dict(ENV)
    env["CLAUDE_CODE_EFFORT_LEVEL"] = effort
    return env


def usage_from_cli_json(data: Dict[str, Any]) -> Dict[str, Any]:
    """Token misurati dal JSON della CLI. La percentuale della finestra Pro non c'è.

    ``claude -p --output-format json`` espone ``usage`` e ``total_cost_usd``.
    ``/usage`` è solo interattivo. Nessun campo ufficiale dà la percentuale
    della finestra di 5 ore o del tetto settimanale, quindi quelle restano
    ``non disponibile``. I token non sono quel denominatore: la finestra di
    contesto (1M) e il prezzo API non sono la quota dell'abbonamento.
    """
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    model_usage = data.get("modelUsage") if isinstance(data.get("modelUsage"), dict) else {}
    effective_model = data.get("model")
    if not effective_model and model_usage:
        effective_model = next(iter(model_usage))
    effort = data.get("effort") or data.get("effortLevel")

    def num(source: Dict[str, Any], key: str):
        value = source.get(key)
        return value if isinstance(value, (int, float)) else None

    return {
        "effective_model": effective_model if isinstance(effective_model, str) else None,
        "effective_effort": effort if isinstance(effort, str) else None,
        "input_tokens": num(usage, "input_tokens"),
        "output_tokens": num(usage, "output_tokens"),
        "cache_read_input_tokens": num(usage, "cache_read_input_tokens"),
        "cache_creation_input_tokens": num(usage, "cache_creation_input_tokens"),
        "total_cost_usd": data.get("total_cost_usd") if isinstance(data.get("total_cost_usd"), (int, float)) else None,
        "duration_ms": num(data, "duration_ms") or num(data, "duration_api_ms"),
        "pro_5h_percent": None,
        "weekly_percent": None,
        "percent_note": "non disponibile",
    }


def format_usage_footer(usage: Optional[Dict[str, Any]]) -> str:
    """Riga unica per la notifica già esistente. Nessuna percentuale inventata."""
    if not usage:
        measured = "token non misurati"
        model = "n/d"
        effort = "n/d"
    else:
        def shown(key: str) -> str:
            value = usage.get(key)
            return "n/d" if value is None else str(value)

        measured = (
            f"in {shown('input_tokens')}, out {shown('output_tokens')}, "
            f"cache_read {shown('cache_read_input_tokens')}, "
            f"cache_write {shown('cache_creation_input_tokens')}"
        )
        model = usage.get("effective_model") or "non riportato"
        effort = usage.get("effective_effort") or "non riportato"
    return (
        f"Uso misurato ({model}, effort {effort}): {measured}. "
        "Finestra 5h Pro: non disponibile. "
        "Limite settimanale: distinto dalla finestra 5h, non disponibile da questa CLI."
    )


def classify_quota(text: str) -> Optional[str]:
    """Riconosce i messaggi di quota documentati. Non confonde il contesto da 1M token."""
    low = (text or "").lower()
    if "5-hour limit" in low or "five-hour limit" in low or "session limit" in low:
        return "session_5h"
    if "weekly limit" in low or "weekly usage" in low:
        return "weekly"
    if "rate_limit" in low or "rate limit" in low:
        return "rate_limit"
    return None


def _next_clock(now: datetime, hhmm: str, tz_name: str, days_ahead: int) -> datetime:
    tz = ZoneInfo(tz_name)
    local = now.astimezone(tz)
    hour, minute = (int(part) for part in hhmm.split(":", 1))
    candidate = (local + timedelta(days=days_ahead)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate


def resume_after_quota(kind: str, message: str, now: Optional[datetime] = None) -> datetime:
    """Attesa senza nuovi tentativi. Il giorno settimanale si usa solo se è nel messaggio."""
    from src.config import config

    now = now or datetime.now(timezone.utc)
    hhmm = config.claude_weekly_reset_time
    tz_name = config.claude_weekly_reset_tz
    if kind == "weekly":
        return _next_clock(now, hhmm, tz_name, days_ahead=7)
    if kind == "session_5h":
        return now + timedelta(hours=5)
    return now + timedelta(minutes=30)


def quota_pause_file() -> Path:
    from src.config import config
    return config.data_dir / "claude_quota_pause.json"


def read_quota_pause(now: Optional[datetime] = None) -> Optional[Dict[str, str]]:
    path = quota_pause_file()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        resume_at = datetime.fromisoformat(data["resume_at"])
    except Exception:
        return None
    now = now or datetime.now(timezone.utc)
    if resume_at.tzinfo is None:
        resume_at = resume_at.replace(tzinfo=timezone.utc)
    if resume_at <= now:
        return None
    return {"reason": str(data.get("reason") or ""), "resume_at": resume_at.isoformat()}


def write_quota_pause(resume_at: datetime, reason: str) -> None:
    path = quota_pause_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"reason": reason, "resume_at": resume_at.isoformat()}
    path.write_text(json.dumps(payload), encoding="utf-8")


class ClaudeAnalyzer:
    """Interfaccia verso Claude CLI. Un solo modello, effort esplicito, nessun fallback."""

    def __init__(self, model: Optional[str] = None, effort: Optional[str] = None):
        from src.config import config
        self.model = config.claude_model if model is None else model
        self.effort = config.claude_effort if effort is None else effort
        self.last_usage: Optional[Dict[str, Any]] = None

    def _command(self, prompt: str, *, schema: bool) -> list:
        command = [
            CLAUDE_CMD, "-p", prompt,
            "--model", self.model,
            "--effort", self.effort,
            "--tools", "",
            "--no-session-persistence",
            "--output-format", "json",
        ]
        if schema:
            command += [
                "--system-prompt", SYSTEM_PROMPT,
                "--json-schema", json.dumps(EXTRACTION_SCHEMA),
            ]
        return command

    def is_configured(self) -> bool:
        return bool(shutil.which(CLAUDE_CMD, path=ENV["PATH"]) or Path(CLAUDE_CMD).exists())

    def test_connection(self) -> Tuple[bool, str]:
        if not self.is_configured():
            return False, f"Claude CLI non trovato in {CLAUDE_CMD}."

        CLAUDE_WORKDIR.mkdir(exist_ok=True)
        command = self._command("Rispondi OK", schema=False)
        try:
            res = subprocess.run(
                command,
                cwd=CLAUDE_WORKDIR,
                capture_output=True,
                text=True,
                timeout=30,
                env=child_env(self.effort),
                stdin=subprocess.DEVNULL,
            )
            if res.returncode == 0:
                return True, f"Claude CLI funzionante ({self.model}, effort {self.effort})"
            return False, f"Errore Claude CLI: {cli_failure_detail(res.stderr, res.stdout, limit=300)}"
        except Exception as e:
            return False, str(e)

    def _run_single(self, request_text: str) -> Dict[str, Any]:
        CLAUDE_WORKDIR.mkdir(exist_ok=True)
        command = self._command(request_text, schema=True)
        try:
            res = subprocess.run(
                command,
                cwd=CLAUDE_WORKDIR,
                capture_output=True,
                text=True,
                timeout=CLAUDE_TIMEOUT,
                env=child_env(self.effort),
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Claude CLI timeout ({CLAUDE_TIMEOUT}s)")

        if res.returncode != 0:
            detail = cli_failure_detail(res.stderr, res.stdout)
            kind = classify_quota(detail)
            if kind:
                raise ClaudeQuotaPause(
                    f"Claude CLI exit {res.returncode}: {detail}",
                    resume_at=resume_after_quota(kind, detail),
                    reason=kind,
                )
            raise RuntimeError(f"Claude CLI exit {res.returncode}: {detail}")

        try:
            data = json.loads(res.stdout or "{}")
        except ValueError:
            raise RuntimeError(f"Output di Claude non è un JSON valido: {res.stdout[:150]}")

        self.last_usage = usage_from_cli_json(data)
        self.last_usage["requested_model"] = self.model
        self.last_usage["requested_effort"] = self.effort
        logger.info("claude_usage %s", json.dumps(self.last_usage, ensure_ascii=False))

        structured = data.get("structured_output")
        if not structured or not isinstance(structured, dict):
            raise RuntimeError(f"Claude non ha restituito structured_output: {data.get('result')}")

        return structured

    def analyze_tweet(self, tweet_data: Dict[str, Any]) -> Dict[str, Any]:
        """Un solo modello e un solo effort. La quota non viene ritentata."""
        author = f"{tweet_data.get('author_name', '')} (@{tweet_data.get('author_screen_name', '')})"
        text = tweet_data.get("tweet_text", "").strip()

        quoted = tweet_data.get("quoted_status")
        if isinstance(quoted, str):
            try:
                quoted = json.loads(quoted)
            except Exception:
                quoted = None

        request_text = f"Autore originale: {author}\nTesto del post:\n{text}\n"
        if quoted:
            request_text += f"\nTweet citato da @{quoted.get('author')}:\n{quoted.get('text')}\n"

        last_exc = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return self._run_single(request_text)
            except ClaudeQuotaPause:
                raise
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Tentativo Claude %s/%s fallito con %s effort %s: %s",
                    attempt, MAX_ATTEMPTS, self.model, self.effort, exc,
                )
                if attempt < MAX_ATTEMPTS:
                    time.sleep(RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)])

        raise RuntimeError(f"Analisi con Claude fallita dopo {MAX_ATTEMPTS} tentativi: {last_exc}")
