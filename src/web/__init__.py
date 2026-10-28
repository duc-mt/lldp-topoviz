"""
==============================================================================
Module Name:   __init__.py
Description:   Embedded web server package: serves the interactive explorer
               page and a small JSON API (status, crawl history, on-demand crawl)
               with the standard library only.
Author:        Mai Tan Duc <ducmai.network@gmail.com>
Created:       2026-10-27
Version:       1.0.0
License:       MIT
==============================================================================
Usage:         python3 main.py --serve [--port N]; see src/web/server.py
Notes:         Requires Python 3.10+. No dependency beyond the standard library.
==============================================================================
"""

from src.web.server import CrawlController, CrawlLock, create_server, serve

__all__ = ["CrawlController", "CrawlLock", "create_server", "serve"]
