"""
==============================================================================
Module Name:   crawler.py
Description:   Breadth-first LLDP crawl: queue of addresses to visit, visited
               set, scope guard (allowed subnets, depth, device limit, exclusions,
               capabilities) and per-device failure handling. One or several seed
               devices share one queue, one visited set and one alias table, so
               disconnected islands are merged into a single topology and no
               device is polled twice.
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
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from src import MapperError, extract_ipv4, is_ipv4, normalize_hostname, normalize_interface
from src.config import ConfigError, as_int, section
from src.graph_engine import STATUS_CRAWLED, STATUS_DISCOVERED, STATUS_FAILED, GraphEngine
from src.linkcheck import LinkCheckConfig
from src.models import (
    REASON_AUTH_FAILURE,
    REASON_CONNECT_FAILURE,
    REASON_CRAWL_ERROR,
    REASON_EXCLUDED,
    REASON_LIMIT_REACHED,
    REASON_NO_MGMT_IP,
    REASON_OUT_OF_SCOPE,
    REASON_TIMEOUT,
    REASON_UNSUPPORTED_OS,
    BlindSpot,
)
from src.parser import ENDPOINT_LABELS, BaseParser
from src.poller import BasePoller, RawLldp
from src.vendors import guess_device_type, is_supported
from src.vendors.base import KNOWN_CAPABILITIES, LldpNeighbor, normalize_capabilities

logger = logging.getLogger(__name__)

__all__ = [
    "SEED_CRAWLED",
    "SEED_DUPLICATE",
    "SEED_FAILED",
    "SEED_OUT_OF_SCOPE",
    "SEED_SKIPPED",
    "CrawlConfig",
    "CrawlError",
    "CrawlResult",
    "Crawler",
    "SeedResult",
]

# What became of one seed device (``SeedResult.status``).
SEED_CRAWLED = "crawled"  # logged in to it and read its neighbors
SEED_FAILED = "failed"  # it could not be crawled (or its name did not resolve)
SEED_OUT_OF_SCOPE = "out_of_scope"  # outside crawl.allowed_subnets: never contacted
SEED_DUPLICATE = "duplicate"  # the same device as an earlier seed (another address of it)
SEED_SKIPPED = "skipped"  # crawl.max_devices was used up before its turn


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
    collect_vlan_info: bool = True  # one extra command per device (VLAN consistency checks)
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
            collect_vlan_info=bool(crawl.get("collect_vlan_info", True)),
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
    """Why a neighbor is not crawled: a short machine-readable code and the readable reason.

    ``endpoint`` is the leaf-endpoint class (``endpoint_phone``, ...) when the neighbor is not
    crawled because it is a leaf; empty for every other reason.
    """

    code: str
    reason: str
    endpoint: str = ""


#: Not-crawled reason codes (``skip_code``) that leave a branch unmapped, and what they are called
#: in the blind-spot report. ``capabilities`` (a leaf endpoint) is deliberately absent.
_SKIP_TO_BLIND_SPOT = {
    "out_of_scope": REASON_OUT_OF_SCOPE,
    "no_ip": REASON_NO_MGMT_IP,
    "no_profile": REASON_UNSUPPORTED_OS,
    "excluded": REASON_EXCLUDED,
    "max_depth": REASON_LIMIT_REACHED,
    "max_devices": REASON_LIMIT_REACHED,
}
#: The same for failed crawls (``error_kind``); kinds not listed become ``CRAWL_ERROR``.
_FAILURE_TO_BLIND_SPOT = {
    "auth": REASON_AUTH_FAILURE,
    "timeout": REASON_TIMEOUT,
    "connect": REASON_CONNECT_FAILURE,
    "unsupported": REASON_UNSUPPORTED_OS,
}


@dataclass
class _Target:
    """One entry of the crawl queue."""

    host: str
    device_type: str
    node_id: str | None
    depth: int
    remaining_hosts: tuple[str, ...] = ()
    origin: str = ""  # the seed (as given) this target descends from


@dataclass
class SeedResult:
    """What became of one seed device of the crawl."""

    host: str  # as given (an IP or a DNS name)
    device_type: str
    status: str = SEED_SKIPPED  # one of the SEED_* constants
    detail: str = ""  # why it was not crawled, or the error
    node_id: str = ""  # its node in the topology graph ("" when it never got one)
    crawled: int = 0  # devices crawled that this seed led to (itself included)
    discovered: int = 0  # devices of any status that this seed led to (itself included)


@dataclass
class CrawlResult:
    """What the crawl did.

    ``seed`` is the first seed; ``seeds`` has one ``SeedResult`` per seed given (in order),
    including the ones that were skipped or failed.
    """

    seed: str
    attempted: int = 0
    crawled: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)  # (host, error)
    duplicates: int = 0
    bundled_links: int = 0  # links that belong to a port-channel
    warned_links: int = 0  # links with at least one link-check warning
    limit_hit: bool = False
    duration: float = 0.0
    blind_spots: list[BlindSpot] = field(default_factory=list)  # uncrawled infrastructure
    seeds: list[SeedResult] = field(default_factory=list)

    @property
    def seed_hosts(self) -> list[str]:
        """Every seed as given (just ``[seed]`` when the per-seed records are not filled)."""
        return [item.host for item in self.seeds] or [self.seed]

    @property
    def partial(self) -> bool:
        """True if some devices failed or the device limit cut the crawl short."""
        return bool(self.failures) or self.limit_hit


class Crawler:
    """Walk the network from one or several seed devices and fill a ``GraphEngine``.

    All seeds start in the same queue (depth 0), so they are served before any deeper device, and
    share one visited set and one alias table: a device reachable from several seeds is polled
    once, and seeds in disconnected islands are crawled until each island is exhausted, all into
    the same graph. ``max_depth`` counts hops from the nearest seed, ``max_devices`` is shared by
    all seeds. Every device remembers (``seed_origin``) the first seed that led to it; on a tie
    the seed listed first wins. The crawl is sequential, one SSH session at a time.
    """

    def __init__(
        self,
        poller: BasePoller,
        parser: BaseParser,
        engine: GraphEngine,
        config: CrawlConfig,
        seed_host: str,
        seed_device_type: str,
        raw_sink: Callable[[RawLldp], None] | None = None,
        extra_seeds: Sequence[tuple[str, str]] = (),
    ) -> None:
        """
        Args:
            seed_host: The first seed (an IP or DNS name); ``seed_device_type`` is its Netmiko
                device type.
            extra_seeds: More seeds as ``(host, device_type)`` pairs (multi-seed crawl). A seed
                repeated (compared case-insensitively) is crawled once.
        """
        self.poller = poller
        self.parser = parser
        self.engine = engine
        self.config = config
        self.seed_host = seed_host
        self.seed_device_type = seed_device_type
        self.raw_sink = raw_sink
        unique: dict[str, tuple[str, str]] = {}
        for host, device_type in [(seed_host, seed_device_type), *extra_seeds]:
            unique.setdefault(str(host).strip().lower(), (str(host).strip(), device_type))
        self.seeds: list[tuple[str, str]] = list(unique.values())
        self._visited_hosts: set[str] = set()
        self._queued_nodes: set[str] = set()
        self._dns_cache: dict[str, str] = {}
        self._seed_nodes: dict[str, str] = {}  # seed as given -> its node

    # --------------------------------------------------------------- DNS
    def _resolve_host(self, host: str) -> str:
        """Resolve a DNS name to one IPv4 address, remembered for the rest of this crawl.

        ``getaddrinfo`` restricted to ``AF_INET`` instead of ``gethostbyname``: the result is
        filtered to IPv4 socket addresses explicitly, so a dual-stack name can never hand back
        an IPv6 literal (the crawler, the scope check and SSH all work on IPv4 only).

        Raises:
            socket.gaierror: If the name has no IPv4 address (or does not resolve at all).
        """
        host = host.strip()
        if host in self._dns_cache:
            return self._dns_cache[host]
        for family, _type, _proto, _canonname, sockaddr in socket.getaddrinfo(
            host, None, family=socket.AF_INET
        ):
            if family == socket.AF_INET:
                self._dns_cache[host] = str(sockaddr[0])
                return self._dns_cache[host]
        raise socket.gaierror(getattr(socket, "EAI_NONAME", -2), f"no IPv4 address for {host!r}")

    # ------------------------------------------------------------------ seeds
    def _seed_target(self, host: str, device_type: str) -> _Target:
        """The queue entry for one seed, after checking it against ``crawl.allowed_subnets``.

        A seed given by name is resolved (``device_overrides`` / ``name_to_ip`` first, then DNS)
        so its address can be checked before anything is contacted.

        Raises:
            CrawlError: If the seed (every address it has) is outside the allowed subnets, or
                its name does not resolve to an IPv4 address.
        """
        seed_ip = host.strip() if is_ipv4(host) else None
        candidate_ips: list[str] = [seed_ip] if seed_ip else []
        name_key = normalize_hostname(host)
        override = self.config.device_overrides.get(name_key) or {}
        if not candidate_ips:
            if override.get("host"):
                candidate_ips = extract_ipv4(override["host"])
            elif name_key in self.config.name_to_ip:
                candidate_ips = extract_ipv4(self.config.name_to_ip[name_key])
            else:
                try:
                    candidate_ips = [self._resolve_host(host)]
                except (socket.gaierror, OSError) as exc:
                    raise CrawlError(
                        f"Could not resolve seed host {host!r} to verify scope: {exc}"
                    ) from exc

        in_scope_ips = [ip for ip in candidate_ips if self.config.in_scope(ip)]
        if not in_scope_ips:
            raise CrawlError(f"Seed device {host} is outside crawl.allowed_subnets")
        return _Target(
            in_scope_ips[0],
            override.get("device_type") or device_type,
            None,
            0,
            remaining_hosts=tuple(in_scope_ips[1:]),
            origin=host,
        )

    def _unusable_seed(
        self, host: str, device_type: str, exc: CrawlError, result: CrawlResult
    ) -> SeedResult:
        """Record a seed that cannot be crawled (multi-seed crawls go on without it).

        An out-of-scope seed becomes an ``OUT_OF_SCOPE`` blind spot (never contacted); a seed
        whose name does not resolve becomes a failed device. Either way the run is partial.
        """
        ips = [host] if is_ipv4(host) else []
        name = "" if ips else host
        node = self.engine.resolve(name=name, ips=ips)
        self.engine.set_seed_origin(node, host)
        if "outside crawl.allowed_subnets" in str(exc):
            self.engine.mark_skipped(node, str(exc), "out_of_scope")
            status = SEED_OUT_OF_SCOPE
        else:
            self.engine.mark_failed(
                node, str(exc), host=host, device_type=device_type, kind="connect"
            )
            status = SEED_FAILED
        logger.warning("Skipping seed %s: %s", host, exc)
        result.failures.append((host, str(exc)))
        return SeedResult(host, device_type, status, str(exc), node_id=node)

    def _finish_seeds(self, result: CrawlResult) -> None:
        """Fill each seed's final status and the number of devices it led to."""
        graph = self.engine.graph
        by_origin: dict[str, list[str]] = {}
        for node_id, attrs in graph.nodes(data=True):
            by_origin.setdefault(attrs.get("seed_origin") or "", []).append(node_id)
        for seed in result.seeds:
            node = self._seed_nodes.get(seed.host) or seed.node_id
            if node and self.engine.has_node(node):
                seed.node_id = node
                attrs = graph.nodes[node]
                if attrs["status"] == STATUS_CRAWLED:
                    owner = attrs.get("seed_origin") or seed.host
                    if owner != seed.host:  # another address of a device an earlier seed crawled
                        seed.status = SEED_DUPLICATE
                        seed.detail = f"the same device as seed {owner}"
                    else:
                        seed.status, seed.detail = SEED_CRAWLED, ""
                elif attrs["status"] == STATUS_FAILED:
                    seed.status, seed.detail = SEED_FAILED, attrs["error"]
                # anything else keeps the status it got when it was set aside (out of scope ...)
            members = by_origin.get(seed.host, [])
            seed.discovered = len(members)
            seed.crawled = sum(1 for n in members if graph.nodes[n]["status"] == STATUS_CRAWLED)

    # ------------------------------------------------------------------ run
    def run(self) -> CrawlResult:
        """Crawl until the queue is empty.

        Per-device failures (timeout, authentication, parse errors) are logged, recorded as
        ``failed`` nodes and do not stop the crawl. With several seeds, a seed that is outside
        ``crawl.allowed_subnets`` (or does not resolve) is skipped and reported (out-of-scope
        blind spot, partial result) while the others are crawled.

        Raises:
            CrawlError: If the (only) seed device is outside allowed subnets, if no seed is
                usable, or if no device could be crawled.
        """
        started = time.monotonic()
        result = CrawlResult(seed=self.seed_host)
        queue: deque[_Target] = deque()
        for host, device_type in self.seeds:
            try:
                target = self._seed_target(host, device_type)
            except CrawlError as exc:
                if len(self.seeds) == 1:
                    raise
                result.seeds.append(self._unusable_seed(host, device_type, exc, result))
                continue
            result.seeds.append(SeedResult(host, target.device_type))
            queue.append(target)
        if not queue:
            raise CrawlError(
                "No seed device can be crawled: "
                + "; ".join(f"{host}: {err}" for host, err in result.failures)
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
                    if target.depth == 0:  # another seed (or an address of it) got there first
                        self._seed_nodes.setdefault(target.origin, current_node)
                    continue

            limit = self.config.max_devices
            if limit is not None and result.attempted >= limit:
                result.limit_hit = True
                if target.node_id:
                    self.engine.mark_skipped(
                        target.node_id, f"max_devices limit ({limit}) reached", "max_devices"
                    )
                elif target.depth == 0:
                    for seed in result.seeds:
                        if seed.host == target.origin:
                            seed.detail = f"crawl.max_devices ({limit}) was used up first"
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
        result.blind_spots = self._collect_blind_spots()
        self._finish_seeds(result)
        if result.crawled == 0:
            reason = result.failures[0][1] if result.failures else "no device was crawled"
            if len(self.seeds) == 1:
                raise CrawlError(f"Could not crawl the seed device {self.seed_host}: {reason}")
            names = ", ".join(host for host, _type in self.seeds)
            raise CrawlError(f"Could not crawl any seed device ({names}): {reason}")
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
                                target.node_id,
                                f"max_devices limit ({limit}) reached",
                                "max_devices",
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
        if target.depth == 0:
            self._seed_nodes[target.origin] = device_id
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
            self.engine.set_seed(device_id, origin=target.origin)
        else:
            self.engine.set_seed_origin(device_id, target.origin)
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
        vlan_ports = {
            key: parsed.vlan_ports[key]
            for key in (
                [normalize_interface(n.local_interface).lower() for n in parsed.neighbors]
                + [normalize_interface(pc.name).lower() for pc in parsed.port_channels]
            )
            if key in parsed.vlan_ports
        }
        self.engine.set_vlan_info(device_id, vlan_ports, parsed.vlan_note)
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
                seed_name = target.origin if target.depth == 0 else self.seed_host
                name = "" if is_ipv4(seed_name) else seed_name
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
            self.engine.set_seed(node_id, origin=target.origin)
            self._seed_nodes[target.origin] = node_id
        else:
            self.engine.set_seed_origin(node_id, target.origin)
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
        origin = self.engine.graph.nodes[device_id]["seed_origin"]
        self.engine.set_seed_origin(neighbor_id, origin)  # the first seed to reach it keeps it

        if self.engine.status(neighbor_id) in (STATUS_CRAWLED, STATUS_FAILED):
            return
        if neighbor_id in self._queued_nodes:
            return
        decision = self._decide(
            neighbor, depth, self.engine.graph.nodes[neighbor_id]["endpoint_class"]
        )
        if isinstance(decision, _Skip):  # a reason not to crawl it
            if decision.endpoint:
                self.engine.set_endpoint_class(neighbor_id, decision.endpoint)
            if self.engine.mark_skipped(neighbor_id, decision.reason, decision.code):
                logger.info("Not crawling %s: %s", self.engine.label(neighbor_id), decision.reason)
            return
        host, remaining_hosts, device_type = decision
        self.engine.mark_skipped(neighbor_id, "")
        self._queued_nodes.add(neighbor_id)
        queue.append(
            _Target(
                host,
                device_type,
                neighbor_id,
                depth,
                remaining_hosts=remaining_hosts,
                origin=origin,
            )
        )
        logger.debug("Queued %s (%s) at depth %d", host, device_type, depth)

    def _decide(
        self, neighbor: LldpNeighbor, depth: int, known_endpoint: str = ""
    ) -> tuple[str, tuple[str, ...], str] | _Skip:
        """Return ``(host, remaining_hosts, device_type)`` to crawl the neighbor, or the reason
        not to.

        ``known_endpoint`` is the leaf class an earlier report from another switch gave this
        device; a leaf verdict is never overturned by a later, different report.
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
                neighbor.endpoint_class,
            )

        # Leaf Guard. The check above catches neighbors that advertise only end-device
        # capabilities; this one catches a leaf that also claims bridge or router (an IP phone
        # with a PC port, an access point) or that advertises nothing but names itself. Same
        # opt-outs as the check above (``require_capabilities: []``, a ``device_overrides``
        # entry); without them a leaf is never queued, so it is never logged in to.
        endpoint = neighbor.endpoint_class or known_endpoint
        if endpoint and not override and cfg.require_capabilities:
            if not neighbor.endpoint_class:
                evidence = "another switch's report"
            else:
                evidence = ", ".join(sorted(known_caps)) if known_caps else "platform / description"
            return _Skip(
                "capabilities",
                f"leaf endpoint ({ENDPOINT_LABELS[endpoint]}, from {evidence}): never logged in to",
                endpoint,
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

    # ------------------------------------------------------------ blind spots
    def _collect_blind_spots(self) -> list[BlindSpot]:
        """Every infrastructure neighbor that was seen but not crawled, with who saw it.

        Built from the final graph rather than recorded while crawling, so a device that was
        skipped from one switch but crawled via another is correctly not a blind spot. Leaf
        endpoints never appear: they were skipped as ``capabilities``, which is not a blind spot
        (a phone has no downstream branch to miss).
        """
        graph = self.engine.graph
        spots: list[BlindSpot] = []
        for node_id, attrs in graph.nodes(data=True):
            if attrs["status"] == STATUS_FAILED:
                reason = _FAILURE_TO_BLIND_SPOT.get(attrs["error_kind"], REASON_CRAWL_ERROR)
                detail = attrs["error"]
            elif attrs["status"] == STATUS_DISCOVERED and attrs["skip_code"] in _SKIP_TO_BLIND_SPOT:
                reason = _SKIP_TO_BLIND_SPOT[attrs["skip_code"]]
                detail = attrs["skip_reason"]
            else:
                continue
            parent, port = self._reporting_switch(node_id)
            ips = attrs.get("mgmt_ips") or []
            spots.append(
                BlindSpot(
                    hostname=self.engine.label(node_id),
                    chassis_id=attrs.get("chassis_id") or "",
                    management_ip=attrs.get("address") or (ips[0] if ips else None),
                    discovered_via=self.engine.label(parent) if parent else "",
                    parent_port=port,
                    reason=reason,
                    detail=detail,
                    node_id=node_id,
                    parent_id=parent or "",
                )
            )
        spots.sort(key=lambda b: (b.discovered_via.lower(), b.parent_port, b.hostname.lower()))
        return spots

    def _reporting_switch(self, node_id: str) -> tuple[str | None, str]:
        """The crawled neighbor closest to the seed and its port on the link to ``node_id``."""
        graph = self.engine.graph
        best: tuple[tuple[int, str, str], str, str] | None = None
        for _u, other, data in graph.edges(node_id, data=True):
            attrs = graph.nodes[other]
            if attrs["status"] != STATUS_CRAWLED:
                continue
            port = data["ports"].get(other, "")
            key = (attrs["depth"] or 0, self.engine.label(other).lower(), port)
            if best is None or key < best[0]:
                best = (key, other, port)
        return (best[1], best[2]) if best else (None, "")
