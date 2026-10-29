"""Interfaccia CLI ricca e interattiva per X-Gemini Bookmarks."""

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table
from rich.text import Text

from src.analyzer.claude import ClaudeAnalyzer
from src.analyzer.gemini import GeminiAnalyzer
from src.auth.x_auth import XAuthManager
from src.config import BASE_DIR, config
from src.service.sync_service import SyncService, get_default_analyzer
from src.storage.db import Database
from src.storage.markdown_exporter import MarkdownExporter

console = Console()


def print_header():
    engine_name = "Claude CLI" if config.analyzer_engine == "claude" else f"Gemini ({config.gemini_model})"
    console.print(
        Panel.fit(
            f"[bold cyan]Bookmarks-X[/bold cyan] — [dim]Sincronizzazione X e Analisi AI ({engine_name})[/dim]",
            border_style="cyan",
        )
    )


async def cmd_sync(fetch_all: bool = False):
    print_header()
    sync_service = SyncService()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("Avvio sincronizzazione...", total=100)

        def on_progress(desc: str, current: int, total: int):
            if total > 0:
                progress.update(task_id, description=f"{desc} ({current}/{total})", completed=(current / total) * 100)
            else:
                progress.update(task_id, description=f"{desc} ({current})")

        stats = await sync_service.run_sync(fetch_all=fetch_all, progress_callback=on_progress)
        progress.update(task_id, description="Completato!", completed=100)

    # Tabella riassuntiva
    table = Table(title="Riepilogo Sincronizzazione", border_style="cyan")
    table.add_column("Metrica", style="bold")
    table.add_column("Valore", justify="right")

    table.add_row("Segnalibri trovati su X", str(stats["fetched_from_x"]))
    table.add_row("Nuovi post archiviati", f"[green]{stats['new_saved']}[/green]")
    table.add_row("Media scaricati", str(stats["media_downloaded"]))
    table.add_row("Post analizzati con Gemini", f"[bold cyan]{stats['analyzed_by_gemini']}[/bold cyan]")
    if stats["analysis_errors"] > 0:
        table.add_row("Errori di analisi", f"[red]{stats['analysis_errors']}[/red]")
    table.add_row("Totale post nel database", str(stats["total_in_db"]))

    console.print(table)

    if not stats["gemini_configured"]:
        console.print(
            "\n[yellow]⚠️ Nota su Gemini:[/yellow] La chiave `GEMINI_API_KEY` non è ancora presente nel file `.env`.\n"
            "I post sono stati scaricati e memorizzati localmente. Quando inserirai la chiave API,\n"
            "puoi eseguire `python main.py analyze-pending` per analizzarli tutti in automatico!"
        )


async def cmd_watch(interval: Optional[int] = None):
    print_header()
    interval_sec = interval or config.sync_interval_seconds
    console.print(
        f"[green]Modalità Watch attiva:[/green] controllo continuo dei nuovi segnalibri ogni [bold]{interval_sec}[/bold] secondi."
    )
    console.print("[dim]Premi CTRL+C per arrestare il demone in qualsiasi momento.[/dim]\n")

    sync_service = SyncService()

    def on_cycle(stats):
        now = datetime.now().strftime("%H:%M:%S")
        if stats["new_saved"] > 0 or stats["analyzed_by_gemini"] > 0:
            console.print(
                f"[{now}] [green]✓[/green] Nuovi: [bold]{stats['new_saved']}[/bold] | Analizzati: [cyan]{stats['analyzed_by_gemini']}[/cyan] | Totale DB: {stats['total_in_db']}"
            )
        else:
            console.print(f"[{now}] [dim]Nessun nuovo segnalibro salvato. In attesa del prossimo controllo...[/dim]")

    try:
        await sync_service.run_watch_loop(interval_seconds=interval_sec, on_cycle_complete=on_cycle)
    except (KeyboardInterrupt, asyncio.CancelledError):
        console.print("\n[yellow]Watch interrotto dall'utente.[/yellow]")


