"""Client per l'analisi dei post tramite Claude CLI (Claude Code), configurato per Bookmarks-X."""

import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("bookmarks_x_claude")

CLAUDE_WORKDIR = Path(tempfile.gettempdir()) / "bookmarksx-claude"

ENV = {
    key: value for key, value in os.environ.items()
    if key != "CLAUDECODE" and (not key.startswith("CLAUDE_CODE_") or key == "CLAUDE_CODE_OAUTH_TOKEN")
}
ENV["PATH"] = os.pathsep.join([
    str(Path(__file__).resolve().parent.parent.parent / ".venv" / "bin"),
    str(Path.home() / ".local" / "bin"),
    os.environ.get("PATH", ""),
    "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
])

CLAUDE_CMD = shutil.which("claude", path=ENV["PATH"]) or "/Users/luke/.local/bin/claude"

FALLBACK_MODEL = "sonnet"
MAX_ATTEMPTS = 3
RETRY_DELAYS = (3, 10)
CLAUDE_TIMEOUT = 180

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


class ClaudeAnalyzer:
    """Interfaccia verso Claude CLI (Claude Code) per analizzare i post."""

    def __init__(self, model: str = "haiku"):
        self.model = model

    def is_configured(self) -> bool:
        return bool(shutil.which(CLAUDE_CMD, path=ENV["PATH"]) or Path(CLAUDE_CMD).exists())

    def test_connection(self) -> Tuple[bool, str]:
        if not self.is_configured():
            return False, f"Claude CLI non trovato in {CLAUDE_CMD}."

        CLAUDE_WORKDIR.mkdir(exist_ok=True)
        command = [
            CLAUDE_CMD, "-p", "Rispondi OK",
            "--model", self.model,
            "--tools", "",
            "--no-session-persistence",
            "--output-format", "json"
        ]

        try:
            res = subprocess.run(command, cwd=CLAUDE_WORKDIR, capture_output=True, text=True, timeout=30, env=ENV)
            if res.returncode == 0:
                return True, f"Claude CLI funzionante ({self.model})"
            return False, f"Errore Claude CLI: {res.stderr[:150]}"
        except Exception as e:
            return False, str(e)

    def _run_single(self, request_text: str, model: str) -> Dict[str, Any]:
        CLAUDE_WORKDIR.mkdir(exist_ok=True)
        command = [
            CLAUDE_CMD, "-p", request_text,
            "--model", model,
            "--tools", "",
            "--no-session-persistence",
            "--system-prompt", SYSTEM_PROMPT,
            "--output-format", "json",
            "--json-schema", json.dumps(EXTRACTION_SCHEMA),
        ]
        if model != FALLBACK_MODEL:
            command += ["--fallback-model", FALLBACK_MODEL]

        try:
            res = subprocess.run(
                command,
                cwd=CLAUDE_WORKDIR,
                capture_output=True,
                text=True,
                timeout=CLAUDE_TIMEOUT,
                env=ENV,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Claude CLI timeout ({CLAUDE_TIMEOUT}s)")

        if res.returncode != 0:
            raise RuntimeError(f"Claude CLI exit {res.returncode}: {res.stderr.strip()}")

        try:
            data = json.loads(res.stdout or "{}")
        except ValueError:
            raise RuntimeError(f"Output di Claude non è un JSON valido: {res.stdout[:150]}")

        structured = data.get("structured_output")
        if not structured or not isinstance(structured, dict):
            raise RuntimeError(f"Claude non ha restituito structured_output: {data.get('result')}")

        return structured

    def analyze_tweet(self, tweet_data: Dict[str, Any]) -> Dict[str, Any]:
        """Invia il contenuto del post a Claude CLI per estrarre l'analisi completa."""
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

        models = [self.model] + [FALLBACK_MODEL] * (MAX_ATTEMPTS - 1)
        last_exc = None

        for attempt, current_model in enumerate(models, 1):
            try:
                return self._run_single(request_text, current_model)
            except Exception as exc:
                last_exc = exc
                logger.warning(f"Tentativo Claude {attempt}/{len(models)} fallito con {current_model}: {exc}")
                if attempt < len(models):
                    time.sleep(RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)])

        raise RuntimeError(f"Analisi con Claude fallita dopo {MAX_ATTEMPTS} tentativi: {last_exc}")
