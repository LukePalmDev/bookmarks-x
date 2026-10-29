#!/usr/bin/env python3
"""Gestione del servizio macOS in background (LaunchAgent) per X-Gemini Bookmarks."""

import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time

LABEL = "com.user.x-gemini-bookmarks"


def python_runtime(python: Path):
    """Usa l'app framework macOS se disponibile per ereditare correttamente i permessi."""
    if sys.platform == "darwin":
        app = python.resolve().parent.parent / "Resources/Python.app/Contents/MacOS/Python"
        if app.is_file():
            return app, {"__PYVENV_LAUNCHER__": str(python)}
    return python, {}


def bootstrap(domain: str, plist: Path):
    """Avvia il servizio registrandolo in launchctl con retry."""
    for attempt in range(5):
        res = subprocess.run(["launchctl", "bootstrap", domain, str(plist)], capture_output=True, text=True)
        if res.returncode == 0:
            return
        time.sleep(2)
    sys.exit(f"launchctl non riesce ad avviare il servizio: {res.stderr}")


def verify_service(target: str):
    """Verifica che il servizio resti effettivamente attivo dopo l'avvio."""
    for _ in range(5):
        time.sleep(1)
        status = subprocess.run(["launchctl", "print", target], capture_output=True, text=True)
        if status.returncode == 0 and "\tstate = running\n" in status.stdout:
            return
    sys.exit(f"Il servizio non è rimasto in esecuzione. Controlla i log in /tmp/x-gemini-bookmarks.log")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="Installa e avvia il servizio in background")
    parser.add_argument("--uninstall", action="store_true", help="Ferma e disinstalla il servizio")
    parser.add_argument("--restart", action="store_true", help="Riavvia il servizio attivo")
    parser.add_argument("--status", action="store_true", help="Mostra lo stato del servizio")
    args = parser.parse_args()

    project = Path(__file__).resolve().parent
    python = project / ".venv/bin/python"
    domain = f"gui/{os.getuid()}"
    target_service = f"{domain}/{LABEL}"
    launch_agent_plist = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"

    if args.status:
        res = subprocess.run(["launchctl", "print", target_service], capture_output=True, text=True)
        if res.returncode == 0:
            print(f"✓ Il servizio {LABEL} è REGISTRATO su launchd.")
            for line in res.stdout.splitlines():
                if "state =" in line or "pid =" in line:
                    print(f"  {line.strip()}")
        else:
            print(f"✗ Il servizio {LABEL} non è attivo.")
        return

    if args.uninstall:
        subprocess.run(["launchctl", "bootout", target_service], capture_output=True)
        if launch_agent_plist.exists():
            launch_agent_plist.unlink()
        print(f"✓ Servizio {LABEL} fermato e rimosso con successo.")
        return

    if args.restart:
        subprocess.run(["launchctl", "kickstart", "-k", target_service], check=True)
        print(f"✓ Servizio {LABEL} riavviato.")
        return

    if not python.exists():
        sys.exit("Ambiente virtuale .venv non trovato. Crealo prima con: python3 -m venv .venv")

    runtime, environment = python_runtime(python)
    logs_dir = project / "logs"
    logs_dir.mkdir(exist_ok=True)
    log_file = logs_dir / "service.log"

    env_vars = {
        "PATH": f"{project}/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
        "PYTHONPATH": str(project),
        **environment,
    }

    config = {
        "Label": LABEL,
        "ProgramArguments": [str(runtime), "-u", str(project / "main.py"), "watch"],
        "WorkingDirectory": str(project),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "StandardOutPath": str(log_file),
        "StandardErrorPath": "/tmp/x-gemini-bookmarks.log",
        "EnvironmentVariables": env_vars,
    }

    # Scrivi il file plist
    launch_agent_plist.parent.mkdir(parents=True, exist_ok=True)
    launch_agent_plist.write_bytes(plistlib.dumps(config))
    print(f"✓ Configurazione LaunchAgent generata: {launch_agent_plist}")

    if not args.install:
        print("Per installare e avviare il servizio in background, esegui:")
        print("  python install-service.py --install")
        return

    # Se già registrato, fermalo prima
    subprocess.run(["launchctl", "bootout", target_service], capture_output=True)
    bootstrap(domain, launch_agent_plist)
    verify_service(target_service)
    print(f"✓ Servizio {LABEL} installato e avviato con successo in background 24/7!")
    print(f"  Log stdout: {log_file}")
    print(f"  Log stderr: /tmp/x-gemini-bookmarks.log")


if __name__ == "__main__":
    main()
