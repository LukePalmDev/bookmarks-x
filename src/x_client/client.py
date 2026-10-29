"""Client asincrono per l'estrazione dei Segnalibri (Bookmarks) da X / Twitter."""

import json
from typing import Any, Callable, Dict, List, Optional, Tuple
import httpx

from src.auth.x_auth import TWITTER_BEARER, XAuthManager
from src.config import config


class XBookmarksClient:
    """Client per interagire con l'API GraphQL di X ed estrarre i segnalibri salvati."""

    GRAPHQL_BOOKMARKS_URL = "https://x.com/i/api/graphql/qToeLeMs43Q8cr7tRYXmaQ/Bookmarks"

    def __init__(self, cookies: Optional[Dict[str, str]] = None):
        self.cookies = cookies or XAuthManager.get_credentials()

    def _get_headers(self) -> Dict[str, str]:
        return {
            "authorization": f"Bearer {TWITTER_BEARER}",
            "content-type": "application/json",
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-active-user": "yes",
            "x-csrf-token": self.cookies.get("ct0", ""),
            "referer": "https://x.com/i/bookmarks",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
        }

    def _get_params(self, count: int = 20, cursor: Optional[str] = None) -> Dict[str, str]:
        variables = {
            "count": count,
            "includePromotedContent": False,
        }
        if cursor:
            variables["cursor"] = cursor

        features = {
            "graphql_timeline_v2_bookmark_timeline": True,
            "responsive_web_graphql_exclude_directive_enabled": True,
            "verified_phone_label_enabled": False,
            "responsive_web_graphql_timeline_navigation_enabled": True,
            "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
            "tweetypie_unmention_optimization_enabled": True,
            "responsive_web_edit_tweet_api_enabled": True,
            "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
            "view_counts_everywhere_api_enabled": True,
            "longform_notetweets_consumption_enabled": True,
            "responsive_web_twitter_article_tweet_consumption_enabled": True,
            "tweet_awards_web_tipping_enabled": False,
            "freedom_of_speech_not_reach_fetch_enabled": True,
            "standardized_nudges_misinfo": True,
            "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
            "rweb_video_timestamps_enabled": True,
            "longform_notetweets_rich_text_read_enabled": True,
            "longform_notetweets_inline_media_enabled": True,
            "responsive_web_enhance_cards_enabled": False,
        }

        return {
            "variables": json.dumps(variables),
            "features": json.dumps(features),
        }

    @staticmethod
    def _parse_tweet_result(tweet_res: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not tweet_res:
            return None

        # Gestione wrapper TweetWithVisibilityResults
        if tweet_res.get("__typename") == "TweetWithVisibilityResults":
            tweet_res = tweet_res.get("tweet", {})

        tweet_id = tweet_res.get("rest_id")
        if not tweet_id:
            return None

        # Autore
        core = tweet_res.get("core", {})
        user_res = core.get("user_results", {}).get("result", {})
        user_legacy = user_res.get("legacy", {})
        author_name = user_legacy.get("name", "Unknown")
        author_screen_name = user_legacy.get("screen_name", "unknown")
        author_id = user_res.get("rest_id", "")

        # Testo (priorità a note_tweet per articoli lunghi >280 caratteri)
        legacy = tweet_res.get("legacy", {})
        note_text = (
            tweet_res.get("note_tweet", {})
            .get("note_tweet_results", {})
            .get("result", {})
            .get("text")
        )
        full_text = note_text or legacy.get("full_text", "")

        # Data di pubblicazione
        created_at = legacy.get("created_at", "")

        # Media allegati (foto / miniature video)
        media_urls: List[str] = []
        extended_entities = legacy.get("extended_entities", {})
        for m in extended_entities.get("media", []):
            url = m.get("media_url_https")
            if url:
                media_urls.append(url)

        # Quoted tweet se presente
        quoted_data = None
        quoted_result = tweet_res.get("quoted_status_result", {}).get("result", {})
        if quoted_result:
            if quoted_result.get("__typename") == "TweetWithVisibilityResults":
                quoted_result = quoted_result.get("tweet", {})
            q_user = quoted_result.get("core", {}).get("user_results", {}).get("result", {}).get("legacy", {})
            q_legacy = quoted_result.get("legacy", {})
            quoted_data = {
                "id": quoted_result.get("rest_id"),
                "author": q_user.get("screen_name"),
                "author_name": q_user.get("name"),
                "text": q_legacy.get("full_text"),
            }

        return {
            "tweet_id": str(tweet_id),
            "author_name": author_name,
            "author_screen_name": author_screen_name,
            "author_id": str(author_id),
            "tweet_text": full_text,
            "tweet_url": f"https://x.com/{author_screen_name}/status/{tweet_id}",
            "created_at": created_at,
            "media_urls": media_urls,
            "quoted_status": quoted_data,
            "raw_json": tweet_res,
        }

    async def fetch_page(
        self, cursor: Optional[str] = None, count: int = 20
    ) -> Tuple[List[Dict[str, Any]], Optional[str], Optional[str]]:
        """Scarica una singola pagina di segnalibri. Ritorna (tweets, next_cursor, top_cursor)."""
        headers = self._get_headers()
        params = self._get_params(count=count, cursor=cursor)

        async with httpx.AsyncClient(timeout=25.0) as client:
            res = await client.get(
                self.GRAPHQL_BOOKMARKS_URL,
                headers=headers,
                cookies=self.cookies,
                params=params,
            )

            if res.status_code == 429:
                raise RuntimeError("Rate limit raggiunto su X (HTTP 429). Attendi qualche minuto.")
            if res.status_code != 200:
                raise RuntimeError(f"Errore chiamata X GraphQL: HTTP {res.status_code} - {res.text[:200]}")

            data = res.json()

        timeline = (
            data.get("data", {})
            .get("bookmark_timeline_v2", {})
            .get("timeline", {})
            .get("instructions", [])
        )

        tweets: List[Dict[str, Any]] = []
        next_bottom_cursor: Optional[str] = None
        next_top_cursor: Optional[str] = None

        for inst in timeline:
            if inst.get("type") == "TimelineAddEntries":
                for entry in inst.get("entries", []):
                    entry_content = entry.get("content", {})
                    entry_type = entry_content.get("entryType")

                    if entry_type == "TimelineTimelineItem":
                        item_content = entry_content.get("itemContent", {})
                        tweet_res = item_content.get("tweet_results", {}).get("result", {})
                        parsed = self._parse_tweet_result(tweet_res)
                        if parsed:
                            tweets.append(parsed)

                    elif entry_type == "TimelineTimelineCursor":
                        cursor_type = entry_content.get("cursorType")
                        cursor_val = entry_content.get("value")
                        if cursor_type == "Bottom":
                            next_bottom_cursor = cursor_val
                        elif cursor_type == "Top":
                            next_top_cursor = cursor_val

        return tweets, next_bottom_cursor, next_top_cursor

    async def fetch_incremental(
        self,
        is_already_saved_fn: Callable[[str], bool],
        max_pages: int = 20,
    ) -> List[Dict[str, Any]]:
        """Scarica i nuovi segnalibri fino al primo già incontrato nel database.
        Ideale per la sincronizzazione periodica e futura."""
        new_tweets: List[Dict[str, Any]] = []
        cursor: Optional[str] = None

        for page in range(max_pages):
            tweets, bottom_cursor, _ = await self.fetch_page(cursor=cursor, count=config.x_batch_size)
            if not tweets:
                break

            hit_saved = False
            for t in tweets:
                t_id = t["tweet_id"]
                if is_already_saved_fn(t_id):
                    hit_saved = True
                    break
                new_tweets.append(t)

            if hit_saved or not bottom_cursor or bottom_cursor == cursor:
                break

            cursor = bottom_cursor

        return new_tweets

    async def fetch_all(
        self,
        progress_callback: Optional[Callable[[int], None]] = None,
        max_total: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Scarica l'intero storico dei segnalibri salvati effettuando paginazione completa."""
        all_tweets: List[Dict[str, Any]] = []
        seen_ids = set()
        cursor: Optional[str] = None

        while True:
            tweets, bottom_cursor, _ = await self.fetch_page(cursor=cursor, count=config.x_batch_size)
            if not tweets:
                break

            new_in_page = 0
            for t in tweets:
                t_id = t["tweet_id"]
                if t_id not in seen_ids:
                    seen_ids.add(t_id)
                    all_tweets.append(t)
                    new_in_page += 1

            if progress_callback:
                progress_callback(len(all_tweets))

            if max_total and len(all_tweets) >= max_total:
                all_tweets = all_tweets[:max_total]
                break

            # Se non ci sono nuovi tweet o il cursore non avanza, siamo alla fine
            if new_in_page == 0 or not bottom_cursor or bottom_cursor == cursor:
                break

            cursor = bottom_cursor

        return all_tweets
