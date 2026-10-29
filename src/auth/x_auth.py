"""Gestione autenticazione e recupero cookie per X (Twitter)."""

import json
from pathlib import Path
from typing import Dict, Optional
import httpx

from src.config import BASE_DIR, config

COOKIES_CACHE_FILE = BASE_DIR / "cookies.json"
TWITTER_BEARER = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"


class XAuthManager:
    """Gestisce il recupero e la validazione dei cookie di sessione di X."""

    @staticmethod
    def get_cookies_from_env() -> Optional[Dict[str, str]]:
        auth_token = config.x_auth_token
        ct0 = config.x_ct0
        if auth_token and ct0:
            return {"auth_token": auth_token, "ct0": ct0}
        return None

    @staticmethod
    def get_cookies_from_file() -> Optional[Dict[str, str]]:
        if COOKIES_CACHE_FILE.exists():
            try:
                with open(COOKIES_CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and "auth_token" in data and "ct0" in data:
                        return {"auth_token": data["auth_token"], "ct0": data["ct0"]}
                    # Supporto esportazione array estensione cookie
                    if isinstance(data, list):
                        cookie_dict = {item["name"]: item["value"] for item in data if "name" in item and "value" in item}
                        if "auth_token" in cookie_dict and "ct0" in cookie_dict:
                            return {"auth_token": cookie_dict["auth_token"], "ct0": cookie_dict["ct0"]}
            except Exception:
                pass
        return None

    @staticmethod
    def get_cookies_from_browser(browser_name: str = "safari") -> Optional[Dict[str, str]]:
        try:
            import browser_cookie3
        except ImportError:
            return None

        # Priorità predefinita o browser richiesto
        browsers_to_try = [browser_name.lower()] if browser_name.lower() != "auto" else ["safari", "chrome", "brave", "firefox", "edge"]

        for b in browsers_to_try:
            try:
                browser_fn = getattr(browser_cookie3, b, None)
                if not browser_fn:
                    continue
                # Estrai per dominio x.com o twitter.com
                jar = browser_fn(domain_name="x.com")
                cookies = {c.name: c.value for c in jar}
                if not ("auth_token" in cookies and "ct0" in cookies):
                    jar = browser_fn(domain_name="twitter.com")
                    cookies.update({c.name: c.value for c in jar})

                if "auth_token" in cookies and "ct0" in cookies:
                    return {
                        "auth_token": cookies["auth_token"],
                        "ct0": cookies["ct0"],
                    }
            except Exception:
                continue
        return None

    @classmethod
    def get_credentials(cls) -> Dict[str, str]:
        """Recupera le credenziali con strategia a cascata:
        1. Variabili d'ambiente (.env)
        2. File cookies.json
        3. Safari o browser configurato
        4. Fallback automatico sugli altri browser
        """
        # 1. Da env
        cookies = cls.get_cookies_from_env()
        if cookies:
            return cookies

        # 2. Da cache locale
        cookies = cls.get_cookies_from_file()
        if cookies:
            return cookies

        # 3. Dal browser configurato (default Safari)
        cookies = cls.get_cookies_from_browser(config.x_cookie_source)
        if cookies:
            cls.save_cookies_cache(cookies)
            return cookies

        # 4. Tentativo automatico di tutti i browser
        cookies = cls.get_cookies_from_browser("auto")
        if cookies:
            cls.save_cookies_cache(cookies)
            return cookies

        raise RuntimeError(
            "Impossibile trovare i cookie di autenticazione di X (auth_token e ct0).\n"
            "Soluzioni possibili:\n"
            "1. Assicurati di aver effettuato l'accesso a x.com su Safari.\n"
            "2. Oppure imposta X_AUTH_TOKEN e X_CT0 nel file .env.\n"
            "3. Oppure salva i cookie in 'cookies.json'."
        )

    @staticmethod
    def save_cookies_cache(cookies: Dict[str, str]) -> None:
        try:
            with open(COOKIES_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cookies, f, indent=2)
        except Exception:
            pass

    @classmethod
    def validate_session(cls, cookies: Dict[str, str]) -> tuple[bool, str]:
        """Verifica se i cookie sono validi effettuando una chiamata di test a X."""
        headers = {
            "authorization": f"Bearer {TWITTER_BEARER}",
            "x-csrf-token": cookies.get("ct0", ""),
            "x-twitter-auth-type": "OAuth2Session",
            "x-twitter-active-user": "yes",
            "referer": "https://x.com/i/bookmarks",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
        }
        url = "https://x.com/i/api/graphql/qToeLeMs43Q8cr7tRYXmaQ/Bookmarks"
        params = {
            "variables": json.dumps({"count": 1, "includePromotedContent": False}),
            "features": json.dumps({
                "graphql_timeline_v2_bookmark_timeline": True,
                "responsive_web_graphql_exclude_directive_enabled": True,
            }),
        }
        try:
            with httpx.Client(timeout=10.0) as client:
                res = client.get(url, headers=headers, cookies=cookies, params=params)
                if res.status_code == 200:
                    return True, "Sessione X valida e attiva!"
                return False, f"HTTP {res.status_code}: {res.text[:100]}"
        except Exception as e:
            return False, str(e)