def cmd_list(limit: int = 20, pending_only: bool = False):
    print_header()
    db = Database()
    status = "pending_analysis" if pending_only else None
    items = db.get_recent_bookmarks(limit=limit, status=status)

    if not items:
        console.print("[dim]Nessun segnalibro trovato nel database.[/dim]")
        return

    table = Table(title=f"Ultimi {len(items)} Segnalibri Archiviati", border_style="cyan")
    table.add_column("Tweet ID", style="dim", width=20)
    table.add_column("Autore", style="bold green", width=22)
    table.add_column("Titolo Gemini", style="cyan")
    table.add_column("Stato", width=12)

    for item in items:
        status_val = item.get("status")
        if status_val == "analyzed":
            status_text = "[green]✓ Analizzato[/green]"
        elif status_val == "failed_analysis":
            status_text = "[red]✗ Errore[/red]"
        else:
            status_text = "[yellow]⏳ In attesa[/yellow]"

        author = item.get("gemini_author") or f"@{item.get('author_screen_name', '')}"
        title = item.get("gemini_title") or item.get("tweet_text", "")[:60] + "..."

        table.add_row(item["tweet_id"], author, title, status_text)

    console.print(table)


def cmd_show(tweet_id: str):
    print_header()
    db = Database()
    item = db.get_bookmark(tweet_id)
    if not item:
        console.print(f"[red]Segnalibro con ID '{tweet_id}' non trovato.[/red]")
        return

    author = item.get("gemini_author") or f"{item.get('author_name')} (@{item.get('author_screen_name')})"
    title = item.get("gemini_title") or "Non ancora analizzato da Gemini"

    console.print(Panel(f"[bold cyan]{title}[/bold cyan]\n[dim]Autore:[/dim] [bold]{author}[/bold]", title=f"Post ID: {tweet_id}"))
    console.print(f"[bold]URL:[/bold] {item.get('tweet_url')}")
    console.print(f"[bold]Data originale:[/bold] {item.get('created_at')}")
    console.print(f"[bold]Data archiviazione:[/bold] {item.get('bookmarked_at')}\n")

    console.print("[bold]Testo:[/bold]")
    console.print(Panel(item.get("tweet_text", "").strip(), border_style="dim"))

    if item.get("gemini_analysis_json"):
        console.print("\n[bold cyan]Dati Analisi Gemini:[/bold cyan]")
        console.print(Panel(item["gemini_analysis_json"], border_style="cyan"))


async def cmd_analyze_pending():
    print_header()
    analyzer = get_default_analyzer()
    if not analyzer.is_configured():
        console.print(
            f"[red]Motore {config.analyzer_engine} non configurato o non accessibile.[/red]"
        )
        return

    db = Database()
    pending = db.get_pending_analysis(limit=100)
    if not pending:
        console.print("[green]Nessun post in attesa di analisi. Tutti i post sono già aggiornati![/green]")
        return

    console.print(f"[cyan]Avvio analisi per {len(pending)} post con {config.analyzer_engine.capitalize()}...[/cyan]\n")
    exporter = MarkdownExporter()
    from src.notifier.telegram import TelegramNotifier
    notifier = TelegramNotifier()

    for idx, item in enumerate(pending, 1):
        t_id = item["tweet_id"]
        author_hint = f"@{item.get('author_screen_name')}"
        console.print(f"[{idx}/{len(pending)}] Analisi post {t_id} ({author_hint})...", end=" ")
        try:
            res = analyzer.analyze_tweet(item)
            author = res.get("autore", author_hint)
            title = res.get("titolo", "Post X")
            db.update_analysis_success(t_id, author, title, res)

            item["gemini_author"] = author
            item["gemini_title"] = title
            item["gemini_analysis_json"] = res
            if config.export_markdown:
                exporter.export_bookmark(item)

            if notifier.is_configured():
                notifier.notify_bookmark(item)

            console.print(f"[green]✓ Fatto:[/green] [bold]{title}[/bold]")
            # Con Claude CLI pausa breve di 2s
            await asyncio.sleep(2.0)
        except Exception as exc:
            db.update_analysis_failure(t_id, str(exc))
            console.print(f"[red]✗ Fallito:[/red] {exc}")

    console.print("\n[bold green]Analisi completata con successo![/bold green]")


