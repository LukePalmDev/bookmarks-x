"""Regressioni dei tre guasti osservati sul servizio watch.

1. Telegram: sendPhoto con didascalia lunga inviava anche il testo intero.
2. Watch: il mirror non aveva ``analyzed_by_gemini`` e il callback faceva KeyError.
3. Claude CLI: exit 1 con stderr vuoto nascondeva lo stdout.
"""

import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, PropertyMock, patch

import httpx

from src.analyzer.claude import ClaudeAnalyzer, build_claude_env, cli_failure_detail
from src.cli.app import describe_watch_cycle
from src.config import AppConfig, config
from src.notifier.telegram import TelegramNotifier
from src.service.sync_service import SyncService, get_default_analyzer, watch_cycle_counts
from src.storage.db import Database


OK_ANALYSIS = {
    "autore": "N (@n)",
    "titolo": "Titolo",
    "categoria": "📰 News & Trend",
    "il_succo": "Sintesi.",
    "punti_chiave": ["a"],
    "caso_d_uso": "Uso.",
    "tag": ["#t"],
}


def tweet(tweet_id, text="post"):
    return {
        "tweet_id": tweet_id,
        "author_name": "N",
        "author_screen_name": "n",
        "tweet_text": text,
        "tweet_url": f"https://x.com/n/status/{tweet_id}",
        "media_urls": [],
    }


class XStub:
    def __init__(self, tweets):
        self.tweets = tweets

    async def fetch_incremental(self, pred):
        return list(self.tweets)

    async def fetch_all(self, progress_callback=None):
        return list(self.tweets), True


class AnalyzerStub:
    def __init__(self, fail_ids=None, stop_on=None, max_per_cycle=None):
        self.calls = []
        self.fail_ids = set(fail_ids or [])
        self.stop_on = stop_on
        if max_per_cycle is not None:
            self.max_per_cycle = max_per_cycle

    def is_configured(self):
        return True

    def analyze_tweet(self, item):
        self.calls.append(item["tweet_id"])
        if item["tweet_id"] == self.stop_on:
            err = RuntimeError("Ollama timeout (120s)")
            err.stop_cycle = True
            raise err
        if item["tweet_id"] in self.fail_ids:
            raise RuntimeError("Claude CLI exit 1: dettaglio visibile")
        return dict(OK_ANALYSIS)


