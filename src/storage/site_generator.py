"""Generatore del sito web statico ottimizzato per mobile (iPhone 13 mini) e GitHub Pages."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from src.config import BASE_DIR, config
from src.storage.db import Database

DOCS_DIR = BASE_DIR / "docs"
IT_MONTHS = ["Gen", "Feb", "Mar", "Apr", "Mag", "Giu", "Lug", "Ago", "Set", "Ott", "Nov", "Dic"]


def parse_tweet_date_and_ts(created_at_raw: str, tweet_id: str) -> Tuple[int, str]:
    """Calcola il timestamp Unix e formatta la data in italiano (es. '28 Ott 2026')."""
    # 1. Prova parsing stringa data X standard (es. 'Wed Oct 28 10:17:05 +0000 2026')
    if created_at_raw:
        try:
            dt = datetime.strptime(created_at_raw, "%a %b %d %H:%M:%S %z %Y")
            ts = int(dt.timestamp())
            date_str = f"{dt.day} {IT_MONTHS[dt.month - 1]} {dt.year}"
            return ts, date_str
        except Exception:
            pass

    # 2. Fallback Snowflake ID di Twitter (64-bit int dove i bit alti sono millisecondi da Twitter epoch)
    try:
        tid = int(tweet_id)
        snowflake_ms = (tid >> 22) + 1288834974657
        dt = datetime.fromtimestamp(snowflake_ms / 1000)
        ts = int(dt.timestamp())
        date_str = f"{dt.day} {IT_MONTHS[dt.month - 1]} {dt.year}"
        return ts, date_str
    except Exception:
        pass

    return 0, created_at_raw[:10] if created_at_raw else "Data sconosciuta"


def export_site_data(db: Database) -> Path:
    """Esporta tutti i bookmark analizzati in un file JSON per l'app web."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    bookmarks = db.get_recent_bookmarks(limit=1000)

    site_items = []
    for b in bookmarks:
        analysis = {}
        raw_analysis = b.get("gemini_analysis_json")
        if raw_analysis:
            try:
                analysis = json.loads(raw_analysis) if isinstance(raw_analysis, str) else raw_analysis
            except Exception:
                analysis = {}

        ts, date_str = parse_tweet_date_and_ts(b.get("created_at") or "", b["tweet_id"])

        site_items.append({
            "id": b["tweet_id"],
            "title": analysis.get("titolo") or b.get("gemini_title") or b.get("tweet_text", "")[:60],
            "author": analysis.get("autore") or f"{b.get('author_name', '')} (@{b.get('author_screen_name', '')})",
            "screen_name": b.get("author_screen_name", ""),
            "category": analysis.get("categoria", "📌 Risorsa X"),
            "summary": analysis.get("il_succo", b.get("tweet_text", "")[:180]),
            "points": analysis.get("punti_chiave", []),
            "use_case": analysis.get("caso_d_uso", ""),
            "tags": analysis.get("tag", []),
            "url": b.get("tweet_url", f"https://x.com/i/web/status/{b['tweet_id']}"),
            "date": date_str,
            "timestamp": ts,
            "text": b.get("tweet_text", ""),
            "status": b.get("status", "pending"),
        })

    # Ordina di default dal più recente al più vecchio (timestamp decrescente)
    site_items.sort(key=lambda x: x.get("timestamp", 0), reverse=True)

    json_path = DOCS_DIR / "bookmarks.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(site_items, f, ensure_ascii=False, indent=2)

    return json_path