def cmd_test():
    print_header()
    console.print("[bold]Diagnostica di Sistema[/bold]\n")

    # 1. Test X
    console.print("1. Verifica connessione X (Twitter)...", end=" ")
    try:
        cookies = XAuthManager.get_credentials()
        valid, info = XAuthManager.validate_session(cookies)
        if valid:
            console.print(f"[green]✓ OK[/green] ({info})")
        else:
            console.print(f"[red]✗ Fallito[/red] ({info})")
    except Exception as e:
        console.print(f"[red]✗ Errore:[/red] {e}")

    # 2. Test Motore di Analisi (Claude CLI o Gemini)
    if config.analyzer_engine == "claude":
        console.print(f"2. Verifica Claude CLI ({config.claude_model})...", end=" ")
        analyzer = ClaudeAnalyzer(model=config.claude_model)
        valid_claude, claude_info = analyzer.test_connection()
        if valid_claude:
            console.print(f"[green]✓ OK[/green] ({claude_info})")
        else:
            console.print(f"[red]✗ Fallito[/red] ({claude_info})")
    else:
        console.print(f"2. Verifica Google Gemini ({config.gemini_model})...", end=" ")
        analyzer = GeminiAnalyzer()
        valid_gemini, gemini_info = analyzer.test_connection()
        if valid_gemini:
            console.print(f"[green]✓ OK[/green] ({gemini_info})")
        else:
            console.print(f"[yellow]⚠️ In attesa di configurazione[/yellow] ({gemini_info})")

    # 3. Test Storage & Percorsi
    console.print("3. Verifica percorsi e database...", end=" ")
    try:
        db = Database()
        stats = db.get_stats()
        console.print(f"[green]✓ OK[/green] (DB: {db.db_path.name}, Post archiviati: {stats['total']})")
    except Exception as e:
        console.print(f"[red]✗ Errore DB:[/red] {e}")

    # 4. Test Notifiche Telegram
    console.print("4. Verifica notifiche Telegram...", end=" ")
    from src.notifier.telegram import TelegramNotifier
    notifier = TelegramNotifier()
    valid_tg, tg_info = notifier.test_connection()
    if valid_tg:
        console.print(f"[green]✓ OK[/green] (Bot: {tg_info}, Chat ID: {notifier.chat_id})")
    else:
        console.print(f"[yellow]⚠️ In attesa di configurazione[/yellow] ({tg_info})")


def cmd_export(output_format: str = "markdown"):
    print_header()
    db = Database()
    bookmarks = db.get_recent_bookmarks(limit=1000)
    console.print(f"[cyan]Esportazione di {len(bookmarks)} segnalibri in formato {output_format}...[/cyan]")

    if output_format == "markdown":
        exporter = MarkdownExporter()
        for b in bookmarks:
            exporter.export_bookmark(b)
        console.print(f"[green]✓ Tutti i file Markdown sono stati generati in:[/green] {config.archive_dir}")
    else:
        import json
        out_file = config.data_dir / "bookmarks_export.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(bookmarks, f, ensure_ascii=False, indent=2)
        console.print(f"[green]✓ Esportazione JSON salvata in:[/green] {out_file}")


def cmd_set_telegram(token: Optional[str] = None, chat_id: Optional[str] = None):
    print_header()
    env_file = BASE_DIR / ".env"
    env_lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []

    updated = {}
    if token:
        updated["TELEGRAM_BOT_TOKEN"] = token.strip()
    if chat_id:
        updated["TELEGRAM_CHAT_ID"] = chat_id.strip()

    new_lines = []
    seen = set()
    for line in env_lines:
        if "=" in line and not line.strip().startswith("#"):
            k = line.split("=", 1)[0].strip()
            if k in updated:
                new_lines.append(f"{k}={updated[k]}")
                seen.add(k)
                continue
        new_lines.append(line)

    for k, v in updated.items():
        if k not in seen:
            new_lines.append(f"{k}={v}")

    env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    console.print("[green]✓ Configurazione Telegram aggiornata nel file .env![/green]")

    from src.notifier.telegram import TelegramNotifier
    notifier = TelegramNotifier(bot_token=updated.get("TELEGRAM_BOT_TOKEN"), chat_id=updated.get("TELEGRAM_CHAT_ID"))
    ok, info = notifier.test_connection()
    if ok:
        console.print(f"[bold green]✓ Connesso con successo al bot Telegram:[/bold green] {info}")
        notifier.send_message("👋 Ciao! Questo è il canale dedicato <b>bookmarks-x</b> per i tuoi post salvati su X!")
        console.print("[green]✓ Messaggio di benvenuto inviato sul canale Telegram![/green]")
    else:
        console.print(f"[yellow]⚠️ Test di connessione:[/yellow] {info}")


