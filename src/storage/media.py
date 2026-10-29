"""Download e archiviazione locale dei file multimediali dei post di X."""

import asyncio
from pathlib import Path
from typing import List
import httpx

from src.config import config


class MediaDownloader:
    """Scarica e memorizza localmente i file multimediali associati ai post salvati."""

    def __init__(self, media_dir: Path | None = None):
        self.media_dir = media_dir or config.media_dir
        self.media_dir.mkdir(parents=True, exist_ok=True)

    async def download_file(self, client: httpx.AsyncClient, url: str, target_path: Path) -> bool:
        if target_path.exists() and target_path.stat().st_size > 0:
            return True

        try:
            # Scarica in formato ad alta risoluzione per le immagini X
            download_url = url
            if "twimg.com" in url and "format=" not in url and "?" not in url:
                download_url = f"{url}?format=jpg&name=orig"

            res = await client.get(download_url, timeout=30.0)
            if res.status_code == 200:
                with open(target_path, "wb") as f:
                    f.write(res.content)
                return True
        except Exception:
            # Fallback con URL originale non modificato
            try:
                res = await client.get(url, timeout=30.0)
                if res.status_code == 200:
                    with open(target_path, "wb") as f:
                        f.write(res.content)
                    return True
            except Exception:
                pass
        return False

    async def download_tweet_media(self, tweet_id: str, media_urls: List[str]) -> List[str]:
        """Scarica tutti i file multimediali di un tweet e ritorna i percorsi relativi salvati."""
        if not media_urls or not config.download_media:
            return []

        saved_paths: List[str] = []
        async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}) as client:
            tasks = []
            for idx, url in enumerate(media_urls):
                ext = ".jpg"
                if ".png" in url:
                    ext = ".png"
                elif ".webp" in url:
                    ext = ".webp"
                elif ".mp4" in url:
                    ext = ".mp4"

                filename = f"{tweet_id}_{idx}{ext}"
                target_path = self.media_dir / filename
                tasks.append((url, target_path, filename))

            for url, path, filename in tasks:
                ok = await self.download_file(client, url, path)
                if ok:
                    saved_paths.append(str(path))

        return saved_paths