def generate_index_html() -> Path:
    """Genera la web app mobile-first, ottimizzata al pixel per iPhone 13 mini (375pt)."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    html_content = """<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
  <title>Bookmarks-X</title>
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="apple-mobile-web-app-title" content="Bookmarks-X">
  <meta name="theme-color" content="#000000">
  <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🔖</text></svg>">
  <style>
    :root {
      --bg: #000000;
      --card-bg: #121214;
      --card-border: #222226;
      --text-main: #f5f5f7;
      --text-sub: #a1a1a6;
      --accent: #2997ff;
      --accent-glow: rgba(41, 151, 255, 0.15);
      --badge-bg: #1c1c1e;
      --safe-top: env(safe-area-inset-top, 20px);
      --safe-bottom: env(safe-area-inset-bottom, 20px);
    }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      -webkit-tap-highlight-color: transparent;
    }

    body {
      background-color: var(--bg);
      color: var(--text-main);
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", "Segoe UI", Roboto, sans-serif;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      overflow-x: hidden;
      padding-top: calc(var(--safe-top) + 65px);
      padding-bottom: calc(var(--safe-bottom) + 30px);
      letter-spacing: -0.015em;
    }

    /* Header Fisso per iPhone 13 mini */
    header {
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      z-index: 100;
      background: rgba(0, 0, 0, 0.85);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border-bottom: 0.5px solid rgba(255, 255, 255, 0.12);
      padding: var(--safe-top) 16px 10px 16px;
    }

    .header-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 8px;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 6px;
    }

    .brand-icon {
      font-size: 20px;
    }

    .brand-title {
      font-size: 19px;
      font-weight: 700;
      color: #ffffff;
      letter-spacing: -0.02em;
    }

    .header-actions {
      display: flex;
      align-items: center;
      gap: 6px;
    }

    .sort-btn {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-size: 11.5px;
      font-weight: 600;
      padding: 4px 9px;
      border-radius: 12px;
      background: #1c1c1e;
      border: 1px solid #2c2c2e;
      color: #f5f5f7;
      cursor: pointer;
      user-select: none;
      transition: all 0.15s ease;
    }

    .sort-btn:active {
      transform: scale(0.94);
      background: #2a2a2d;
    }

    .sort-icon {
      color: var(--accent);
      font-size: 13px;
      font-weight: 700;
    }

    .count-badge {
      font-size: 12px;
      font-weight: 600;
      padding: 4px 8px;
      border-radius: 12px;
      background: var(--card-border);
      color: var(--accent);
    }

    /* Barra di ricerca compatta */
    .search-box {
      position: relative;
      width: 100%;
    }

    .search-box input {
      width: 100%;
      height: 36px;
      background: #1c1c1e;
      border: 1px solid #2c2c2e;
      border-radius: 10px;
      padding: 0 12px 0 34px;
      color: #fff;
      font-size: 14px;
      outline: none;
      transition: border 0.2s;
    }

    .search-box input:focus {
      border-color: var(--accent);
      background: #242426;
    }

    .search-icon {
      position: absolute;
      left: 10px;
      top: 50%;
      transform: translateY(-50%);
      font-size: 14px;
      color: var(--text-sub);
    }

    /* Filtro categorie scrollabile orizzontalmente per 375pt */
    .filter-scroll {
      display: flex;
      gap: 8px;
      overflow-x: auto;
      padding: 12px 16px 8px 16px;
      scrollbar-width: none;
      -webkit-overflow-scrolling: touch;
    }

    .filter-scroll::-webkit-scrollbar {
      display: none;
    }

    .chip {
      flex: 0 0 auto;
      padding: 6px 12px;
      border-radius: 20px;
      font-size: 13px;
      font-weight: 500;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      color: var(--text-sub);
      cursor: pointer;
      transition: all 0.15s ease;
    }

    .chip.active {
      background: var(--accent);
      color: #ffffff;
      border-color: var(--accent);
      font-weight: 600;
    }

    /* Contenitore Cards */
    .feed {
      padding: 0 14px;
      display: flex;
      flex-direction: column;
      gap: 14px;
      max-width: 500px;
      margin: 0 auto;
      width: 100%;
    }

    /* Scheda Post ottimizzata */
    .card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 16px;
      padding: 14px;
      display: flex;
      flex-direction: column;
      gap: 10px;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
      transition: transform 0.1s ease;
    }

    .card:active {
      transform: scale(0.99);
    }

    .card-meta {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 11px;
    }

    .card-cat {
      font-weight: 600;
      color: var(--accent);
      background: var(--accent-glow);
      padding: 3px 8px;
      border-radius: 6px;
    }

    .card-date {
      color: var(--text-sub);
    }

    .card-title {
      font-size: 16px;
      font-weight: 700;
      line-height: 1.3;
      color: #ffffff;
      letter-spacing: -0.015em;
    }

    .card-author {
      font-size: 13px;
      color: var(--text-sub);
      display: flex;
      align-items: center;
      gap: 4px;
    }

    .card-author span {
      color: #fff;
      font-weight: 600;
    }

    .card-summary {
      background: rgba(255, 255, 255, 0.03);
      border-left: 3px solid var(--accent);
      padding: 8px 10px;
      border-radius: 0 8px 8px 0;
      font-size: 13.5px;
      line-height: 1.4;
      color: #e5e5ea;
    }

    .points-list {
      list-style: none;
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin-top: 2px;
    }

    .point-item {
      font-size: 12.5px;
      line-height: 1.35;
      color: #d1d1d6;
      display: flex;
      gap: 6px;
    }

    .point-item::before {
      content: "•";
      color: var(--accent);
      font-weight: bold;
    }

    .use-case-box {
      font-size: 12px;
      color: #8e8e93;
      background: #18181b;
      padding: 6px 10px;
      border-radius: 8px;
    }

    .use-case-box b {
      color: #ff9f0a;
    }

    .tags-container {
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
      margin-top: 2px;
    }

    .tag-chip {
      font-size: 11px;
      color: #64d2ff;
      background: rgba(100, 210, 255, 0.08);
      padding: 2px 7px;
      border-radius: 4px;
    }

    .card-actions {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-top: 4px;
      padding-top: 8px;
      border-top: 0.5px solid rgba(255, 255, 255, 0.08);
    }

    .btn-x {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-size: 12px;
      font-weight: 600;
      color: #000;
      background: #fff;
      padding: 6px 12px;
      border-radius: 16px;
      text-decoration: none;
    }

    .btn-expand {
      font-size: 12px;
      color: var(--text-sub);
      background: none;
      border: none;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 3px;
    }

    .original-text {
      display: none;
      font-size: 12px;
      color: #8e8e93;
      background: #09090b;
      padding: 10px;
      border-radius: 8px;
      white-space: pre-wrap;
      line-height: 1.4;
      border: 1px dashed #27272a;
    }

    .empty-state {
      text-align: center;
      padding: 40px 20px;
      color: var(--text-sub);
    }

    /* Pulsante Torna Su */
    .scroll-top {
      position: fixed;
      bottom: calc(var(--safe-bottom) + 15px);
      right: 15px;
      width: 40px;
      height: 40px;
      border-radius: 20px;
      background: rgba(41, 151, 255, 0.85);
      color: #fff;
      display: none;
      align-items: center;
      justify-content: center;
      font-size: 18px;
      border: none;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
      z-index: 99;
    }
  </style>
