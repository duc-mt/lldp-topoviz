#!/usr/bin/env python3
"""
==============================================================================
Module Name:   main.py
Description:   CLI entry point and pipeline orchestrator: crawl a network over
               LLDP from a seed device, build the topology graph and write an
               interactive HTML diagram.
Author:        Mai Tan Duc <ducmai.network@gmail.com>
Created:       2026-10-10
Version:       1.0.0
License:       MIT
==============================================================================
Usage:         python3 main.py [options]
Notes:         Requires Python 3.10+
==============================================================================
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src import MapperError, __version__
from src.config import ConnectionSettings, SeedSettings, load_settings, section
from src.crawler import CrawlConfig, Crawler, CrawlResult
from src.graph_engine import STATUS_DISCOVERED, STATUS_FAILED, GraphEngine
from src.parser import LldpParser
from src.poller import BasePoller, DevicePoller, FilePoller, RawCapture
from src.report import blind_spots_payload, format_blind_spots
from src.vendors import load_custom_vendors, supported_device_types
from src.visualizer import TopologyVisualizer, VisualOptions


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = _get_base_dir()
if Path("config/settings.yaml").exists():
    DEFAULT_CONFIG = Path("config/settings.yaml")
elif Path("settings.yaml").exists():
    DEFAULT_CONFIG = Path("settings.yaml")
else:
    DEFAULT_CONFIG = BASE_DIR / "config" / "settings.yaml"

DATA_DIR = Path("data") if Path("data").exists() else BASE_DIR / "data"
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    TEMPLATE_DIR = Path(sys._MEIPASS) / "templates"
else:
    TEMPLATE_DIR = BASE_DIR / "templates"

OUTPUT_HTML = Path("output/topology.html")

EXIT_OK, EXIT_ERROR, EXIT_UNEXPECTED, EXIT_PARTIAL = 0, 1, 2, 3

logger = logging.getLogger("lldp_mapper")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Crawl a network over LLDP from a seed device and draw its topology."
    )
    ap.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="settings file (default: %(default)s)",
    )
    ap.add_argument(
        "--seed",
        metavar="HOST",
        help="seed device IP or DNS name, overrides seed.host from the settings file",
    )
    ap.add_argument(
        "--device-type",
        metavar="TYPE",
        help="Netmiko device type of the seed, overrides the settings file "
        f"({', '.join(supported_device_types())})",
    )
    ap.add_argument(
        "--allow",
        action="append",
        default=[],
        metavar="CIDR",
        help="add an allowed subnet (repeatable); the crawler only logs in to devices inside these",
    )
    ap.add_argument(
        "--max-depth",
        type=int,
        metavar="N",
        help="how many hops from the seed to crawl (0 = the seed only)",
    )
    ap.add_argument(
        "--max-devices",
        type=int,
        metavar="N",
        help="maximum number of devices to log in to",
    )
    ap.add_argument(
        "--no-port-channels",
        action="store_true",
        help="do not read port-channels (saves one command per device; links are not bundled)",
    )
    ap.add_argument(
        "--protocols",
        metavar="LIST",
        help="discovery protocols to read, in priority order, comma separated "
        "(default from the settings file: lldp,cdp)",
    )
    ap.add_argument(
        "--no-link-checks",
        action="store_true",
        help="do not read interface speed/duplex or check links (saves a command per device)",
    )
    ap.add_argument(
        "--replay",
        type=Path,
        metavar="DIR",
        help="read saved CLI output (<host>.txt + index.yaml) from DIR instead of using SSH",
    )
    ap.add_argument(
        "--save-raw",
        type=Path,
        metavar="DIR",
        help="also write each device's raw CLI output to DIR; replayable with --replay "
        "(costs one extra command per device)",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_HTML,
        metavar="PATH",
        help="interactive diagram output path (default: %(default)s)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="crawl and draw without writing the JSON snapshot to data/",
    )
    ap.add_argument(
        "--init",
        action="store_true",
        help="generate sample settings.yaml in the current directory and exit",
    )
    ap.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return ap.parse_args(argv)


def print_summary(result: CrawlResult, engine: GraphEngine) -> None:
    """Print a human-readable crawl report to the console."""
    graph = engine.graph
    counts = engine.counts()
    print()
    print("=" * 64)
    print(" LLDP Topology Summary")
    print("=" * 64)
    print(f" Seed      : {result.seed}")
    print(
        f" Devices   : {graph.number_of_nodes()} "
        f"({counts['crawled']} crawled, {counts['failed']} failed, "
        f"{counts['discovered']} not crawled)"
    )
    print(
        f" Links     : {graph.number_of_edges()} ({result.bundled_links} in port-channels, "
        f"{result.warned_links} with warnings)"
    )
    print(f" Sessions  : {result.attempted} attempted in {result.duration:.1f}s")
    if result.duplicates:
        print(f" Duplicates: {result.duplicates} address(es) of devices already crawled")

    def section_out(title: str, rows: list[str]) -> None:
        if rows:
            print(f"\n {title} ({len(rows)}):")
            for row in rows:
                print(f"   {row}")

    failed = [
        f"{engine.label(n)} ({a['address'] or n}): {a['error']}"
        for n, a in sorted(graph.nodes(data=True))
        if a["status"] == STATUS_FAILED
    ]
    skipped = [
        f"{engine.label(n)}: {a['skip_reason'] or 'seen as a neighbor only'}"
        for n, a in sorted(graph.nodes(data=True))
        if a["status"] == STATUS_DISCOVERED
    ]
    section_out("!! Failed", failed)
    section_out("Not crawled", skipped)
    if result.limit_hit:
        print("\n !! crawl.max_devices was reached; the map may be incomplete.")
    blind_spots = format_blind_spots(result.blind_spots, counts["crawled"])
    if blind_spots:
        print(f"\n{blind_spots}")


def build_poller(
    args: argparse.Namespace,
    settings: dict,
    config_path: Path,
    crawl_config: CrawlConfig,
) -> BasePoller:
    """Pick the data source. Swap or extend here to add another transport."""
    collect_lag = crawl_config.collect_port_channels and not args.no_port_channels
    collect_status = (
        crawl_config.collect_interface_status
        and crawl_config.link_checks.enabled
        and not args.no_link_checks
    )
    if args.replay:
        return FilePoller(
            args.replay,
            collect_lag=collect_lag,
            protocols=crawl_config.protocols,
            collect_status=collect_status,
        )
    return DevicePoller(
        ConnectionSettings.from_settings(settings, source=config_path),
        template_dirs=[TEMPLATE_DIR],
        keep_raw=bool(args.save_raw),
        collect_lag=collect_lag,
        protocols=crawl_config.protocols,
        collect_status=collect_status,
    )


def run(args: argparse.Namespace) -> int:
    """Execute the full pipeline once."""
    if args.init:
        target = Path("settings.yaml")
        if target.exists():
            print("[.] settings.yaml already exists, skipping")
            return EXIT_OK
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            sample_path = Path(sys._MEIPASS) / "config" / "settings.yaml"
        else:
            sample_path = BASE_DIR / "config" / "settings.yaml"

        if sample_path.exists():
            target.write_text(sample_path.read_text(encoding="utf-8"), encoding="utf-8")
            print("[+] Created sample settings.yaml")
        else:
            print("[!] Could not locate bundled sample settings.yaml")
            return EXIT_ERROR
        print(
            "\nSample configuration created! "
            "Edit settings.yaml with your seed IP, device type, and credentials."
        )
        return EXIT_OK

    try:
        settings = load_settings(args.config, required=not args.replay)
    except Exception:
        if not args.config.exists():
            print(f"Error: configuration file '{args.config}' not found.")
            print("\nTip: Run with '--init' to create a sample settings.yaml template.\n")
            return EXIT_ERROR
        raise

    # Build every stage first so configuration problems surface before any SSH traffic.
    load_custom_vendors(settings.get("custom_vendors"), base_dir=BASE_DIR)
    seed = SeedSettings.from_settings(settings, host=args.seed, device_type=args.device_type)
    crawl_config = CrawlConfig.from_settings(
        settings.get("crawl"),
        extra_subnets=args.allow,
        max_depth=args.max_depth,
        max_devices=args.max_devices,
        link_checks=settings.get("link_checks"),
        protocols=[p for p in (args.protocols or "").split(",") if p.strip()] or None,
    )
    poller = build_poller(args, settings, args.config, crawl_config)
    parser = LldpParser(template_dirs=[TEMPLATE_DIR])
    engine = GraphEngine(data_dir=DATA_DIR)
    visualizer = TopologyVisualizer(
        output_path=args.output,
        options=VisualOptions.from_settings(section(settings, "visualization")),
    )
    capture = RawCapture(args.save_raw) if args.save_raw else None

    crawler = Crawler(
        poller=poller,
        parser=parser,
        engine=engine,
        config=crawl_config,
        seed_host=seed.host,
        seed_device_type=seed.device_type,
        raw_sink=capture.write if capture else None,
    )
    result = crawler.run()  # 1-3. Poll, parse and build the graph, device by device

    print_summary(result, engine)  # shown even if rendering fails below
    metadata = {
        "seed": seed.host,
        "device_type": seed.device_type,
        "source": str(args.replay) if args.replay else "ssh",
        "sessions": result.attempted,
        "failed": [{"host": h, "error": e} for h, e in result.failures],
        "partial": result.partial,
        "blind_spots": blind_spots_payload(result.blind_spots),
    }
    if args.dry_run:
        print("\n [DRY RUN] Snapshot not written.")
    else:
        engine.save(metadata)  # 4. Snapshot
    if args.save_raw:
        logger.info("Raw CLI output saved to %s", args.save_raw)
    page = visualizer.render(engine.graph, metadata)  # 5. Visualize
    print(f"\n Diagram   : {page}\n")
    return EXIT_PARTIAL if result.partial else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    try:
        return run(args)
    except MapperError as exc:
        logger.error("%s", exc)
        return EXIT_ERROR
    except KeyboardInterrupt:
        logger.error("Interrupted")
        return 130
    except Exception:  # last-resort guard: log the traceback, return a clean failure code
        logger.exception("Unexpected error")
        return EXIT_UNEXPECTED


if __name__ == "__main__":
    sys.exit(main())
