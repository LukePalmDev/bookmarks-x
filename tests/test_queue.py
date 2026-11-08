"""Conteggi di coda in sola lettura: niente testi e niente riscrittura del file."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.cli.app import cmd_queue
from src.storage.db import Database, format_queue_counts, read_queue_counts


class QueueCountTests(unittest.TestCase):
    def test_counts_omit_text_and_leave_the_file_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bookmarks.db"
            db = Database(path)
            db.save_tweet({
                "tweet_id": "1",
                "author_name": "N",
                "author_screen_name": "n",
                "tweet_text": "SEGRETO_TESTO_NON_STAMPARE",
                "tweet_url": "https://x.com/n/status/1",
                "media_urls": [],
            })
            db.update_analysis_failure("1", "SEGRETO_ERRORE token=abc")
            db.save_tweet({
                "tweet_id": "2",
                "author_name": "N",
                "author_screen_name": "n",
                "tweet_text": "ALTRO_SEGRETO",
                "tweet_url": "https://x.com/n/status/2",
                "media_urls": [],
            })
            db.update_analysis_success("2", "N (@n)", "Titolo", {"autore": "N (@n)", "titolo": "Titolo"})
            before = path.read_bytes()
            counts = read_queue_counts(path)
            text = format_queue_counts(counts)
            self.assertEqual(path.read_bytes(), before)
            self.assertNotIn("SEGRETO", text)
            self.assertNotIn("token", text)
            self.assertIn("failed_analysis=1", text)
            self.assertIn("analyzed=1", text)
            self.assertIn("pending_analysis=0", text)
            self.assertIn("total=2", text)
            uri = path.resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute("UPDATE bookmarks SET tweet_text = 'mutato'")
            finally:
                conn.close()

    def test_missing_database_exits_without_a_path(self):
        printed = []
        missing = Path("/tmp/bookmarks-x-queue-missing-ad2a.db")
        with patch("src.cli.app.console.print", side_effect=printed.append), \
                patch("src.cli.app.config.db_path", missing):
            code = cmd_queue()
        self.assertEqual(code, 1)
        self.assertEqual(printed, ["queue_error=FileNotFoundError"])
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
