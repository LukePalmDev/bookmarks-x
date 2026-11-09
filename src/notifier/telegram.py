"""Modulo per l'invio delle notifiche e dei risultati su Telegram (bookmarks-x)."""

import html
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional
import httpx

from src.analyzer.claude import format_usage_footer
from src.config import config

logger = logging.getLogger("bookmarks_x_telegram")

# Telegram conta i caratteri in unità UTF-16. Caption foto 1024, testo 4096.
PHOTO_CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4096


def telegram_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _cut_is_safe(text: str, cut: int) -> bool:
    """Il taglio non cade dentro un tag HTML né dentro un'entità &...;."""
    if cut <= 0 or cut > len(text):
        return False
    in_tag = False
    i = 0
    while i < cut:
        ch = text[i]
        if in_tag:
            if ch == ">":
                in_tag = False
            i += 1
            continue
        if ch == "<":
            in_tag = True
            i += 1
            continue
        if ch == "&":
            end = text.find(";", i + 1)
            if end == -1 or end >= cut:
                return False
            i = end + 1
            continue
        i += 1
    return not in_tag


def split_html_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Spezza solo oltre il limite, su un confine che non rompe HTML."""
    if telegram_len(text) <= limit:
        return [text] if text else []
    parts: list[str] = []
    rest = text
    while rest:
        if telegram_len(rest) <= limit:
            parts.append(rest)
            break
        # Finestra in caratteri Python, poi si restringe finché le unità UTF-16 stanno nel limite.
        window_end = min(len(rest), limit)
        while window_end > 0 and telegram_len(rest[:window_end]) > limit:
            window_end -= 1
        split_at = None
        for i in range(window_end, 0, -1):
            if rest[i - 1] == "\n" and _cut_is_safe(rest, i):
                split_at = i
                break
        if split_at is None:
            for i in range(window_end, 0, -1):
                if _cut_is_safe(rest, i):
                    split_at = i
                    break
        if not split_at:
            split_at = max(1, window_end)
        parts.append(rest[:split_at])
        rest = rest[split_at:]
    return parts


def short_photo_caption(title: str) -> str:
    """Didascalia corta, distinta dalla scheda. Il titolo si accorcia prima dell'escape."""
    plain = (title or "Segnalibro").strip().replace("\n", " ")
    if len(plain) > 180:
        plain = plain[:177].rstrip() + "…"
    return (
        f"🔖 <b>{html.escape(plain)}</b>\n"
        "<i>Scheda completa nel messaggio sotto.</i>"
    )


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

        parts = split_html_message(text)
        if not parts:
            return False
        url = f"{self.base_url}/sendMessage"
        ok = True
        for part in parts:
            payload = {
                "chat_id": self.chat_id,
                "text": part,
                "parse_mode": "HTML",
                "disable_web_page_preview": disable_preview,
            }
            try:
                with httpx.Client(timeout=15.0) as client:
                    res = client.post(url, json=payload)
                    if res.status_code == 200:
                        continue
                    logger.error(f"Telegram sendMessage failed: {res.status_code} - {res.text}")
            except Exception as e:
                logger.error(f"Errore invio messaggio Telegram: {e}")
            ok = False
            break
        return ok

    def send_photo(self, photo_path: Path, caption: str) -> bool:
        """Invia solo la foto. La didascalia deve essere già corta: non si taglia l'HTML."""
        if not self.is_configured() or not photo_path.exists():
            return False
        if telegram_len(caption) > PHOTO_CAPTION_LIMIT:
            logger.warning("Didascalia foto oltre il limite: non la taglio, invio senza testo.")
            caption = ""

        url = f"{self.base_url}/sendPhoto"
        try:
            with open(photo_path, "rb") as f:
                files = {"photo": (photo_path.name, f, "image/jpeg")}
                data = {
                    "chat_id": self.chat_id,
                    "caption": caption,
                    "parse_mode": "HTML",
                }
                with httpx.Client(timeout=30.0) as client:
                    res = client.post(url, data=data, files=files)
                    if res.status_code == 200:
                        return True
                    logger.warning(f"Telegram sendPhoto failed: {res.status_code} - {res.text}")
        except httpx.TransportError as e:
            logger.warning(f"Telegram sendPhoto incerto: {e}")
        except Exception as e:
            logger.warning(f"Errore invio foto Telegram: {e}")
        return False

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
            f"🔗 <a href=\"{tweet_url}\">Apri il post su X</a>\n"
            f"<i>{html.escape(format_usage_footer(bookmark.get('claude_usage')))}</i>"
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
                    self.send_photo(path_obj, short_photo_caption(title))
                    break

        return self.send_message(message)
