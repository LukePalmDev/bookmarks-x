"""Esportazione dei post analizzati in file Markdown formattati per Obsidian e Notion."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from src.config import config


def sanitize_filename(name: str) -> str:
    """Rimuove caratteri non validi per i file macOS."""
    clean = re.sub(r'[\\/*?:"<>|]', "", name)
    clean = clean.replace(" ", "-").lower()
    return clean[:50]


def bookmark_iso_date(created_at: str, tweet_id: str) -> str:
    """Data ISO (YYYY-MM-DD) da created_at di X, da una data già ISO, o dallo snowflake."""
    raw = created_at or ""
    if raw:
        try:
            return datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y").date().isoformat()
        except Exception:
            pass
        if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
            return raw[:10]
    try:
        snowflake_ms = (int(tweet_id) >> 22) + 1288834974657
        return datetime.fromtimestamp(snowflake_ms / 1000, tz=timezone.utc).date().isoformat()
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


class MarkdownExporter:
    """Esporta un bookmark analizzato in un file Markdown ben strutturato con frontmatter YAML."""

    def __init__(self, archive_dir: Path | None = None):
        self.archive_dir = archive_dir or config.archive_dir
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def export_bookmark(self, bookmark: Dict[str, Any]) -> Path:
        tweet_id = bookmark["tweet_id"]

        # Recupera dati di analisi
        analysis = {}
        raw_analysis = bookmark.get("gemini_analysis_json")
        if raw_analysis:
            try:
                analysis = json.loads(raw_analysis) if isinstance(raw_analysis, str) else raw_analysis
            except Exception:
                analysis = {}

        author = analysis.get("autore") or bookmark.get("gemini_author") or bookmark.get("author_screen_name") or "unknown"
        title = analysis.get("titolo") or bookmark.get("gemini_title") or "Post senza titolo"
        category = analysis.get("categoria", "📌 Risorsa X")
        il_succo = analysis.get("il_succo", "")
        punti = analysis.get("punti_chiave", [])
        caso_d_uso = analysis.get("caso_d_uso", "")
        tags = [t.lstrip("#") for t in analysis.get("tag", [])]
        created_at = bookmark.get("created_at") or datetime.now().strftime("%Y-%m-%d")
        date_prefix = bookmark_iso_date(created_at, str(tweet_id))
        safe_title = sanitize_filename(title)
        filename = f"{date_prefix}_{sanitize_filename(author)}_{safe_title}_{tweet_id}.md"
        file_path = self.archive_dir / filename

        # Rimuove eventuale versione precedente per evitare duplicati con titoli diversi
        self.delete_bookmark_file(tweet_id)

        # Formatta i media
        media_paths = bookmark.get("media_local_paths") or []
        if isinstance(media_paths, str):
            try:
                media_paths = json.loads(media_paths)
            except Exception:
                media_paths = []

        media_section = ""
        if media_paths:
            media_section = "\n## 🖼️ Media Allegati\n\n"
            for p in media_paths:
                media_path = Path(p)
                if media_path.exists():
                    media_section += f"![Media]({media_path.resolve()})\n\n"

        # Tweet citato se presente
        quoted_section = ""
        quoted_data = bookmark.get("quoted_status")
        if quoted_data:
            try:
                q = json.loads(quoted_data) if isinstance(quoted_data, str) else quoted_data
                if q:
                    q_author = q.get("author", "Autore sconosciuto")
                    q_text = q.get("text", "")
                    quoted_section = f"\n> **Citando @{q_author}:**\n> {q_text}\n"
            except Exception:
                pass

        # Punti chiave in markdown
        punti_md = "\n".join([f"- {p}" for p in punti]) if punti else ""

        # Frontmatter tags
        tags_yaml = "\n".join([f"  - {t}" for t in tags])

        content = f"""---
id: "{tweet_id}"
titolo: "{title.replace('"', "'")}"
autore: "{author.replace('"', "'")}"
categoria: "{category}"
tags:
{tags_yaml}
url: "{bookmark.get('tweet_url', '')}"
data_creazione: "{created_at}"
---

# {title}

> **Autore:** [{author}]({bookmark.get('tweet_url', '')})  
> **Categoria:** {category}  
> **Data:** {created_at}  

## ⚡ Il Succo
{il_succo}

## 💡 Punti Chiave
{punti_md}

## 🎯 Caso d'Uso
{caso_d_uso}

---

## 📝 Testo Originale del Post

{bookmark.get('tweet_text', '').strip()}

{quoted_section}
{media_section}
"""
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        return file_path

    def delete_bookmark_file(self, tweet_id: str) -> bool:
        """Elimina il file Markdown associato al bookmark dall'archivio locale."""
        deleted = False
        for fp in self.archive_dir.glob(f"*_{tweet_id}.md"):
            try:
                fp.unlink()
                deleted = True
            except Exception:
                pass
        return deleted
