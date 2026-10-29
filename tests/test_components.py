"""Test unitari per i componenti principali di X-Gemini Bookmarks."""

import json
import tempfile
import unittest
from pathlib import Path

from src.analyzer.prompts import PostAnalysisResult, build_analysis_prompt
from src.storage.db import Database
from src.storage.markdown_exporter import MarkdownExporter
from src.x_client.client import XBookmarksClient


class TestComponents(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_bookmarks.db"
        self.archive_dir = Path(self.temp_dir.name) / "archive"
        self.db = Database(self.db_path)
        self.exporter = MarkdownExporter(self.archive_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_database_crud_and_deduplication(self):
        tweet_data = {
            "tweet_id": "123456789",
            "author_name": "Mario Rossi",
            "author_screen_name": "mariorossi",
            "author_id": "999",
            "tweet_text": "Questo è un post di prova su Python e AI.",
            "tweet_url": "https://x.com/mariorossi/status/123456789",
            "created_at": "2026-10-29T10:00:00Z",
            "media_urls": ["https://pbs.twimg.com/media/test.jpg"],
        }

        # 1. Inserimento
        inserted = self.db.save_tweet(tweet_data)
        self.assertTrue(inserted)
        self.assertTrue(self.db.is_tweet_saved("123456789"))

        # 2. Deduplicazione (stesso ID non viene duplicato)
        inserted_again = self.db.save_tweet(tweet_data)
        self.assertFalse(inserted_again)

        # 3. Verifica statistiche
        stats = self.db.get_stats()
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["pending"], 1)
        self.assertEqual(stats["analyzed"], 0)

        # 4. Aggiornamento analisi Gemini
        analysis_json = {
            "autore": "Mario Rossi (@mariorossi)",
            "titolo": "Guida introduttiva su Python e Intelligenza Artificiale",
        }
        self.db.update_analysis_success(
            "123456789",
            author=analysis_json["autore"],
            title=analysis_json["titolo"],
            analysis_json=analysis_json,
        )

        saved = self.db.get_bookmark("123456789")
        self.assertIsNotNone(saved)
        self.assertEqual(saved["status"], "analyzed")
        self.assertEqual(saved["gemini_title"], "Guida introduttiva su Python e Intelligenza Artificiale")
        self.assertEqual(saved["gemini_author"], "Mario Rossi (@mariorossi)")

        stats_after = self.db.get_stats()
        self.assertEqual(stats_after["analyzed"], 1)
        self.assertEqual(stats_after["pending"], 0)

    def test_markdown_exporter(self):
        bookmark = {
            "tweet_id": "987654321",
            "author_name": "Giulia Bianchi",
            "author_screen_name": "giuliab",
            "tweet_text": "Novità straordinarie sul nuovo modello Gemini 2.5 Flash!",
            "tweet_url": "https://x.com/giuliab/status/987654321",
            "created_at": "2026-10-29",
            "bookmarked_at": "2026-10-29T12:00:00",
            "gemini_author": "Giulia Bianchi (@giuliab)",
            "gemini_title": "Annuncio e Novità di Gemini 2.5 Flash",
            "gemini_analysis_json": json.dumps({
                "autore": "Giulia Bianchi (@giuliab)",
                "titolo": "Annuncio e Novità di Gemini 2.5 Flash",
            }),
            "media_local_paths": "[]",
        }

        out_file = self.exporter.export_bookmark(bookmark)
        self.assertTrue(out_file.exists())
        content = out_file.read_text(encoding="utf-8")
        self.assertIn("# Annuncio e Novità di Gemini 2.5 Flash", content)
        self.assertIn("Giulia Bianchi (@giuliab)", content)
        self.assertIn("Novità straordinarie sul nuovo modello Gemini 2.5 Flash!", content)

    def test_prompt_generation_and_schema(self):
        tweet_data = {
            "author_name": "Test User",
            "author_screen_name": "testuser",
            "tweet_text": "Esempio testo per test prompt",
        }
        prompt = build_analysis_prompt(tweet_data)
        self.assertIn("Test User (@testuser)", prompt)
        self.assertIn("Esempio testo per test prompt", prompt)
        self.assertIn("autore", prompt)
        self.assertIn("titolo", prompt)

        # Test validazione schema
        valid_res = PostAnalysisResult(
            autore="Test User (@testuser)",
            titolo="Titolo di test",
        )
        self.assertEqual(valid_res.autore, "Test User (@testuser)")
        self.assertEqual(valid_res.titolo, "Titolo di test")

    def test_tweet_parsing(self):
        mock_raw = {
            "__typename": "Tweet",
            "rest_id": "11223344",
            "core": {
                "user_results": {
                    "result": {
                        "rest_id": "555",
                        "legacy": {
                            "name": "Dev Account",
                            "screen_name": "devacc",
                        },
                    }
                }
            },
            "legacy": {
                "full_text": "Short tweet text",
                "created_at": "Wed Oct 28 14:00:00 +0000 2026",
                "extended_entities": {
                    "media": [
                        {"media_url_https": "https://pbs.twimg.com/media/sample.jpg"}
                    ]
                },
            },
        }

        parsed = XBookmarksClient._parse_tweet_result(mock_raw)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["tweet_id"], "11223344")
        self.assertEqual(parsed["author_name"], "Dev Account")
        self.assertEqual(parsed["author_screen_name"], "devacc")
        self.assertEqual(parsed["tweet_text"], "Short tweet text")
        self.assertEqual(parsed["media_urls"], ["https://pbs.twimg.com/media/sample.jpg"])


if __name__ == "__main__":
    unittest.main()
