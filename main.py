#!/usr/bin/env python3
"""X-Gemini Bookmarks: Entrypoint principale."""

import sys
from pathlib import Path

# Assicura che la cartella radice sia nel path di sistema
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.cli.app import main

if __name__ == "__main__":
    main()
