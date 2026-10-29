"""Servizio orchestratore per la sincronizzazione e l'analisi dei segnalibri di X."""

import asyncio
import json
import logging
import subprocess
from typing import Any, Callable, Dict, Optional

from src.analyzer.claude import ClaudeAnalyzer
from src.analyzer.gemini import GeminiAnalyzer
from src.config import BASE_DIR, config
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


def sync_site_to_git(commit_msg: str = "Update bookmarks site data") -> bool:
    """Rigenera e pusha i file docs/ su GitHub Pages se ci sono modifiche."""
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "docs/"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
        )
        if not status.stdout.strip():
            return True

        subprocess.run(["git", "add", "docs/"], cwd=BASE_DIR, check=True)
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=BASE_DIR, check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=BASE_DIR, check=True, timeout=30)
        return True
    except Exception as e:
        logger.warning(f"Push su GitHub Pages non riuscito: {e}")
        return False


class SyncService:
    """Coordina l'intero ciclo di vita:
    1. Lettura dei bookmark da X (attuali e futuri)
    2. Download locale e deduplicazione nel database SQLite
    3. Download delle immagini/media allegati
    4. Analisi automatica con Claude CLI (o Gemini) (estrazione Autore, Titolo, Categoria, Punti Chiave)
    5. Esportazione in formato Markdown
    6. Notifica istantanea su Telegram
    7. Sincronizzazione a specchio (Mirroring) e aggiornamento GitHub Pages
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

    def delete_bookmark_fully(self, tweet_id: str) -> bool:
        """Rimuove completamente un post da:
        1. Media scaricati in data/media/
        2. Scheda Markdown in data/archive/
        3. Database SQLite
        """
        item = self.db.get_bookmark(tweet_id)
        if not item:
            return False

        # 1. Rimuovi file media
        media_paths = []
        raw_paths = item.get("media_local_paths")
        if raw_paths:
            try:
                media_paths = json.loads(raw_paths) if isinstance(raw_paths, str) else raw_paths
            except Exception:
                media_paths = []
        self.media_downloader.delete_tweet_media(tweet_id, media_paths)

        # 2. Rimuovi file Markdown
        self.markdown_exporter.delete_bookmark_file(tweet_id)

        # 3. Rimuovi dal Database
        self.db.delete_bookmark(tweet_id)
        return True

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
            raw_tweets, _ = await self.x_client.fetch_all(
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

        # 3. Analisi dei post pendenti
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

                    await asyncio.sleep(1.5)

                except Exception as exc:
                    self.db.update_analysis_failure(t_id, str(exc))
                    stats["analysis_errors"] += 1

                if progress_callback:
                    progress_callback(f"Analisi con {config.analyzer_engine.capitalize()}", idx + 1, len(pending))

        stats["total_in_db"] = self.db.get_stats()["total"]
        stats["analyzer_configured"] = can_analyze
        stats["pending_analysis"] = len(self.db.get_pending_analysis())

        # Aggiorna i dati per l'app web mobile GitHub Pages
        try:
            from src.storage.site_generator import export_site_data, generate_index_html
            export_site_data(self.db)
            generate_index_html()
            sync_site_to_git(f"Sync: {stats['new_saved']} nuovi post")
        except Exception as e:
            logger.warning(f"Errore aggiornamento dati sito web: {e}")

        return stats

    async def run_mirror(
        self,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> Dict[str, Any]:
        """Esegue una sincronizzazione a specchio (Mirroring):
        1. Scarica l'elenco completo dei segnalibri attuali da X.
        2. Salva eventuali nuovi post non ancora presenti.
        3. Se la scansione da X è completa al 100%, individua i post presenti nel DB locale
           ma non più presenti nei segnalibri di X e li elimina (DB, Media, Markdown, Sito).
        4. Analizza eventuali post pendenti con Claude CLI.
        5. Aggiorna il sito e pusha le modifiche su GitHub Pages.
        """
        stats = {
            "fetched_from_x": 0,
            "new_saved": 0,
            "deleted_removed": 0,
            "media_downloaded": 0,
            "analyzed": 0,
            "total_in_db": 0,
            "scan_complete": False,
        }

        if progress_callback:
            progress_callback("Scansione segnalibri attuali su X", 0, 0)

        raw_tweets, is_complete = await self.x_client.fetch_all(
            progress_callback=lambda n: progress_callback("Scansione segnalibri su X", n, 0)
            if progress_callback
            else None
        )
        stats["fetched_from_x"] = len(raw_tweets)
        stats["scan_complete"] = is_complete
        current_x_ids = {t["tweet_id"] for t in raw_tweets}

        # Salva nuovi post se presenti
        for idx, tweet in enumerate(raw_tweets):
            t_id = tweet["tweet_id"]
            is_new = self.db.save_tweet(tweet)
            if is_new:
                stats["new_saved"] += 1
                if config.download_media and tweet.get("media_urls"):
                    paths = await self.media_downloader.download_tweet_media(t_id, tweet["media_urls"])
                    if paths:
                        self.db.update_media_paths(t_id, paths)
                        stats["media_downloaded"] += len(paths)

            if progress_callback:
                progress_callback("Verifica e salvataggio post", idx + 1, len(raw_tweets))

        # Rimozione dei post eliminati da X (solo con scansione 100% integra)
        if is_complete:
            local_ids = set(self.db.get_all_tweet_ids())
            removed_ids = local_ids - current_x_ids

            if removed_ids:
                for idx, t_id in enumerate(removed_ids):
                    self.delete_bookmark_fully(t_id)
                    stats["deleted_removed"] += 1
                    if progress_callback:
                        progress_callback("Rimozione post eliminati su X", idx + 1, len(removed_ids))
        else:
            logger.warning("Scansione X parziale: rimozione post sospesa per precauzione.")

        # Analisi post pendenti
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
                    stats["analyzed"] += 1

                    item["gemini_author"] = author
                    item["gemini_title"] = title
                    item["gemini_analysis_json"] = res

                    if config.export_markdown:
                        self.markdown_exporter.export_bookmark(item)

                    if self.notifier.is_configured():
                        self.notifier.notify_bookmark(item)

                    await asyncio.sleep(1.5)
                except Exception as exc:
                    self.db.update_analysis_failure(t_id, str(exc))

                if progress_callback:
                    progress_callback(f"Analisi con {config.analyzer_engine.capitalize()}", idx + 1, len(pending))

        stats["total_in_db"] = self.db.get_stats()["total"]

        # Rigenera sito e invia a GitHub
        try:
            from src.storage.site_generator import export_site_data, generate_index_html
            export_site_data(self.db)
            generate_index_html()
            sync_site_to_git(f"Mirror: +{stats['new_saved']} nuovi, -{stats['deleted_removed']} rimossi")
        except Exception as e:
            logger.warning(f"Errore aggiornamento dati sito web: {e}")

        return stats

    async def run_watch_loop(
        self,
        interval_seconds: Optional[int] = None,
        on_cycle_complete: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        """Loop continuo in background per monitorare nuovi post e riconciliare periodicamente le rimozioni."""
        interval = interval_seconds or config.sync_interval_seconds
        cycle_count = 0
        # Esegue il mirror completo ogni 12 cicli (es. 12 * 5 minuti = ogni ora)
        mirror_every_n = 12

        while True:
            try:
                cycle_count += 1
                if cycle_count >= mirror_every_n:
                    cycle_count = 0
                    stats = await self.run_mirror()
                else:
                    stats = await self.run_sync(fetch_all=False)

                if on_cycle_complete:
                    on_cycle_complete(stats)
            except Exception as e:
                logger.error(f"Errore durante il ciclo di sync: {e}", exc_info=True)

            await asyncio.sleep(interval)
