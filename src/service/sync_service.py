"""Servizio orchestratore per la sincronizzazione e l'analisi dei segnalibri di X."""

import asyncio
import logging
from typing import Any, Callable, Dict, Optional

from src.analyzer.claude import ClaudeAnalyzer
from src.analyzer.gemini import GeminiAnalyzer
from src.config import config
from src.notifier.telegram import TelegramNotifier
from src.storage.db import Database
from src.storage.markdown_exporter import MarkdownExporter
from src.storage.media import MediaDownloader
from src.x_client.client import XBookmarksClient

logger = logging.getLogger("bookmarks_x_sync")


def get_default_analyzer():
    """Restituisce il motore di analisi configurato ('claude' come in prompt-x o 'gemini')."""
    if config.analyzer_engine == "claude":
        return ClaudeAnalyzer(model=config.claude_model)
    return GeminiAnalyzer()


class SyncService:
    """Coordina l'intero ciclo di vita:
    1. Lettura dei bookmark da X (attuali e futuri)
    2. Download locale e deduplicazione nel database SQLite
    3. Download delle immagini/media allegati
    4. Analisi automatica con Claude CLI (o Gemini) (estrazione Autore e Titolo)
    5. Esportazione opzionale in formato Markdown
    6. Notifica istantanea su Telegram
    """

    def __init__(
        self,
        db: Optional[Database] = None,
        x_client: Optional[XBookmarksClient] = None,
        analyzer: Optional[Any] = None,
        media_downloader: Optional[MediaDownloader] = None,
        markdown_exporter: Optional[MarkdownExporter] = None,
        notifier: Optional[TelegramNotifier] = None,
    ):
        self.db = db or Database()
        self.x_client = x_client or XBookmarksClient()
        self.analyzer = analyzer or get_default_analyzer()
        self.media_downloader = media_downloader or MediaDownloader()
        self.markdown_exporter = markdown_exporter or MarkdownExporter()
        self.notifier = notifier or TelegramNotifier()

    async def run_sync(
        self,
        fetch_all: bool = False,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> Dict[str, Any]:
        """Esegue un ciclo di sincronizzazione completo."""
        stats = {
            "fetched_from_x": 0,
            "new_saved": 0,
            "media_downloaded": 0,
            "analyzed_by_gemini": 0,
            "analysis_errors": 0,
        }

        # 1. Recupero da X
        if fetch_all:
            raw_tweets = await self.x_client.fetch_all(
                progress_callback=lambda n: progress_callback("Scaricamento segnalibri storici da X", n, 0)
                if progress_callback
                else None
            )
        else:
            raw_tweets = await self.x_client.fetch_incremental(self.db.is_tweet_saved)

        stats["fetched_from_x"] = len(raw_tweets)

        # 2. Salvataggio e download media
        for idx, tweet in enumerate(raw_tweets):
            t_id = tweet["tweet_id"]
            is_new = self.db.save_tweet(tweet)
            if is_new:
                stats["new_saved"] += 1

                # Download media se presenti
                if config.download_media and tweet.get("media_urls"):
                    paths = await self.media_downloader.download_tweet_media(t_id, tweet["media_urls"])
                    if paths:
                        self.db.update_media_paths(t_id, paths)
                        stats["media_downloaded"] += len(paths)

            if progress_callback:
                progress_callback("Archiviazione post", idx + 1, len(raw_tweets))

        # 3. Analisi con Gemini dei post pendenti
        pending = self.db.get_pending_analysis(limit=100)
        can_analyze = self.analyzer.is_configured()

        if pending and can_analyze:
            for idx, item in enumerate(pending):
                t_id = item["tweet_id"]
                try:
                    res = self.analyzer.analyze_tweet(item)
                    author = res.get("autore", item.get("author_screen_name", ""))
                    title = res.get("titolo", "Post X")
                    self.db.update_analysis_success(t_id, author, title, res)
                    stats["analyzed_by_gemini"] += 1

                    # Aggiorna record per l'esportazione Markdown
                    item["gemini_author"] = author
                    item["gemini_title"] = title
                    item["gemini_analysis_json"] = res

                    if config.export_markdown:
                        self.markdown_exporter.export_bookmark(item)

                    if self.notifier.is_configured():
                        self.notifier.notify_bookmark(item)

                    # Con Claude CLI non c'è il limite di 5 RPM, pausa breve di 1.5s
                    await asyncio.sleep(1.5)

                except Exception as exc:
                    self.db.update_analysis_failure(t_id, str(exc))
                    stats["analysis_errors"] += 1

                if progress_callback:
                    progress_callback(f"Analisi con {config.analyzer_engine.capitalize()}", idx + 1, len(pending))

        stats["total_in_db"] = self.db.get_stats()["total"]
        stats["analyzer_configured"] = can_analyze
        stats["pending_analysis"] = len(self.db.get_pending_analysis())

        return stats

    async def run_watch_loop(
        self,
        interval_seconds: Optional[int] = None,
        on_cycle_complete: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        """Loop continuo in background per monitorare e scaricare automaticamente i nuovi post salvati."""
        interval = interval_seconds or config.sync_interval_seconds

        while True:
            try:
                stats = await self.run_sync(fetch_all=False)
                if on_cycle_complete:
                    on_cycle_complete(stats)
            except Exception as e:
                logger.error(f"Errore durante il ciclo di sync: {e}", exc_info=True)

            await asyncio.sleep(interval)