async def cmd_mirror():
    """Esegue il mirroring bidirezionale (Opzione B): elimina i post rimossi da X."""
    print_header()
    sync_service = SyncService()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("Avvio sincronizzazione a specchio (Mirroring)...", total=100)

        def on_progress(desc: str, current: int, total: int):
            if total > 0:
                progress.update(task_id, description=f"{desc} ({current}/{total})", completed=(current / total) * 100)
            else:
                progress.update(task_id, description=f"{desc} ({current})")

        stats = await sync_service.run_mirror(progress_callback=on_progress)
        progress.update(task_id, description="Mirroring completato!", completed=100)

    table = Table(title="Riepilogo Mirroring a Specchio", border_style="cyan")
    table.add_column("Operazione", style="bold")
    table.add_column("Risultato", justify="right")

    table.add_row("Segnalibri attuali su X", str(stats["fetched_from_x"]))
    table.add_row("Nuovi post aggiunti", f"[green]{stats['new_saved']}[/green]")
    table.add_row("Post rimossi (non più su X)", f"[red]{stats['deleted_removed']}[/red]")
    table.add_row("Media scaricati", str(stats["media_downloaded"]))
    table.add_row("Post analizzati", str(stats["analyzed"]))
    table.add_row("Totale post nel database", str(stats["total_in_db"]))

    console.print(table)
    console.print("\n[green]✓[/green] Archivio locale e sito web GitHub Pages sincronizzati a specchio con X.")


def cmd_remove(target: str):
    """Rimuove manualmente un singolo post da DB, media, markdown e sito."""
    print_header()
    tweet_id = target.strip().rstrip("/").split("/")[-1]
    sync_service = SyncService()
    item = sync_service.db.get_bookmark(tweet_id)
    if not item:
        console.print(f"[red]Errore:[/red] Nessun post trovato con ID '{tweet_id}' nel database locale.")
        return

    author = item.get("gemini_author") or item.get("author_screen_name", "unknown")
    title = item.get("gemini_title") or item.get("tweet_text", "")[:40]
    ok = sync_service.delete_bookmark_fully(tweet_id)
    if ok:
        from src.service.sync_service import sync_site_to_git
        from src.storage.site_generator import export_site_data, generate_index_html
        export_site_data(sync_service.db)
        generate_index_html()
        sync_site_to_git(f"Rimosso post {tweet_id}")
        console.print(f"[green]✓ Post rimosso con successo![/green] ({author}: '{title}')")
        console.print("[dim]File media, scheda markdown e record database eliminati. Sito aggiornato.[/dim]")
    else:
        console.print(f"[red]Errore durante l'eliminazione del post {tweet_id}.[/red]")


def cmd_build_site():
    """Rigenera i dati del sito web e invia a GitHub Pages."""
    print_header()
    from src.service.sync_service import sync_site_to_git
    from src.storage.site_generator import export_site_data, generate_index_html
    db = Database()
    export_site_data(db)
    generate_index_html()
    sync_site_to_git("Rigenerazione manuale sito web Bookmarks-X")
    console.print("[green]✓ Sito web e bookmarks.json rigenerati e aggiornati su GitHub Pages.[/green]")


async def cmd_reanalyze(force_all: bool = False, concurrency: int = 3):
    """Ricontrolla e ri-analizza tutti i post per assicurare che abbiano tutti i punti concordati."""
    print_header()
    sync_service = SyncService()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("Verifica post da rianalizzare...", total=100)

        def on_progress(desc: str, current: int, total: int):
            if total > 0:
                progress.update(task_id, description=f"{desc} ({current}/{total})", completed=(current / total) * 100)
            else:
                progress.update(task_id, description=f"{desc} ({current})")

        stats = await sync_service.reanalyze_all(
            force_all=force_all,
            concurrency=concurrency,
            progress_callback=on_progress,
        )
        progress.update(task_id, description="Reanalisi completata!", completed=100)

    table = Table(title="Riepilogo Reanalisi Post", border_style="cyan")
    table.add_column("Metrica", style="bold")
    table.add_column("Valore", justify="right")

    table.add_row("Totale post nel database", str(stats["total_in_db"]))
    table.add_row("Post che necessitavano di analisi completa", str(stats["target_count"]))
    table.add_row("Post completati con successo", f"[green]{stats['completed']}[/green]")
    if stats["errors"] > 0:
        table.add_row("Errori riscontrati", f"[red]{stats['errors']}[/red]")

    console.print(table)
    console.print("\n[green]✓ Tutti i post sono stati aggiornati con titolo, categoria, succo, punti chiave, caso d'uso e tag![/green]")
    console.print("[dim]Le schede Markdown e il sito GitHub Pages sono stati aggiornati.[/dim]")


