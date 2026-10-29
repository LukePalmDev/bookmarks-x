"""Modulo per l'invio delle notifiche e dei risultati su Telegram (bookmarks-x)."""

import html
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional
import httpx

from src.config import config

logger = logging.getLogger("bookmarks_x_telegram")


class TelegramNotifier:
    """Invia notifiche eleganti e formattate su Telegram per ogni nuovo post salvato e analizzato."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        enabled: Optional[bool] = None,
    ):
        self.bot_token = bot_token or config.telegram_bot_token
        self.chat_id = chat_id or config.telegram_chat_id
        self.enabled = config.telegram_enabled if enabled is None else enabled
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    def is_configured(self) -> bool:
        return bool(self.enabled and self.bot_token and self.chat_id)

    def test_connection(self) -> tuple[bool, str]:
        if not self.is_configured():
            return False, "Telegram non configurato (TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID mancante)"

        try:
            with httpx.Client(timeout=10.0) as client:
                res = client.get(f"{self.base_url}/getMe")
                if res.status_code == 200:
                    data = res.json()
                    bot_name = data.get("result", {}).get("username", "Bot")
                    return True, f"@{bot_name}"
                return False, f"HTTP {res.status_code}: {res.text[:100]}"
        except Exception as e:
            return False, str(e)

    def send_message(self, text: str, disable_preview: bool = False) -> bool:
        if not self.is_configured():
            return False

        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": disable_preview,
        }

        try:
            with httpx.Client(timeout=15.0) as client:
                res = client.post(url, json=payload)
                if res.status_code == 200:
                    return True
                logger.error(f"Telegram sendMessage failed: {res.status_code} - {res.text}")
        except Exception as e:
            logger.error(f"Errore invio messaggio Telegram: {e}")
        return False

    def send_photo(self, photo_path: Path, caption: str) -> bool:
        if not self.is_configured() or not photo_path.exists():
            return False

        url = f"{self.base_url}/sendPhoto"
        # Telegram supporta fino a 1024 caratteri nelle didascalie delle foto
        if len(caption) > 1020:
            short_caption = caption[:1015] + "..."
        else:
            short_caption = caption

        try:
            with open(photo_path, "rb") as f:
                files = {"photo": (photo_path.name, f, "image/jpeg")}
                data = {
                    "chat_id": self.chat_id,
                    "caption": short_caption,
                    "parse_mode": "HTML",
                }
                with httpx.Client(timeout=30.0) as client:
                    res = client.post(url, data=data, files=files)
                    if res.status_code == 200:
                        # Se il messaggio originale era molto lungo, invia il resto come testo
                        if len(caption) > 1020:
                            self.send_message(caption)
                        return True
                    logger.warning(f"Telegram sendPhoto failed: {res.status_code} - {res.text}")
        except Exception as e:
            logger.warning(f"Errore invio foto Telegram: {e}")

        # Fallback a testo normale
        return self.send_message(caption)

    def notify_bookmark(self, bookmark: Dict[str, Any]) -> bool:
        """Costruisce e recapita la notifica strutturata per un post analizzato con Claude."""
        if not self.is_configured():
            return False

        # Recupera dati strutturati dall'analisi
        analysis = {}
        raw_analysis = bookmark.get("gemini_analysis_json")
        if raw_analysis:
            try:
                analysis = json.loads(raw_analysis) if isinstance(raw_analysis, str) else raw_analysis
            except Exception:
                analysis = {}

        author = analysis.get("autore") or bookmark.get("gemini_author") or f"{bookmark.get('author_name', '')} (@{bookmark.get('author_screen_name', '')})"
        title = analysis.get("titolo") or bookmark.get("gemini_title") or "Post salvato su X"
        category = analysis.get("categoria", "📌 Risorsa X")
        il_succo = analysis.get("il_succo", bookmark.get("tweet_text", "")[:200])
        punti = analysis.get("punti_chiave", [])
        caso_d_uso = analysis.get("caso_d_uso", "")
        tags_list = analysis.get("tag", [])

        tweet_url = bookmark.get("tweet_url", "")

        # Formatta i punti chiave
        punti_str = ""
        if punti:
            punti_str = "\n💡 <b>Punti Chiave:</b>\n" + "\n".join([f"• {html.escape(p)}" for p in punti]) + "\n"

        # Formatta i tag
        tags_str = " ".join([f"<code>{html.escape(t)}</code>" for t in tags_list])

        caso_str = f"🎯 <b>Utile per:</b> {html.escape(caso_d_uso)}\n" if caso_d_uso else ""

        message = (
            f"🔖 <b>{html.escape(title)}</b>\n"
            f"📁 <i>{html.escape(category)}</i> • 👤 <a href=\"{tweet_url}\">{html.escape(author)}</a>\n\n"
            f"⚡ <b>Il Succo:</b>\n{html.escape(il_succo)}\n"
            f"{punti_str}\n"
            f"{caso_str}"
            f"{tags_str}\n\n"
            f"🔗 <a href=\"{tweet_url}\">Apri il post su X</a>"
        )

        # Se sono presenti immagini e send_photos è attivo, invia con foto
        if config.telegram_send_photos:
            media_paths = []
            raw_paths = bookmark.get("media_local_paths")
            if raw_paths:
                try:
                    media_paths = json.loads(raw_paths) if isinstance(raw_paths, str) else raw_paths
                except Exception:
                    pass

            for p in media_paths:
                path_obj = Path(p)
                if path_obj.exists() and path_obj.stat().st_size > 0:
                    return self.send_photo(path_obj, message)

        return self.send_message(message)