class NotifierStub:
    def __init__(self, raise_on_send=False):
        self.ids = []
        self.raise_on_send = raise_on_send

    def is_configured(self):
        return True

    def notify_bookmark(self, item):
        self.ids.append(item["tweet_id"])
        if self.raise_on_send:
            raise RuntimeError("dopo l'invio")
        return True


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [
            patch("src.storage.site_generator.export_site_data", lambda db: None),
            patch("src.storage.site_generator.generate_index_html", lambda: None),
            patch("src.service.sync_service.sync_site_to_git", return_value=True),
            patch("src.service.sync_service.asyncio.sleep", new=AsyncMock()),
            patch.object(AppConfig, "export_markdown", new_callable=PropertyMock),
            patch.object(AppConfig, "download_media", new_callable=PropertyMock),
            patch.object(AppConfig, "telegram_notify_on_new", new_callable=PropertyMock),
        ]
        started = [p.start() for p in self.patches]
        started[-3].return_value = False
        started[-2].return_value = False
        started[-1].return_value = True

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def service(self, tweets, analyzer, notifier):
        db = Database(Path(self.tmp.name) / "t.db")
        return SyncService(
            db=db,
            x_client=XStub(tweets),
            analyzer=analyzer,
            notifier=notifier,
            media_downloader=object(),
            markdown_exporter=object(),
        ), db

    def test_success_is_not_notified_again(self):
        analyzer = AnalyzerStub()
        notifier = NotifierStub()
        svc, db = self.service([tweet("42")], analyzer, notifier)
        asyncio.run(svc.run_sync())
        asyncio.run(svc.run_sync())
        self.assertEqual(notifier.ids, ["42"])
        self.assertEqual(db.get_bookmark("42")["status"], "analyzed")
        self.assertEqual(analyzer.calls, ["42"])

    def test_notify_exception_does_not_requeue(self):
        notifier = NotifierStub(raise_on_send=True)
        svc, db = self.service([tweet("42")], AnalyzerStub(), notifier)
        asyncio.run(svc.run_sync())
        asyncio.run(svc.run_sync())
        self.assertEqual(notifier.ids, ["42"])
        self.assertEqual(db.get_bookmark("42")["status"], "analyzed")

    def test_ordinary_failure_continues_and_does_not_notify(self):
        analyzer = AnalyzerStub(fail_ids={"bad"})
        notifier = NotifierStub()
        svc, db = self.service([tweet("bad"), tweet("good")], analyzer, notifier)
        stats = asyncio.run(svc.run_sync())
        self.assertEqual(set(analyzer.calls), {"bad", "good"})
        self.assertEqual(notifier.ids, ["good"])
        self.assertEqual(db.get_bookmark("bad")["status"], "failed_analysis")
        self.assertEqual(db.get_bookmark("good")["status"], "analyzed")
        self.assertEqual(stats["analysis_errors"], 1)
        self.assertEqual(stats["analyzed_by_gemini"], 1)

    def test_overload_stops_batch_without_success_or_notify(self):
        class StopFirst(AnalyzerStub):
            def analyze_tweet(self, item):
                self.calls.append(item["tweet_id"])
                err = RuntimeError("Ollama timeout (120s)")
                err.stop_cycle = True
                raise err

        analyzer = StopFirst(max_per_cycle=5)
        notifier = NotifierStub()
        svc, db = self.service([tweet("early"), tweet("later")], analyzer, notifier)
        asyncio.run(svc.run_sync())
        self.assertEqual(len(analyzer.calls), 1)
        self.assertEqual(notifier.ids, [])
        self.assertEqual(db.get_bookmark(analyzer.calls[0])["status"], "failed_analysis")
        other = "later" if analyzer.calls[0] == "early" else "early"
        self.assertEqual(db.get_bookmark(other)["status"], "pending_analysis")

    def test_per_cycle_limit_leaves_the_rest_pending(self):
        analyzer = AnalyzerStub(max_per_cycle=1)
        notifier = NotifierStub()
        svc, db = self.service([tweet("a"), tweet("b")], analyzer, notifier)
        asyncio.run(svc.run_sync())
        self.assertEqual(len(analyzer.calls), 1)
        self.assertEqual(len(notifier.ids), 1)
        pending = {row["tweet_id"] for row in db.get_pending_analysis()}
        self.assertEqual(len(pending), 1)
        self.assertNotIn(notifier.ids[0], pending)

    def test_mirror_stats_include_watch_key(self):
        notifier = NotifierStub()
        svc, _db = self.service([tweet("42")], AnalyzerStub(), notifier)
        stats = asyncio.run(svc.run_mirror())
        self.assertIn("analyzed_by_gemini", stats)
        self.assertEqual(stats["analyzed_by_gemini"], 1)
        self.assertEqual(stats["analyzed"], 1)
        self.assertEqual(describe_watch_cycle(stats), "Nuovi: 1 | Analizzati: 1 | Totale DB: 1")
        historical = {"new_saved": 1, "analyzed": 2, "total_in_db": 9}
        self.assertEqual(watch_cycle_counts(historical), (1, 2))
        self.assertIn("Analizzati: 2", describe_watch_cycle(historical))
        self.assertIn("Nessun nuovo", describe_watch_cycle({"new_saved": 0, "analyzed": 0, "total_in_db": 3}))

    def test_quota_pause_does_not_touch_the_other_rows(self):
        from datetime import datetime, timedelta, timezone
        from src.analyzer.claude import ClaudeQuotaPause

        class QuotaAz(AnalyzerStub):
            def analyze_tweet(self, item):
                self.calls.append(item["tweet_id"])
                raise ClaudeQuotaPause(
                    "You've reached your weekly limit",
                    resume_at=datetime.now(timezone.utc) + timedelta(hours=2),
                    reason="weekly",
                )

        pause_path = Path(self.tmp.name) / "pause.json"
        analyzer = QuotaAz()
        notifier = NotifierStub()
        svc, db = self.service([tweet("keep"), tweet("other")], analyzer, notifier)
        with patch("src.analyzer.claude.quota_pause_file", lambda: pause_path):
            asyncio.run(svc.run_sync())
            first_calls = list(analyzer.calls)
            asyncio.run(svc.run_sync())
        self.assertEqual(first_calls, analyzer.calls)
        self.assertEqual(len(first_calls), 1)
        self.assertEqual(notifier.ids, [])
        self.assertEqual(db.get_bookmark(first_calls[0])["status"], "failed_analysis")
        other = "other" if first_calls[0] == "keep" else "keep"
        self.assertEqual(db.get_bookmark(other)["status"], "pending_analysis")
        self.assertTrue(pause_path.is_file())

    def test_default_engine_stays_claude(self):
        text = Path("config/config.yaml").read_text(encoding="utf-8")
        self.assertIn('engine: "claude"', text)
        self.assertIn("gemma4:12b-it-q4_K_M", text)
        self.assertEqual(config.analyzer_engine, "claude")
        self.assertEqual(config.claude_model, "claude-sonnet-5-5")
        self.assertEqual(config.claude_effort, "high")
        self.assertEqual(get_default_analyzer().__class__.__name__, "ClaudeAnalyzer")
        with patch.object(AppConfig, "analyzer_engine", new_callable=PropertyMock) as engine:
            engine.return_value = "ollama"
            ollama = get_default_analyzer()
        self.assertEqual(ollama.__class__.__name__, "OllamaAnalyzer")
        self.assertEqual(ollama.model, "gemma4:12b-it-q4_K_M")


class TelegramDedupTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.behavior = "ok"

        outer = self

        class Client:
            def __init__(self, timeout=None):
                self.timeout = timeout

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, json=None, data=None, files=None):
                kind = "photo" if files is not None else "message"
                text = (data or {}).get("caption") if files is not None else (json or {}).get("text")
                outer.calls.append(kind if kind == "photo" else text)
                if kind == "photo" and outer.behavior == "timeout":
                    raise httpx.ReadTimeout("read timed out")
                code = 500 if kind == "photo" and outer.behavior == "http500" else 200

                class Resp:
                    status_code = code
                    text = "nope"

                return Resp()

        self.client = Client
        self.patch = patch("src.notifier.telegram.httpx.Client", Client)
        self.patch.start()
        self.notifier = TelegramNotifier(bot_token="t", chat_id="1", enabled=True)
        self.image = Path(tempfile.mkdtemp()) / "a.jpg"
        self.image.write_bytes(b"\xff\xd8\xff" + b"x" * 32)

    def tearDown(self):
        self.patch.stop()

    def bookmark(self, succo, with_photo=True):
        item = {
            "tweet_url": "https://x.com/a/status/1",
            "author_screen_name": "a",
            "tweet_text": "t",
            "gemini_analysis_json": {
                "autore": "A (@a)",
                "titolo": "Titolo",
                "categoria": "📰 News & Trend",
                "il_succo": succo,
                "punti_chiave": ["p1", "p2", "p3"],
                "caso_d_uso": "uso",
                "tag": ["#a"],
            },
        }
        if with_photo:
            item["media_local_paths"] = json.dumps([str(self.image)])
        return item

    def test_long_photo_caption_is_not_sent_twice(self):
        ok = self.notifier.notify_bookmark(self.bookmark("X" * 1500))
        self.assertTrue(ok)
        self.assertEqual(self.calls, ["photo"])

    def test_text_only_is_a_single_message(self):
        ok = self.notifier.notify_bookmark(self.bookmark("X" * 1500, with_photo=False))
        self.assertTrue(ok)
        self.assertEqual(len(self.calls), 1)
        self.assertNotEqual(self.calls[0], "photo")

    def test_photo_timeout_does_not_fall_back_to_text(self):
        self.behavior = "timeout"
        ok = self.notifier.notify_bookmark(self.bookmark("breve"))
        self.assertFalse(ok)
        self.assertEqual(self.calls, ["photo"])

    def test_rejected_photo_falls_back_once(self):
        self.behavior = "http500"
        ok = self.notifier.notify_bookmark(self.bookmark("breve"))
        self.assertTrue(ok)
        self.assertEqual(self.calls[0], "photo")
        self.assertEqual(len(self.calls), 2)


