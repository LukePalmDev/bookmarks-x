"""Client per l'analisi intelligente dei post tramite Google Gemini."""

import json
import re
import time
from typing import Any, Dict, Optional, Tuple

from google import genai
from google.genai import types

from src.analyzer.prompts import PostAnalysisResult, build_analysis_prompt
from src.config import config


def clean_json_text(text: str) -> str:
    """Rimuove markdown backticks se presenti nella risposta del modello."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


class GeminiAnalyzer:
    """Interfaccia verso l'API Gemini per l'elaborazione dei contenuti."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or config.gemini_api_key
        self.model = model or config.gemini_model
        self.client: Optional[genai.Client] = None

        if self.api_key:
            self.client = genai.Client(api_key=self.api_key)

    def is_configured(self) -> bool:
        return bool(self.api_key and self.client)

    def test_connection(self) -> Tuple[bool, str]:
        """Verifica se la chiave API Gemini è valida e funzionante."""
        if not self.is_configured():
            return False, "GEMINI_API_KEY non configurata nel file .env"

        try:
            res = self.client.models.generate_content(
                model=self.model,
                contents="Rispondi solo con: 'OK'",
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=10,
                ),
            )
            text = (res.text or "").strip()
            return True, f"Connessione riuscita a Gemini ({self.model}): {text}"
        except Exception as e:
            return False, f"Errore Gemini ({self.model}): {e}"

    def analyze_tweet(self, tweet_data: Dict[str, Any], max_retries: int = 5) -> Dict[str, Any]:
        """Invia il contenuto del post a Gemini per determinare Autore e Titolo sintetico."""
        if not self.is_configured():
            raise RuntimeError(
                "GEMINI_API_KEY mancante. Configura la chiave in .env per completare l'analisi con Gemini."
            )

        prompt = build_analysis_prompt(tweet_data)

        last_error = None
        for attempt in range(max_retries):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=config.gemini_temperature,
                        max_output_tokens=config.gemini_max_tokens,
                        response_mime_type="application/json",
                    ),
                )

                raw_text = clean_json_text(response.text or "")
                parsed = json.loads(raw_text)

                # Validazione schema
                result = PostAnalysisResult(**parsed)
                return result.model_dump()

            except Exception as exc:
                last_error = exc
                err_str = str(exc)
                # Se è un errore di rate limit 429, attendi il reset della quota
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    wait_time = 20 + (attempt * 10)
                    time.sleep(wait_time)
                else:
                    time.sleep(2 ** attempt)

        raise RuntimeError(f"Analisi Gemini fallita dopo {max_retries} tentativi: {last_error}")
