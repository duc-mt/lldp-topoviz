"""
==============================================================================
Module Name:   crawler.py
Description:   Breadth-first LLDP crawl: queue of addresses to visit, visited
               set, scope guard (allowed subnets, depth, device limit, exclusions,
               capabilities) and per-device failure handling.
Author:        Mai Tan Duc <ducmai.network@gmail.com>
Created:       2026-10-10
Version:       1.0.0
License:       MIT
==============================================================================
Usage:         imported by main.py (not run directly)
Notes:         Requires Python 3.10+
==============================================================================
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from src import MapperError, extract_ipv4, is_ipv4, normalize_hostname, normalize_interface
from src.config import ConfigError, as_int, section
from src.graph_engine import STATUS_CRAWLED, STATUS_FAILED, GraphEngine
from src.linkcheck import LinkCheckConfig
from src.parser import BaseParser
from src.poller import BasePoller, RawLldp
from src.vendors import guess_device_type, is_supported
from src.vendors.base import KNOWN_CAPABILITIES, LldpNeighbor, normalize_capabilities

logger = logging.getLogger(__name__)

__all__ = ["CrawlConfig", "CrawlError", "CrawlResult", "Crawler"]


class CrawlError(MapperError):
    """Raised when the crawl cannot produce a topology at all (e.g. the seed is unreachable)."""


@dataclass(frozen=True)
class CrawlConfig:
    """Crawl scope and device-type rules (``crawl:`` in settings.yaml).

    The crawler sends your credentials to every device it decides to visit, so
    ``allowed_subnets`` is mandatory: nothing outside it is ever contacted.
    Note: While ``allowed_subnets`` accepts IPv4 or IPv6 network objects, polling
    and device discovery currently target IPv4 management addresses.
    ``exclude_hostnames`` patterns are checked via ``re.search`` (unanchored substring match;
    anchor with ``^`` and ``$`` if exact matches are desired).
    """

    allowed_subnets: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]
    max_depth: int | None = 10  # seed is depth 0; None = unlimited
    max_devices: int | None = 200  # SSH sessions attempted; None = unlimited
    exclude_hostnames: tuple[re.Pattern[str], ...] = ()
    require_capabilities: frozenset[str] = frozenset({"router", "bridge"})
    default_device_type: str | None = None  # None = same as the seed
    device_type_rules: tuple[tuple[re.Pattern[str], str], ...] = ()
    device_overrides: dict[str, dict[str, str]] = field(default_factory=dict)
    name_to_ip: dict[str, str] = field(default_factory=dict)
    collect_port_channels: bool = True  # one extra command per device (LAG grouping)
    protocols: tuple[str, ...] = ("lldp", "cdp")  # discovery protocols, in priority order
    collect_interface_status: bool = True  # one extra command per device (link checks)
    link_checks: LinkCheckConfig = field(default_factory=LinkCheckConfig)

    @classmethod
    def from_settings(
        cls,
        settings: dict[str, Any] | None,
        extra_subnets: Iterable[str] = (),
        max_depth: int | None = None,
        max_devices: int | None = None,
        link_checks: dict[str, Any] | None = None,
        protocols: Iterable[str] | None = None,
    ) -> CrawlConfig:
        """Build from the ``crawl`` mapping (CLI values win over the file).

        Raises:
            ConfigError: If ``allowed_subnets`` is empty or any value is malformed.
        """
        crawl = section({"crawl": settings}, "crawl") if settings else {}

        subnets: list[Any] = []
        raw_subnets = list(crawl.get("allowed_subnets") or []) + list(extra_subnets)
        for item in raw_subnets:
            try:
                subnets.append(ipaddress.ip_network(str(item).strip(), strict=False))
            except ValueError as exc:
                raise ConfigError(f"crawl.allowed_subnets: invalid subnet {item!r}") from exc
        if not subnets:
            raise ConfigError(
                "crawl.allowed_subnets is required (or pass --allow CIDR): the crawler only "
                "logs in to devices inside these networks"
            )
        if any(isinstance(net, ipaddress.IPv6Network) for net in subnets):
            logger.warning(
                "crawl.allowed_subnets contains IPv6 network(s); note that crawler discovery "
                "currently targets IPv4 management addresses."
            )

        def limit(value: Any, name: str, minimum: int) -> int | None:
            return None if value is None else as_int(value, name, minimum)

        depth = (
            max_depth
            if max_depth is not None
            else limit(crawl.get("max_depth", 10), "crawl.max_depth", 0)
        )
        devices = (
            max_devices
            if max_devices is not None
            else limit(crawl.get("max_devices", 200), "crawl.max_devices", 1)
        )

        try:
            excludes = tuple(
                re.compile(str(p), re.IGNORECASE) for p in crawl.get("exclude_hostnames") or []
            )
            rules = tuple(
                (re.compile(str(r["pattern"]), re.IGNORECASE), str(r["device_type"]))
                for r in crawl.get("device_type_rules") or []
            )
        except (re.error, KeyError, TypeError) as exc:
            raise ConfigError(
                f"crawl.exclude_hostnames / device_type_rules are invalid ({exc!r}); rules need "
                "'pattern' and 'device_type'"
            ) from exc

        overrides: dict[str, dict[str, str]] = {}
        for key, value in (crawl.get("device_overrides") or {}).items():
            if not isinstance(value, dict):
                raise ConfigError(f"crawl.device_overrides.{key} must be a mapping")
            overrides[normalize_hostname(key)] = {k: str(v) for k, v in value.items()}

        required = crawl.get("require_capabilities", ["router", "bridge"])
        chosen = list(protocols) if protocols else list(crawl.get("discovery") or ["lldp", "cdp"])
        names = tuple(dict.fromkeys(str(p).strip().lower() for p in chosen if str(p).strip()))
        if not names:
            raise ConfigError("crawl.discovery must name at least one protocol (e.g. lldp, cdp)")
        return cls(
            protocols=names,
            collect_interface_status=bool(crawl.get("collect_interface_status", True)),
            link_checks=LinkCheckConfig.from_settings(link_checks),
            collect_port_channels=bool(crawl.get("collect_port_channels", True)),
            allowed_subnets=tuple(subnets),
            max_depth=depth,
            max_devices=devices,
            exclude_hostnames=excludes,
            require_capabilities=normalize_capabilities(required or []),
            default_device_type=(
                str(crawl["default_device_type"]) if crawl.get("default_device_type") else None
            ),
            device_type_rules=rules,
            device_overrides=overrides,
            name_to_ip={
                normalize_hostname(k): str(v) for k, v in (crawl.get("name_to_ip") or {}).items()
            },
        )

    def in_scope(self, address: str) -> bool:
        """True if ``address`` lies inside one of the allowed subnets."""
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        return any(ip in net for net in self.allowed_subnets if net.version == ip.version)


class _Skip(NamedTuple):
    """Why a neighbor is not crawled: a short machine-readable code and the readable reason."""

    code: str
    reason: str


@dataclass
class _Target:
    """One entry of the crawl queue."""

    host: str
    device_type: str
    node_id: str | None
    depth: int
    remaining_hosts: tuple[str, ...] = ()


@dataclass
class CrawlResult:
    """What the crawl did."""

    seed: str
    attempted: int = 0
    crawled: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)  # (host, error)
    duplicates: int = 0
    bundled_links: int = 0  # links that belong to a port-channel
    warned_links: int = 0  # links with at least one link-check warning
    limit_hit: bool = False
    duration: float = 0.0

    @property
    def partial(self) -> bool:
        """True if some devices failed or the device limit cut the crawl short."""
        return bool(self.failures) or self.limit_hit


class Crawler:
    """Walk the network from a seed device and fill a ``GraphEngine``."""

    def __init__(
        self,
        poller: BasePoller,
        parser: BaseParser,
        engine: GraphEngine,
        config: CrawlConfig,
        seed_host: str,
        seed_device_type: str,
        raw_sink: Callable[[RawLldp], None] | None = None,
    ) -> None:
        self.poller = poller
        self.parser = parser
        self.engine = engine
        self.config = config
        self.seed_host = seed_host
        self.seed_device_type = seed_device_type
        self.raw_sink = raw_sink
        self._visited_hosts: set[str] = set()
        self._queued_nodes: set[str] = set()
        self._dns_cache: dict[str, str] = {}

    def _resolve_host(self, host: str) -> str:
        """Resolve a hostname to IPv4 address, caching results."""
        h = host.strip()
        if h in self._dns_cache:
            return self._dns_cache[h]
        resolved = socket.gethostbyname(h)
        self._dns_cache[h] = resolved
        return resolved

    # ------------------------------------------------------------------ run
    def run(self) -> CrawlResult:
        """Crawl until the queue is empty.

        Per-device failures (timeout, authentication, parse errors) are logged, recorded as
        ``failed`` nodes and do not stop the crawl.

        Raises:
            CrawlError: If the seed device is outside allowed subnets or could not be crawled.
        """
        seed_ip = self.seed_host.strip() if is_ipv4(self.seed_host) else None
        candidate_seed_ips: list[str] = [seed_ip] if seed_ip else []
        if not candidate_seed_ips:
            name_key = normalize_hostname(self.seed_host)
            override = self.config.device_overrides.get(name_key)
            if override and override.get("host"):
                candidate_seed_ips = extract_ipv4(override["host"])
            elif name_key in self.config.name_to_ip:
                candidate_seed_ips = extract_ipv4(self.config.name_to_ip[name_key])
            else:
                try:
                    resolved = self._resolve_host(self.seed_host)
                    candidate_seed_ips = [resolved]
                except (socket.gaierror, OSError) as exc:
                    raise CrawlError(
                        f"Could not resolve seed host {self.seed_host!r} to verify scope: {exc}"
                    ) from exc

        in_scope_seed_ips = [ip for ip in candidate_seed_ips if self.config.in_scope(ip)]
        if not in_scope_seed_ips:
            raise CrawlError(f"Seed device {self.seed_host} is outside crawl.allowed_subnets")

        initial_host = in_scope_seed_ips[0]
        initial_remaining = tuple(in_scope_seed_ips[1:])

        started = time.monotonic()
        result = CrawlResult(seed=self.seed_host)
        queue: deque[_Target] = deque(
            [
                _Target(
                    initial_host,
                    self.seed_device_type,
                    None,
                    0,
                    remaining_hosts=initial_remaining,
                )
            ]
        )

        while queue:
            target = queue.popleft()
            host_key = target.host.strip().lower()
            if host_key in self._visited_hosts:
                logger.debug("Already visited %s; skipping", target.host)
                continue

            current_node = (
                target.node_id
                if (target.node_id and self.engine.has_node(target.node_id))
                else None
            )
            if not current_node:
                current_node = self.engine.find(ips=[target.host])

            if current_node:
                node_data = self.engine.graph.nodes.get(current_node, {})
                known_ips = {ip.strip().lower() for ip in node_data.get("mgmt_ips", [])}
                if self.engine.status(current_node) in (STATUS_CRAWLED, STATUS_FAILED) or bool(
                    known_ips & self._visited_hosts
                ):
                    logger.debug(
                        "%s (%s) was already handled; skipping",
                        target.host,
                        self.engine.label(current_node),
                    )
                    continue

            limit = self.config.max_devices
            if limit is not None and result.attempted >= limit:
                result.limit_hit = True
                if target.node_id:
                    self.engine.mark_skipped(target.node_id, f"max_devices limit ({limit}) reached")
                continue

            self._visited_hosts.add(host_key)
            result.attempted += 1
            logger.info(
                "[%d] Crawling %s (%s) depth=%d queue=%d",
                result.attempted,
                target.host,
                target.device_type,
                target.depth,
                len(queue),
            )
            self._crawl_one(target, queue, result)

        result.duration = time.monotonic() - started
        result.bundled_links = self.engine.annotate_bundles()
        result.warned_links = self.engine.annotate_links(self.config.link_checks)
        if result.crawled == 0:
            reason = result.failures[0][1] if result.failures else "no device was crawled"
            raise CrawlError(f"Could not crawl the seed device {self.seed_host}: {reason}")
        return result

    # ------------------------------------------------------------ one device
    def _crawl_one(self, target: _Target, queue: deque[_Target], result: CrawlResult) -> None:
        while True:
            try:
                raw = self.poller.poll(target.host, target.device_type)
                if self.raw_sink:
                    self.raw_sink(raw)
                parsed = self.parser.parse(raw)
                break
            except MapperError as exc:
                remaining = list(target.remaining_hosts)
                next_host = None
                while remaining:
                    candidate = remaining.pop(0)
                    cand_key = candidate.strip().lower()
                    if cand_key not in self._visited_hosts:
                        next_host = candidate
                        break
                if next_host is not None:
                    limit = self.config.max_devices
                    if limit is not None and result.attempted >= limit:
                        result.limit_hit = True
                        if target.node_id:
                            self.engine.mark_skipped(
                                target.node_id, f"max_devices limit ({limit}) reached"
                            )
                        return
                    logger.warning(
                        "Failed to crawl %s (%s): %s; trying alternative in-scope IP %s",
                        target.host,
                        target.device_type,
                        exc,
                        next_host,
                    )
                    target.host = next_host
                    target.remaining_hosts = tuple(remaining)
                    self._visited_hosts.add(next_host.strip().lower())
                    result.attempted += 1
                    logger.info(
                        "[%d] Crawling fallback %s (%s) depth=%d queue=%d",
                        result.attempted,
                        target.host,
                        target.device_type,
                        target.depth,
                        len(queue),
                    )
                    continue
                self._record_failure(target, exc, result)
                return

        host_ips = [target.host] if is_ipv4(target.host) else []
        node_hint = target.node_id
        # The device's own hostname may belong to a node we created under a different LLDP
        # name: it is one device, so fold the two nodes together before going further.
        if node_hint and parsed.hostname:
            known = self.engine.find(name=parsed.hostname)
            if known is not None and known != node_hint:
                logger.info(
                    "%s (%s) is the device already known as %s; merging them",
                    self.engine.label(node_hint),
                    target.host,
                    self.engine.label(known),
                )
                self.engine.merge(keep=known, drop=node_hint)
                node_hint = known
        device_id = self.engine.resolve(name=parsed.hostname, ips=host_ips, node_id=node_hint)
        if self.engine.status(device_id) == STATUS_CRAWLED:
            logger.info(
                "%s is %s, which was already crawled; skipping the duplicate",
                target.host,
                self.engine.label(device_id),
            )
            result.duplicates += 1
            return
        self.engine.mark_crawled(
            device_id,
            host=target.host,
            device_type=parsed.device_type,
            depth=target.depth,
            hostname=parsed.hostname,
        )
        if target.depth == 0:
            self.engine.set_seed(device_id)
        self.engine.set_port_channels(device_id, parsed.port_channels, parsed.lag_note)
        self.engine.set_discovery(
            device_id, parsed.protocols_used, parsed.protocol_notes, parsed.status_note
        )
        link_status: dict[str, dict[str, Any]] = {}
        for neighbor in parsed.neighbors:
            key = normalize_interface(neighbor.local_interface).lower()
            state = parsed.interfaces.get(key)
            if state:
                link_status[key] = {
                    "status": state.status,
                    "speed": state.speed,
                    "duplex": state.duplex,
                    "auto": state.auto,
                }
        self.engine.set_link_status(device_id, link_status)
        result.crawled += 1

        queued_before = len(queue)
        for neighbor in parsed.neighbors:
            self._handle_neighbor(device_id, neighbor, target.depth + 1, queue)
        logger.info(
            "%s: %d LLDP neighbor(s), %d queued",
            self.engine.label(device_id),
            len(parsed.neighbors),
            len(queue) - queued_before,
        )

    def _record_failure(self, target: _Target, exc: MapperError, result: CrawlResult) -> None:
        logger.error("Failed to crawl %s: %s", target.host, exc)
        node_id = target.node_id
        if node_id is None or not self.engine.has_node(node_id):
            found = self.engine.find(ips=[target.host]) if is_ipv4(target.host) else None
            if found:
                node_id = found
            else:
                name = "" if is_ipv4(self.seed_host) else self.seed_host
                ips = [target.host] if is_ipv4(target.host) else []
                node_id = self.engine.resolve(name=name, ips=ips)
        self.engine.mark_failed(
            node_id,
            str(exc),
            host=target.host,
            device_type=target.device_type,
            kind=getattr(exc, "kind", "other"),
        )
        if target.depth == 0:
            self.engine.set_seed(node_id)
        result.failures.append((target.host, str(exc)))

    # -------------------------------------------------------------- neighbors
    def _handle_neighbor(
        self, device_id: str, neighbor: LldpNeighbor, depth: int, queue: deque[_Target]
    ) -> None:
        neighbor_id = self.engine.resolve(
            name=neighbor.neighbor_name,
            ips=neighbor.mgmt_ips,
            chassis_id=neighbor.chassis_id,
            description=neighbor.description,
            capabilities=neighbor.capabilities,
            platform=neighbor.platform,
        )
        if neighbor_id == device_id:
            logger.warning(
                "%s: LLDP neighbor %r resolves to the device itself; ignoring it",
                self.engine.label(device_id),
                neighbor.neighbor_name or neighbor.chassis_id,
            )
            return
        self.engine.add_link(
            device_id,
            neighbor.local_interface,
            neighbor_id,
            neighbor.neighbor_interface,
            protocols=neighbor.protocols,
        )

        if self.engine.status(neighbor_id) in (STATUS_CRAWLED, STATUS_FAILED):
            return
        if neighbor_id in self._queued_nodes:
            return
        decision = self._decide(neighbor, depth)
        if isinstance(decision, _Skip):  # a reason not to crawl it
            if self.engine.mark_skipped(neighbor_id, decision.reason, decision.code):
                logger.info("Not crawling %s: %s", self.engine.label(neighbor_id), decision.reason)
            return
        host, remaining_hosts, device_type = decision
        self.engine.mark_skipped(neighbor_id, "")
        self._queued_nodes.add(neighbor_id)
        queue.append(
            _Target(host, device_type, neighbor_id, depth, remaining_hosts=remaining_hosts)
        )
        logger.debug("Queued %s (%s) at depth %d", host, device_type, depth)

    def _decide(
        self, neighbor: LldpNeighbor, depth: int
    ) -> tuple[str, tuple[str, ...], str] | _Skip:
        """Return ``(host, remaining_hosts, device_type)`` to crawl the neighbor, or the reason
        not to.
        """
        cfg = self.config
        name_key = normalize_hostname(neighbor.neighbor_name)
        name = neighbor.neighbor_name or neighbor.chassis_id

        if name and any(p.search(name) for p in cfg.exclude_hostnames):
            return _Skip("excluded", "matches crawl.exclude_hostnames")

        override = cfg.device_overrides.get(name_key) or next(
            (cfg.device_overrides[ip] for ip in neighbor.mgmt_ips if ip in cfg.device_overrides),
            {},
        )

        known_caps = neighbor.capabilities & KNOWN_CAPABILITIES
        if (
            not override
            and cfg.require_capabilities
            and known_caps
            and not known_caps & cfg.require_capabilities
        ):
            return _Skip(
                "capabilities",
                f"capabilities ({', '.join(sorted(known_caps))}) do not include any of "
                f"{', '.join(sorted(cfg.require_capabilities))}",
            )

        candidates = (
            extract_ipv4(override["host"]) if override.get("host") else list(neighbor.mgmt_ips)
        )
        if not candidates and name_key in cfg.name_to_ip:
            candidates = extract_ipv4(cfg.name_to_ip[name_key])
        if not candidates:
            return _Skip(
                "no_ip", "no management IP advertised (add it under crawl.name_to_ip to crawl it)"
            )
        in_scope = [ip for ip in dict.fromkeys(candidates) if cfg.in_scope(ip)]
        if not in_scope:
            return _Skip(
                "out_of_scope", f"management IP {candidates[0]} is outside crawl.allowed_subnets"
            )
        host = in_scope[0]
        remaining_hosts = tuple(in_scope[1:])

        if cfg.max_depth is not None and depth > cfg.max_depth:
            return _Skip("max_depth", f"crawl.max_depth ({cfg.max_depth}) reached")

        device_type = (
            override.get("device_type")
            or guess_device_type(
                f"{neighbor.description} {neighbor.platform}", cfg.device_type_rules
            )
            or cfg.default_device_type
            or self.seed_device_type
        )
        if not is_supported(device_type):
            return _Skip(
                "no_profile",
                f"no LLDP profile for device_type '{device_type}' "
                "(add one under custom_vendors, or set crawl.device_overrides)",
            )
        return host, remaining_hosts, device_type
