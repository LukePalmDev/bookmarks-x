"""Ollama locale, solo mock. Nessuna chiamata a localhost, X, Telegram o Claude."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, PropertyMock, patch

import httpx

_dumps = json.dumps

from src.analyzer.ollama import (
    NUM_CTX_CEILING,
    VERIFIED_MODEL_TAG,
    OllamaAnalyzer,
    OllamaError,
    _PROCESS_FLIGHT,
)
from src.config import AppConfig
from src.service.sync_service import SyncService
from src.storage.db import Database


OK = {
    "autore": "N (@n)",
    "titolo": "Titolo di prova",
    "categoria": "📰 News & Trend",
    "il_succo": "Sintesi concreta.",
    "punti_chiave": ["uno", "due", "tre"],
    "caso_d_uso": "Utile nei test.",
    "tag": ["#ai", "#locale", "#test"],
}


class FakeResp:
    def __init__(self, code=200, payload=None, text=None):
        self.status_code = code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {})

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeClient:
    posts = []
    gets = []
    mode = "ok"

    def __init__(self, timeout=None):
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, json=None):
        FakeClient.posts.append({"url": url, "json": json})
        mode = FakeClient.mode
        if mode == "timeout":
            raise httpx.ReadTimeout("read timed out")
        if mode == "down":
            raise httpx.ConnectError("connection refused")
        if mode == "503":
            return FakeResp(503, {"error": "model runner busy"}, text='{"error":"model runner busy"}')
        if mode == "bad":
            return FakeResp(200, {"message": {"content": "[]"}, "done": True, "done_reason": "stop"})
        if mode == "then_ok" and len(FakeClient.posts) == 1:
            return FakeResp(200, {"message": {"content": "non proprio json"}, "done": True, "done_reason": "stop"})
        if mode == "length_then_ok" and len(FakeClient.posts) == 1:
            return FakeResp(200, {"message": {"content": "{"}, "done": True, "done_reason": "length"})
        return FakeResp(
            200,
            {"message": {"role": "assistant", "content": _dumps(OK, ensure_ascii=False)}, "done": True, "done_reason": "stop"},
        )

    def get(self, url):
        FakeClient.gets.append(url)
        if FakeClient.mode == "missing":
            return FakeResp(200, {"models": [{"name": "other:latest"}]})
        return FakeResp(200, {"models": [{"name": VERIFIED_MODEL_TAG, "model": VERIFIED_MODEL_TAG}]})


def analyzer(**kwargs):
    params = {"model": VERIFIED_MODEL_TAG, "base_url": "http://127.0.0.1:11434", "timeout": 30}
    params.update(kwargs)
    return OllamaAnalyzer(**params)


class OllamaClientTests(unittest.TestCase):
    def setUp(self):
        FakeClient.posts = []
        FakeClient.gets = []
        FakeClient.mode = "ok"
        self.patch = patch("src.analyzer.ollama.httpx.Client", FakeClient)
        self.patch.start()
        self.sleep = patch("src.analyzer.ollama.time.sleep")
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()
        self.patch.stop()

    def test_success_payload_is_bounded_and_does_not_call_claude(self):
        def forbid_claude(*args, **kwargs):
            raise AssertionError("Claude CLI non deve partire")

        with patch("src.analyzer.claude.subprocess.run", forbid_claude):
            result = analyzer(num_ctx=262144, num_predict=9000, max_input_chars=2000).analyze_tweet(
                {"author_name": "N", "author_screen_name": "n", "tweet_text": "A" * 5000}
            )
        self.assertEqual(result["titolo"], "Titolo di prova")
        body = FakeClient.posts[0]["json"]
        self.assertEqual(body["model"], VERIFIED_MODEL_TAG)
        self.assertIs(body["stream"], False)
        self.assertIs(body["think"], False)
        self.assertNotIn("keep_alive", body)
        self.assertEqual(body["options"]["num_ctx"], NUM_CTX_CEILING)
        self.assertLessEqual(body["options"]["num_predict"], 512)
        self.assertEqual(body["options"]["temperature"], 0)
        self.assertIn("autore", body["format"]["required"])
        user = body["messages"][1]["content"]
        self.assertNotIn("A" * 3000, user)
        self.assertIn("[testo troncato", user)
        self.assertEqual(len(FakeClient.posts), 1)
        self.assertTrue(FakeClient.posts[0]["url"].endswith("/api/chat"))

    def test_malformed_json_is_retried_then_fails_without_stop(self):
        FakeClient.mode = "bad"
        with self.assertRaises(OllamaError) as ctx:
            analyzer(max_attempts=2).analyze_tweet({"tweet_text": "ciao", "author_name": "N", "author_screen_name": "n"})
        self.assertFalse(ctx.exception.stop_cycle)
        self.assertEqual(len(FakeClient.posts), 2)

    def test_malformed_then_success(self):
        FakeClient.mode = "then_ok"
        result = analyzer(max_attempts=2).analyze_tweet({"tweet_text": "ciao", "author_name": "N", "author_screen_name": "n"})
        self.assertEqual(result["autore"], "N (@n)")
        self.assertEqual(len(FakeClient.posts), 2)

    def test_truncated_generation_retries(self):
        FakeClient.mode = "length_then_ok"
        result = analyzer(max_attempts=2).analyze_tweet({"tweet_text": "ciao", "author_name": "N", "author_screen_name": "n"})
        self.assertEqual(result["titolo"], "Titolo di prova")
        self.assertEqual(len(FakeClient.posts), 2)

    def test_timeout_is_not_retried_and_stops_the_cycle(self):
        FakeClient.mode = "timeout"
        with self.assertRaises(OllamaError) as ctx:
            analyzer(max_attempts=3).analyze_tweet({"tweet_text": "ciao", "author_name": "N", "author_screen_name": "n"})
        self.assertTrue(ctx.exception.stop_cycle)
        self.assertIn("timeout", str(ctx.exception))
        self.assertEqual(len(FakeClient.posts), 1)

    def test_connection_error_and_http_503_stop_without_retry(self):
        FakeClient.mode = "down"
        with self.assertRaises(OllamaError) as ctx:
            analyzer(max_attempts=3).analyze_tweet({"tweet_text": "ciao", "author_name": "N", "author_screen_name": "n"})
        self.assertTrue(ctx.exception.stop_cycle)
        self.assertEqual(len(FakeClient.posts), 1)

        FakeClient.posts.clear()
        FakeClient.mode = "503"
        with self.assertRaises(OllamaError) as ctx:
            analyzer(max_attempts=3).analyze_tweet({"tweet_text": "ciao", "author_name": "N", "author_screen_name": "n"})
        self.assertTrue(ctx.exception.stop_cycle)
        self.assertIn("503", str(ctx.exception))
        self.assertIn("busy", str(ctx.exception))
        self.assertEqual(len(FakeClient.posts), 1)

    def test_remote_host_is_refused_without_http(self):
        az = analyzer(base_url="https://example.com")
        self.assertFalse(az.is_configured())
        with self.assertRaises(OllamaError) as ctx:
            az.analyze_tweet({"tweet_text": "x", "author_name": "N", "author_screen_name": "n"})
        self.assertTrue(ctx.exception.stop_cycle)
        self.assertEqual(FakeClient.posts, [])

    def test_empty_model_is_not_configured(self):
        az = analyzer(model="")
        self.assertFalse(az.is_configured())

    def test_tags_check_does_not_generate(self):
        ok, info = analyzer().test_connection()
        self.assertTrue(ok)
        self.assertIn(VERIFIED_MODEL_TAG, info)
        self.assertEqual(FakeClient.posts, [])
        self.assertTrue(FakeClient.gets[0].endswith("/api/tags"))
        FakeClient.mode = "missing"
        ok, info = analyzer().test_connection()
        self.assertFalse(ok)
        self.assertIn("assente", info)

    def test_busy_semaphore_does_not_call_the_api(self):
        self.assertTrue(_PROCESS_FLIGHT.acquire(timeout=0))
        try:
            with self.assertRaises(OllamaError) as ctx:
                analyzer(timeout=0).analyze_tweet({"tweet_text": "x", "author_name": "N", "author_screen_name": "n"})
            self.assertTrue(ctx.exception.stop_cycle)
            self.assertEqual(FakeClient.posts, [])
        finally:
            _PROCESS_FLIGHT.release()

    def test_think_flag_is_configurable(self):
        analyzer(think=True).analyze_tweet({"tweet_text": "x", "author_name": "N", "author_screen_name": "n"})
        self.assertIs(FakeClient.posts[-1]["json"]["think"], True)


class OllamaServiceTests(unittest.TestCase):
    def setUp(self):
        FakeClient.posts = []
        FakeClient.gets = []
        FakeClient.mode = "ok"
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [
            patch("src.analyzer.ollama.httpx.Client", FakeClient),
            patch("src.analyzer.ollama.time.sleep"),
            patch("src.storage.site_generator.export_site_data", lambda db: None),
            patch("src.storage.site_generator.generate_index_html", lambda: None),
            patch("src.service.sync_service.sync_site_to_git", return_value=True),
            patch("src.service.sync_service.asyncio.sleep", new=AsyncMock()),
            patch.object(AppConfig, "export_markdown", new_callable=PropertyMock),
            patch.object(AppConfig, "download_media", new_callable=PropertyMock),
            patch.object(AppConfig, "telegram_notify_on_new", new_callable=PropertyMock),
        ]
        started = [item.start() for item in self.patches]
        started[-3].return_value = False
        started[-2].return_value = False
        started[-1].return_value = True

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def _run(self, tweets, mode="ok"):
        FakeClient.mode = mode
        notes = []

        class Note:
            def is_configured(self):
                return True

            def notify_bookmark(self, item):
                notes.append(item["tweet_id"])
                return True

        class X:
            async def fetch_incremental(self, pred):
                return tweets

            async def fetch_all(self, progress_callback=None):
                return tweets, True

        db = Database(Path(self.tmp.name) / "t.db")
        svc = SyncService(
            db=db,
            x_client=X(),
            analyzer=analyzer(max_per_cycle=5, max_attempts=2),
            notifier=Note(),
            media_downloader=object(),
            markdown_exporter=object(),
        )
        stats = asyncio.run(svc.run_sync())
        return stats, notes, db

    def test_success_notifies_once_and_not_on_the_next_cycle(self):
        tweets = [{
            "tweet_id": "42",
            "author_name": "N",
            "author_screen_name": "n",
            "tweet_text": "post",
            "tweet_url": "https://x.com/n/status/42",
            "media_urls": [],
        }]
        stats, notes, db = self._run(tweets)
        self.assertEqual(stats["analyzed_by_gemini"], 1)
        self.assertEqual(notes, ["42"])
        self.assertEqual(db.get_bookmark("42")["status"], "analyzed")
        stats2, notes2, _db2 = self._run(tweets)
        self.assertEqual(notes2, [])
        self.assertEqual(stats2["analyzed_by_gemini"], 0)
        self.assertEqual(FakeClient.posts[0]["json"]["options"]["num_ctx"], 4096)

    def test_timeout_does_not_mark_success_or_notify_the_rest(self):
        tweets = []
        for tweet_id in ("1", "2"):
            tweets.append({
                "tweet_id": tweet_id,
                "author_name": "N",
                "author_screen_name": "n",
                "tweet_text": "post",
                "tweet_url": f"https://x.com/n/status/{tweet_id}",
                "media_urls": [],
            })
        stats, notes, db = self._run(tweets, mode="timeout")
        self.assertEqual(notes, [])
        self.assertEqual(stats["analyzed_by_gemini"], 0)
        self.assertEqual(len(FakeClient.posts), 1)
        statuses = {row["tweet_id"]: row["status"] for row in (db.get_bookmark("1"), db.get_bookmark("2"))}
        self.assertIn("failed_analysis", statuses.values())
        self.assertIn("pending_analysis", statuses.values())


if __name__ == "__main__":
    unittest.main()