</head>
<body>

  <header>
    <div class="header-top">
      <div class="brand">
        <span class="brand-icon">🔖</span>
        <span class="brand-title">Bookmarks-X</span>
      </div>
      <div class="header-actions">
        <button class="sort-btn" id="sortToggleBtn" onclick="toggleSort()" title="Inverti ordine cronologico">
          <span class="sort-icon" id="sortIcon">↓</span>
          <span class="sort-label" id="sortLabel">Recenti</span>
        </button>
        <div class="count-badge" id="totalCount">0 post</div>
      </div>
    </div>
    <div class="search-box">
      <span class="search-icon">🔍</span>
      <input type="text" id="searchInput" placeholder="Cerca titolo, autore o tag..." autocomplete="off">
    </div>
  </header>

  <div class="filter-scroll" id="filterBar">
    <button class="chip active" data-cat="all">Tutti</button>
    <button class="chip" data-cat="🤖 Agenti & Coding AI">🤖 Agenti</button>
    <button class="chip" data-cat="💡 Prompt & Guide">💡 Prompt</button>
    <button class="chip" data-cat="🛠️ Strumenti & App">🛠️ Strumenti</button>
    <button class="chip" data-cat="🔬 Modelli & Ricerca">🔬 Modelli</button>
    <button class="chip" data-cat="⚡ Risorse & GitHub">⚡ GitHub</button>
    <button class="chip" data-cat="📰 News & Trend">📰 News</button>
  </div>

  <main class="feed" id="feedContainer">
    <div class="empty-state">Caricamento dei post salvati...</div>
  </main>

  <button class="scroll-top" id="scrollTopBtn" onclick="window.scrollTo({top: 0, behavior: 'smooth'})">↑</button>

  <script>
    let allBookmarks = [];
    let currentCategory = 'all';
    let sortOrder = 'desc'; // 'desc' = più recenti prima, 'asc' = meno recenti prima

    function toggleSort() {
      sortOrder = (sortOrder === 'desc') ? 'asc' : 'desc';
      const icon = document.getElementById('sortIcon');
      const label = document.getElementById('sortLabel');
      if (sortOrder === 'desc') {
        icon.textContent = '↓';
        label.textContent = 'Recenti';
      } else {
        icon.textContent = '↑';
        label.textContent = 'Meno recenti';
      }
      renderFeed();
    }

    async function loadBookmarks() {
      try {
        const res = await fetch('bookmarks.json?t=' + Date.now());
        allBookmarks = await res.json();
        renderFeed();
      } catch (e) {
        document.getElementById('feedContainer').innerHTML =
          '<div class="empty-state">⚠️ Impossibile caricare i segnalibri. Riprova più tardi.</div>';
      }
    }

    function renderFeed() {
      const feed = document.getElementById('feedContainer');
      const query = document.getElementById('searchInput').value.toLowerCase().trim();

      const filtered = allBookmarks.filter(item => {
        const matchesCat = currentCategory === 'all' || item.category === currentCategory;
        const matchesSearch = !query ||
          item.title.toLowerCase().includes(query) ||
          item.author.toLowerCase().includes(query) ||
          item.summary.toLowerCase().includes(query) ||
          (item.tags && item.tags.some(t => t.toLowerCase().includes(query)));
        return matchesCat && matchesSearch;
      });

      // Ordinamento cronologico dinamico
      filtered.sort((a, b) => {
        const tsA = a.timestamp || 0;
        const tsB = b.timestamp || 0;
        return sortOrder === 'desc' ? (tsB - tsA) : (tsA - tsB);
      });

      document.getElementById('totalCount').innerText = `${filtered.length} post`;

      if (filtered.length === 0) {
        feed.innerHTML = '<div class="empty-state">Nessun post trovato per questa ricerca.</div>';
        return;
      }

      feed.innerHTML = filtered.map(item => {
        const pointsHtml = item.points && item.points.length ?
          `<ul class="points-list">${item.points.map(p => `<li class="point-item">${escapeHtml(p)}</li>`).join('')}</ul>` : '';

        const tagsHtml = item.tags && item.tags.length ?
          `<div class="tags-container">${item.tags.map(t => `<span class="tag-chip">${escapeHtml(t)}</span>`).join('')}</div>` : '';

        const useCaseHtml = item.use_case ?
          `<div class="use-case-box">🎯 <b>Utile per:</b> ${escapeHtml(item.use_case)}</div>` : '';

        return `
          <article class="card">
            <div class="card-meta">
              <span class="card-cat">${escapeHtml(item.category || '📌 Risorsa')}</span>
              <span class="card-date">📅 ${escapeHtml(item.date || '')}</span>
            </div>

            <h2 class="card-title">${escapeHtml(item.title)}</h2>

            <div class="card-author">
              Da <span>${escapeHtml(item.author)}</span>
            </div>

            <div class="card-summary">
              ${escapeHtml(item.summary)}
            </div>

            ${pointsHtml}
            ${useCaseHtml}
            ${tagsHtml}

            <div class="card-actions">
              <a href="${item.url}" target="_blank" rel="noopener" class="btn-x">
                Apri su 𝕏
              </a>
              <button class="btn-expand" onclick="toggleText('${item.id}', this)">
                Leggi originale ▾
              </button>
            </div>

            <div class="original-text" id="text-${item.id}">
${escapeHtml(item.text || '')}
            </div>
          </article>
        `;
      }).join('');
    }

    function toggleText(id, btn) {
      const el = document.getElementById('text-' + id);
      if (el.style.display === 'block') {
        el.style.display = 'none';
        btn.innerText = 'Leggi originale ▾';
      } else {
        el.style.display = 'block';
        btn.innerText = 'Nascondi originale ▴';
      }
    }

    function escapeHtml(str) {
      return (str || '')
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }

    // Event Listeners
    document.getElementById('searchInput').addEventListener('input', renderFeed);

    document.querySelectorAll('.chip').forEach(chip => {
      chip.addEventListener('click', () => {
        document.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
        chip.classList.add('active');
        currentCategory = chip.getAttribute('data-cat');
        renderFeed();
      });
    });

    window.addEventListener('scroll', () => {
      const btn = document.getElementById('scrollTopBtn');
      if (window.scrollY > 300) {
        btn.style.display = 'flex';
      } else {
        btn.style.display = 'none';
      }
    });

    // Avvio
    loadBookmarks();
  </script>
</body>
</html>
"""

    html_path = DOCS_DIR / "index.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    return html_path
