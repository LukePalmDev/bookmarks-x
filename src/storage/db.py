"""Gestione del database SQLite per l'archiviazione dei segnalibri di X e l'analisi Gemini."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config import config


class Database:
    """Interfaccia verso il database SQLite locale per archiviare i bookmark e le analisi."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or config.db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bookmarks (
                    tweet_id TEXT PRIMARY KEY,
                    author_name TEXT,
                    author_screen_name TEXT,
                    author_id TEXT,
                    tweet_text TEXT,
                    tweet_url TEXT,
                    created_at TEXT,
                    bookmarked_at TEXT,
                    media_urls TEXT,
                    media_local_paths TEXT,
                    quoted_status TEXT,
                    raw_json TEXT,
                    status TEXT DEFAULT 'pending_analysis',
                    gemini_title TEXT,
                    gemini_author TEXT,
                    gemini_analysis_json TEXT,
                    analyzed_at TEXT,
                    analysis_error TEXT
                );
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON bookmarks(status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bookmarked_at ON bookmarks(bookmarked_at);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_author ON bookmarks(author_screen_name);")

    def is_tweet_saved(self, tweet_id: str) -> bool:
        """Controlla se il tweet è già presente nel database."""
        with self._get_connection() as conn:
            cur = conn.execute("SELECT 1 FROM bookmarks WHERE tweet_id = ? LIMIT 1;", (tweet_id,))
            return cur.fetchone() is not None

    def save_tweet(self, tweet_data: Dict[str, Any]) -> bool:
        """Salva un nuovo tweet nel database. Ritorna True se è stato inserito, False se già esistente."""
        tweet_id = tweet_data["tweet_id"]
        if self.is_tweet_saved(tweet_id):
            return False

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO bookmarks (
                    tweet_id, author_name, author_screen_name, author_id,
                    tweet_text, tweet_url, created_at, bookmarked_at,
                    media_urls, media_local_paths, quoted_status, raw_json,
                    status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_analysis');
                """,
                (
                    tweet_id,
                    tweet_data.get("author_name", ""),
                    tweet_data.get("author_screen_name", ""),
                    tweet_data.get("author_id", ""),
                    tweet_data.get("tweet_text", ""),
                    tweet_data.get("tweet_url", f"https://x.com/i/web/status/{tweet_id}"),
                    tweet_data.get("created_at", ""),
                    datetime.now().isoformat(),
                    json.dumps(tweet_data.get("media_urls", [])),
                    json.dumps(tweet_data.get("media_local_paths", [])),
                    json.dumps(tweet_data.get("quoted_status", {})) if tweet_data.get("quoted_status") else None,
                    json.dumps(tweet_data.get("raw_json", {})),
                ),
            )
        return True

    def update_media_paths(self, tweet_id: str, paths: List[str]) -> None:
        """Aggiorna i percorsi locali dei media scaricati."""
        with self._get_connection() as conn:
            conn.execute(
                "UPDATE bookmarks SET media_local_paths = ? WHERE tweet_id = ?;",
                (json.dumps(paths), tweet_id),
            )

    def update_analysis_success(
        self,
        tweet_id: str,
        author: str,
        title: str,
        analysis_json: Dict[str, Any],
    ) -> None:
        """Salva l'esito dell'analisi effettuata da Gemini."""
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE bookmarks SET
                    status = 'analyzed',
                    gemini_author = ?,
                    gemini_title = ?,
                    gemini_analysis_json = ?,
                    analyzed_at = ?,
                    analysis_error = NULL
                WHERE tweet_id = ?;
                """,
                (
                    author,
                    title,
                    json.dumps(analysis_json, ensure_ascii=False),
                    datetime.now().isoformat(),
                    tweet_id,
                ),
            )

    def update_analysis_failure(self, tweet_id: str, error: str) -> None:
        """Registra un errore durante l'analisi Gemini."""
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE bookmarks SET
                    status = 'failed_analysis',
                    analysis_error = ?,
                    analyzed_at = ?
                WHERE tweet_id = ?;
                """,
                (error, datetime.now().isoformat(), tweet_id),
            )

    def get_pending_analysis(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Recupera i segnalibri in attesa di analisi."""
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                SELECT * FROM bookmarks
                WHERE status IN ('pending_analysis', 'failed_analysis')
                ORDER BY bookmarked_at DESC
                LIMIT ?;
                """,
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]

    def get_bookmark(self, tweet_id: str) -> Optional[Dict[str, Any]]:
        """Recupera un singolo segnalibro tramite ID."""
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM bookmarks WHERE tweet_id = ?;", (tweet_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def get_recent_bookmarks(self, limit: int = 20, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Recupera gli ultimi segnalibri salvati."""
        with self._get_connection() as conn:
            if status:
                cur = conn.execute(
                    "SELECT * FROM bookmarks WHERE status = ? ORDER BY bookmarked_at DESC LIMIT ?;",
                    (status, limit),
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM bookmarks ORDER BY bookmarked_at DESC LIMIT ?;",
                    (limit,),
                )
            return [dict(row) for row in cur.fetchall()]

    def get_stats(self) -> Dict[str, int]:
        """Restituisce le statistiche attuali del database."""
        with self._get_connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM bookmarks;").fetchone()[0]
            analyzed = conn.execute("SELECT COUNT(*) FROM bookmarks WHERE status = 'analyzed';").fetchone()[0]
            pending = conn.execute("SELECT COUNT(*) FROM bookmarks WHERE status = 'pending_analysis';").fetchone()[0]
            failed = conn.execute("SELECT COUNT(*) FROM bookmarks WHERE status = 'failed_analysis';").fetchone()[0]
            return {
                "total": total,
                "analyzed": analyzed,
                "pending": pending,
                "failed": failed,
            }
