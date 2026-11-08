"""Analisi locale tramite l'API HTTP di Ollama. Nessun fallback verso Claude o il cloud.

Il tag installato e verificato è ``gemma4:12b-it-q4_K_M``. Il motore di produzione
resta Claude finché ``engine`` non viene impostato esplicitamente a ``ollama``.
Non modifica impostazioni globali di Ollama: Trader usa lo stesso demone e lo stesso modello.
"""

import json
import logging
import threading
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

from src.analyzer.claude import CATEGORIES, EXTRACTION_SCHEMA
from src.analyzer.prompts import PostAnalysisResult
from src.config import config

logger = logging.getLogger("bookmarks_x_ollama")

# Misurato sul Mac mini 16GB: num_ctx 4096 lascia circa il 14% di RAM libera e usa la GPU al 100%.
NUM_CTX_CEILING = 4096
NUM_PREDICT_CEILING = 512
VERIFIED_MODEL_TAG = "gemma4:12b-it-q4_K_M"

# Un solo volo nel processo. Non coordina Trader (altro processo): il tetto per ciclo fa il resto.
_PROCESS_FLIGHT = threading.BoundedSemaphore(1)

SYSTEM_PROMPT = """Sei un analista editoriale. Estrai dal post un JSON con:
autore, titolo (italiano, max 15 parole), categoria (uno dei valori ammessi),
il_succo (1-2 frasi), punti_chiave (3 o 4), caso_d_uso (1 frase), tag (3-5, con #).
Niente markdown. Solo l'oggetto JSON."""


class OllamaError(RuntimeError):
    """Errore di analisi locale. ``stop_cycle`` ferma il batch senza altri invii.

    ``kind`` è ``config`` (niente rete), ``runtime`` (timeout, coda, HTTP) o ``schema``.
    """

    def __init__(self, message: str, *, stop_cycle: bool = False, kind: str = "runtime"):
        super().__init__(message)
        self.stop_cycle = stop_cycle
        self.kind = kind


# Testo fisso, non letto dal database. Lo smoke fa una sola inferenza su questo.
SYNTHETIC_TWEET = {
    "author_name": "Smoke",
    "author_screen_name": "smoke",
    "tweet_text": (
        "Prova sintetica locale: un segnalibro finto dice solo che un modello "
        "sul computer può riassumere una nota breve."
    ),
}

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_RUNTIME = 2
EXIT_SCHEMA = 3