def main():
    parser = argparse.ArgumentParser(
        description="X-Gemini Bookmarks: Sincronizzazione automatica e analisi AI dei segnalibri X."
    )
    subparsers = parser.add_subparsers(dest="command", help="Comando da eseguire")

    # sync
    sync_p = subparsers.add_parser("sync", help="Sincronizza i segnalibri più recenti da X")
    sync_p.add_argument("--all", action="store_true", help="Scaricamento completo dello storico passato")

    # watch
    watch_p = subparsers.add_parser("watch", help="Avvia il demone di monitoraggio continuo in background")
    watch_p.add_argument("--interval", type=int, default=None, help="Intervallo di controllo in secondi")

    # list
    list_p = subparsers.add_parser("list", help="Mostra i segnalibri archiviati")
    list_p.add_argument("--limit", type=int, default=20, help="Numero di post da mostrare")
    list_p.add_argument("--pending", action="store_true", help="Mostra solo i post non ancora analizzati")

    # show
    show_p = subparsers.add_parser("show", help="Mostra il dettaglio di un singolo segnalibro")
    show_p.add_argument("tweet_id", help="ID del tweet da visualizzare")

    # analyze-pending
    subparsers.add_parser("analyze-pending", help="Analizza con Gemini tutti i post in attesa")

    # test
    subparsers.add_parser("test", help="Verifica connessioni a X, Gemini e stato locale")

    # export
    export_p = subparsers.add_parser("export", help="Esporta i segnalibri")
    export_p.add_argument("--format", choices=["markdown", "json"], default="markdown", help="Formato esportazione")

    # set-telegram
    tg_p = subparsers.add_parser("set-telegram", help="Imposta token e chat del bot Telegram bookmarks-x")
    tg_p.add_argument("--token", help="Nuovo token del bot Telegram (da @BotFather)")
    tg_p.add_argument("--chat-id", help="Chat ID o ID del canale Telegram")

    # mirror (Opzione B)
    subparsers.add_parser("mirror", help="Sincronizzazione a specchio: elimina dall'archivio e dal sito i post rimossi su X")

    # remove
    rem_p = subparsers.add_parser("remove", help="Rimuove manualmente un post per ID o URL")
    rem_p.add_argument("target", help="ID del post o URL completo di X")

    # build-site
    subparsers.add_parser("build-site", help="Rigenera il sito web per GitHub Pages e invia su GitHub")

    # reanalyze
    rean_p = subparsers.add_parser("reanalyze", help="Ricontrolla e rianalizza i post con tutti i punti salienti")
    rean_p.add_argument("--all", action="store_true", help="Forza la rianalisi di tutti i post anche se già completi")
    rean_p.add_argument("--concurrency", type=int, default=3, help="Numero di worker paralleli per Claude CLI")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "sync":
        asyncio.run(cmd_sync(fetch_all=args.all))
    elif args.command == "mirror":
        asyncio.run(cmd_mirror())
    elif args.command == "remove":
        cmd_remove(args.target)
    elif args.command == "build-site":
        cmd_build_site()
    elif args.command == "reanalyze":
        asyncio.run(cmd_reanalyze(force_all=args.all, concurrency=args.concurrency))
    elif args.command == "watch":
        asyncio.run(cmd_watch(interval=args.interval))
    elif args.command == "list":
        cmd_list(limit=args.limit, pending_only=args.pending)
    elif args.command == "show":
        cmd_show(args.tweet_id)
    elif args.command == "analyze-pending":
        asyncio.run(cmd_analyze_pending())
    elif args.command == "test":
        cmd_test()
    elif args.command == "export":
        cmd_export(output_format=args.format)
    elif args.command == "set-telegram":
        cmd_set_telegram(token=args.token, chat_id=args.chat_id)


if __name__ == "__main__":
    main()