class ClaudeErrorCaptureTests(unittest.TestCase):
    def test_env_adds_local_bin_and_filters_claude_code(self):
        env = build_claude_env({
            "PATH": "/opt/homebrew/bin:/usr/bin",
            "HOME": "/Users/luke",
            "CLAUDECODE": "1",
            "CLAUDE_CODE_SHOULD_DROP": "no",
            "CLAUDE_CODE_OAUTH_TOKEN": "keep",
        })
        self.assertNotIn("CLAUDECODE", env)
        self.assertNotIn("CLAUDE_CODE_SHOULD_DROP", env)
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "keep")
        self.assertIn(str(Path.home() / ".local" / "bin"), env["PATH"])
        self.assertTrue(env["PATH"].startswith(str(Path(__file__).resolve().parents[1] / ".venv" / "bin")) or ".venv/bin" in env["PATH"])

    def test_empty_stderr_keeps_stdout(self):
        detail = cli_failure_detail("  \n", '{"is_error":true,"result":"schema exploded"}')
        self.assertIn("schema exploded", detail)

    def test_exit_uses_stdout_and_does_not_inherit_stdin(self):
        captured = []

        class Proc:
            returncode = 1
            stderr = " \n"
            stdout = '{"is_error":true,"result":"schema exploded"}'

        def fake_run(command, **kwargs):
            captured.append((list(command), kwargs))
            return Proc()

        with patch("src.analyzer.claude.subprocess.run", fake_run), patch("src.analyzer.claude.time.sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                ClaudeAnalyzer(model="claude-sonnet-5-5", effort="high").analyze_tweet(
                    {"author_name": "N", "author_screen_name": "n", "tweet_text": "ciao"}
                )
        message = str(ctx.exception)
        self.assertIn("schema exploded", message)
        self.assertIn("Claude CLI exit 1:", message)
        self.assertEqual(len(captured), 2)
        self.assertIs(captured[0][1]["stdin"], subprocess.DEVNULL)
        for command, kwargs in captured:
            self.assertNotIn("--fallback-model", command)
            self.assertEqual(command[command.index("--model") + 1], "claude-sonnet-5-5")
            self.assertEqual(command[command.index("--effort") + 1], "high")
            self.assertEqual(kwargs["env"]["CLAUDE_CODE_EFFORT_LEVEL"], "high")
        self.assertNotEqual(os.environ.get("CLAUDE_CODE_EFFORT_LEVEL"), "high")

    def test_missing_binary_is_not_reported_as_exit_1(self):
        def fake_run(command, **kwargs):
            raise FileNotFoundError(2, "No such file")

        with patch("src.analyzer.claude.subprocess.run", fake_run), patch("src.analyzer.claude.time.sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                ClaudeAnalyzer(model="claude-sonnet-5-5", effort="high").analyze_tweet({"tweet_text": "z", "author_name": "", "author_screen_name": ""})
        self.assertNotIn("Claude CLI exit 1", str(ctx.exception))

    def test_parent_effort_env_does_not_override_the_child(self):
        captured = []

        class Proc:
            returncode = 0
            stderr = ""
            stdout = json.dumps({
                "structured_output": {"autore": "N (@n)", "titolo": "T", "categoria": "📰 News & Trend", "il_succo": "s", "punti_chiave": ["a"], "caso_d_uso": "c", "tag": ["#t"]},
                "usage": {"input_tokens": 10, "output_tokens": 4, "cache_read_input_tokens": 2, "cache_creation_input_tokens": 1},
                "total_cost_usd": 0.01,
                "modelUsage": {"claude-sonnet-5-5": {}},
                "duration_ms": 800,
            })

        def fake_run(command, **kwargs):
            captured.append(kwargs["env"])
            return Proc()

        os.environ["CLAUDE_CODE_EFFORT_LEVEL"] = "low"
        try:
            with patch("src.analyzer.claude.subprocess.run", fake_run):
                ClaudeAnalyzer(model="claude-sonnet-5-5", effort="high").analyze_tweet(
                    {"author_name": "N", "author_screen_name": "n", "tweet_text": "ciao"}
                )
        finally:
            os.environ.pop("CLAUDE_CODE_EFFORT_LEVEL", None)
        self.assertEqual(captured[0]["CLAUDE_CODE_EFFORT_LEVEL"], "high")
        self.assertNotIn("CLAUDE_CODE_EFFORT_LEVEL", os.environ)

    def test_weekly_quota_is_not_retried(self):
        calls = []

        class Proc:
            returncode = 1
            stderr = ""
            stdout = "You've reached your weekly limit. It resets Monday at 06:00."

        def fake_run(command, **kwargs):
            calls.append(command)
            return Proc()

        with patch("src.analyzer.claude.subprocess.run", fake_run), patch("src.analyzer.claude.time.sleep") as sleep:
            with self.assertRaises(Exception) as ctx:
                ClaudeAnalyzer(model="claude-sonnet-5-5", effort="high").analyze_tweet(
                    {"author_name": "N", "author_screen_name": "n", "tweet_text": "ciao"}
                )
        self.assertEqual(len(calls), 1)
        self.assertEqual(sleep.call_count, 0)
        self.assertTrue(ctx.exception.stop_cycle)
        self.assertEqual(ctx.exception.reason, "weekly")

    def test_usage_percent_is_not_invented(self):
        from src.analyzer.claude import format_usage_footer, usage_from_cli_json
        usage = usage_from_cli_json({
            "usage": {"input_tokens": 12, "output_tokens": 5, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 0},
            "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 12}},
            "total_cost_usd": 0.02,
        })
        self.assertEqual(usage["input_tokens"], 12)
        self.assertEqual(usage["output_tokens"], 5)
        self.assertIsNone(usage["pro_5h_percent"])
        self.assertIsNone(usage["weekly_percent"])
        self.assertEqual(usage["percent_note"], "non disponibile")
        footer = format_usage_footer(usage)
        self.assertIn("non disponibile", footer)
        self.assertNotIn("%", footer)
        self.assertNotIn("0.02", footer)


if __name__ == "__main__":
    unittest.main()