class OllamaAnalyzer:
    """Client dell'endpoint documentato ``POST /api/chat`` su localhost."""

    single_flight = True

    def __init__(
        self,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[int] = None,
        max_attempts: Optional[int] = None,
        num_ctx: Optional[int] = None,
        num_predict: Optional[int] = None,
        max_input_chars: Optional[int] = None,
        think: Optional[bool] = None,
        temperature: Optional[float] = None,
        max_per_cycle: Optional[int] = None,
    ):
        self.model = config.ollama_model if model is None else model
        self.base_url = config.ollama_base_url if base_url is None else base_url
        self.timeout = config.ollama_timeout_seconds if timeout is None else int(timeout)
        self.max_attempts = config.ollama_max_attempts if max_attempts is None else max(1, int(max_attempts))
        self.num_ctx = config.ollama_num_ctx if num_ctx is None else int(num_ctx)
        self.num_predict = config.ollama_num_predict if num_predict is None else int(num_predict)
        self.max_input_chars = config.ollama_max_input_chars if max_input_chars is None else int(max_input_chars)
        self.think = config.ollama_think if think is None else bool(think)
        self.temperature = config.ollama_temperature if temperature is None else float(temperature)
        self.max_per_cycle = config.ollama_max_per_cycle if max_per_cycle is None else max(1, int(max_per_cycle))
        self.last_call: Dict[str, Any] = {}

    def _local_base(self) -> Optional[str]:
        parsed = urlparse(self.base_url or "")
        if parsed.scheme not in {"http", "https"}:
            return None
        if (parsed.hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"}:
            return None
        return self.base_url.rstrip("/")

    def is_configured(self) -> bool:
        return bool((self.model or "").strip() and self._local_base())

    def _bounded_ctx(self) -> int:
        requested = int(self.num_ctx)
        if requested > NUM_CTX_CEILING:
            logger.warning(
                "num_ctx %s sopra il tetto %s (16GB, misurato): uso %s. Non usare il massimo del modello.",
                requested,
                NUM_CTX_CEILING,
                NUM_CTX_CEILING,
            )
            return NUM_CTX_CEILING
        return max(512, requested)

    def _bounded_predict(self) -> int:
        return min(max(64, int(self.num_predict)), NUM_PREDICT_CEILING)

    def test_connection(self) -> tuple[bool, str]:
        """Controlla ``GET /api/tags``. Non genera token e non contende la GPU a Trader."""
        base = self._local_base()
        if not self.model:
            return False, "Modello Ollama non impostato."
        if not base:
            return False, f"Ollama remoto rifiutato ({self.base_url}). Consentito solo localhost."
        try:
            with httpx.Client(timeout=5.0) as client:
                res = client.get(f"{base}/api/tags")
        except Exception as exc:
            return False, f"Ollama non raggiungibile: {exc}"
        if res.status_code != 200:
            return False, f"Ollama /api/tags HTTP {res.status_code}: {res.text[:200]}"
        try:
            payload = res.json()
        except ValueError:
            return False, "Ollama /api/tags non è JSON."
        names = set()
        for item in payload.get("models") or []:
            if isinstance(item, dict):
                if item.get("name"):
                    names.add(item["name"])
                if item.get("model"):
                    names.add(item["model"])
        if self.model in names:
            return True, f"Ollama locale, modello {self.model} presente (nessuna generazione)"
        return False, f"Modello {self.model} assente in /api/tags"

    def _clip(self, text: str) -> str:
        text = (text or "").strip()
        limit = max(200, int(self.max_input_chars))
        if len(text) <= limit:
            return text
        return text[:limit] + "\n[testo troncato per restare in num_ctx 4096]"

    def _request_body(self, tweet_data: Dict[str, Any]) -> Dict[str, Any]:
        author = f"{tweet_data.get('author_name', '')} (@{tweet_data.get('author_screen_name', '')})"
        text = self._clip(str(tweet_data.get("tweet_text") or ""))
        quoted = tweet_data.get("quoted_status")
        if isinstance(quoted, str):
            try:
                quoted = json.loads(quoted)
            except Exception:
                quoted = None
        user = f"Autore originale: {author}\nTesto del post:\n{text}\n"
        if isinstance(quoted, dict):
            user += (
                f"\nTweet citato da @{quoted.get('author')}:\n"
                f"{self._clip(str(quoted.get('text') or ''))}\n"
            )
        user += (
            "\nCategorie ammesse: "
            + ", ".join(CATEGORIES)
            + "\nSchema JSON:\n"
            + json.dumps(EXTRACTION_SCHEMA, ensure_ascii=False)
        )
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            "stream": False,
            # ThinkValue: false chiede di non emettere thinking. Il default del modello è acceso.
            "think": bool(self.think),
            "format": EXTRACTION_SCHEMA,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self._bounded_ctx(),
                "num_predict": self._bounded_predict(),
            },
        }

    def _parse_content(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if payload.get("done_reason") == "length":
            raise OllamaError(
                "Risposta Ollama troncata (done_reason=length). "
                "think:false è nello schema API ma se il modello pensa lo stesso "
                "i token consumano num_predict (tetto 512) e il JSON si taglia.",
                stop_cycle=False,
                kind="schema",
            )
        message = payload.get("message") or {}
        content = message.get("content")
        if isinstance(content, dict):
            parsed = content
        else:
            raw = (content or "").strip()
            if raw.startswith("```"):
                raw = raw.strip("`")
                raw = raw.removeprefix("json").strip()
            try:
                parsed = json.loads(raw or "")
            except ValueError as exc:
                raise OllamaError(f"JSON Ollama non valido: {raw[:200]}", stop_cycle=False, kind="schema") from exc
        if not isinstance(parsed, dict):
            raise OllamaError("Ollama non ha restituito un oggetto JSON", stop_cycle=False, kind="schema")
        try:
            result = PostAnalysisResult(**parsed).model_dump()
        except Exception as exc:
            raise OllamaError(f"Schema analisi rifiutato: {exc}", stop_cycle=False, kind="schema") from exc
        if result.get("categoria") not in CATEGORIES:
            raise OllamaError(
                f"Categoria non ammessa: {result.get('categoria')}",
                stop_cycle=False,
                kind="schema",
            )
        if not result.get("autore") or not result.get("titolo"):
            raise OllamaError("Autore o titolo vuoti", stop_cycle=False, kind="schema")
        return result

    def _once(self, tweet_data: Dict[str, Any]) -> Dict[str, Any]:
        base = self._local_base()
        if not (self.model or "").strip():
            raise OllamaError("Modello Ollama non configurato", stop_cycle=True, kind="config")
        if not base:
            raise OllamaError(f"Ollama remoto rifiutato ({self.base_url})", stop_cycle=True, kind="config")
        if not _PROCESS_FLIGHT.acquire(timeout=self.timeout):
            raise OllamaError(
                "Inferenza locale già in corso: richiesta abbandonata per non sovraccaricare Ollama",
                stop_cycle=True,
            )
        try:
            body = self._request_body(tweet_data)
            self.last_call = {
                "model": body["model"],
                "think": body["think"],
                "num_ctx": body["options"]["num_ctx"],
                "num_predict": body["options"]["num_predict"],
                "done_reason": "",
                "thinking_emitted": False,
                "eval_count": None,
            }
            timeout = httpx.Timeout(connect=5.0, read=self.timeout, write=30.0, pool=5.0)
            try:
                with httpx.Client(timeout=timeout) as client:
                    res = client.post(f"{base}/api/chat", json=body)
            except httpx.TimeoutException as exc:
                raise OllamaError(f"Ollama timeout ({self.timeout}s): {exc}", stop_cycle=True) from exc
            except httpx.TransportError as exc:
                raise OllamaError(f"Ollama non raggiungibile: {exc}", stop_cycle=True) from exc
            if res.status_code != 200:
                detail = (res.text or "")[:300]
                raise OllamaError(f"Ollama HTTP {res.status_code}: {detail}", stop_cycle=True)
            try:
                payload = res.json()
            except ValueError as exc:
                raise OllamaError(f"Corpo Ollama non JSON: {res.text[:200]}", stop_cycle=True) from exc
            message = payload.get("message") or {}
            thinking = message.get("thinking")
            self.last_call["done_reason"] = payload.get("done_reason") or ""
            self.last_call["thinking_emitted"] = bool(isinstance(thinking, str) and thinking.strip())
            self.last_call["eval_count"] = payload.get("eval_count")
            return self._parse_content(payload)
        finally:
            _PROCESS_FLIGHT.release()

    def analyze_tweet(self, tweet_data: Dict[str, Any]) -> Dict[str, Any]:
        """Un tentativo in più solo per JSON malformato. Timeout e 5xx non vengono ripetuti."""
        last: Optional[BaseException] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self._once(tweet_data)
            except OllamaError as exc:
                last = exc
                logger.warning("Tentativo Ollama %s/%s fallito: %s", attempt, self.max_attempts, exc)
                if exc.stop_cycle or attempt >= self.max_attempts:
                    raise
                time.sleep(min(2 * attempt, 5))
        raise OllamaError(f"Analisi Ollama fallita: {last}", stop_cycle=True)

    def synthetic_smoke(self) -> Dict[str, Any]:
        """Una sola inferenza sul testo fisso. Non apre SQLite, Telegram o X."""
        report: Dict[str, Any] = {
            "exit_code": EXIT_CONFIG,
            "model": self.model,
            "think": bool(self.think),
            "num_ctx": None,
            "num_predict": None,
            "done_reason": "",
            "thinking_emitted": False,
            "eval_count": None,
            "schema_ok": False,
            "detail": "",
        }
        if not self.is_configured():
            report["detail"] = "Ollama non configurato o URL non locale"
            return report
        try:
            self._once(SYNTHETIC_TWEET)
        except OllamaError as exc:
            report.update(self.last_call)
            report["schema_ok"] = False
            report["detail"] = str(exc)
            kind = getattr(exc, "kind", "runtime")
            report["exit_code"] = {"config": EXIT_CONFIG, "schema": EXIT_SCHEMA}.get(kind, EXIT_RUNTIME)
            return report
        report.update(self.last_call)
        report["schema_ok"] = True
        report["exit_code"] = EXIT_OK
        report["detail"] = "schema valido"
        return report


def format_smoke_report(report: Dict[str, Any]) -> str:
    """Righe stabili, senza testo del segnalibro e senza il thinking del modello."""
    detail = (report.get("detail") or "").replace("\n", " ")[:300]
    eval_count = report.get("eval_count")
    lines = [
        f"exit={report.get('exit_code')}",
        f"model={report.get('model') or ''}",
        f"think={str(bool(report.get('think'))).lower()}",
        f"num_ctx={report.get('num_ctx') if report.get('num_ctx') is not None else ''}",
        f"num_predict={report.get('num_predict') if report.get('num_predict') is not None else ''}",
        f"done_reason={report.get('done_reason') or ''}",
        f"thinking_emitted={str(bool(report.get('thinking_emitted'))).lower()}",
        f"eval_count={'' if eval_count is None else eval_count}",
        f"schema_ok={str(bool(report.get('schema_ok'))).lower()}",
        f"detail={detail}",
    ]
    if report.get("thinking_emitted"):
        lines.append(
            "warning=think:false non rispettato; num_predict 512 può troncare il JSON dei post veri"
        )
    return "\n".join(lines)
