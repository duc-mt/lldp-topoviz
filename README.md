# LLDP TopoViz

Crawl a network over LLDP and CDP from one seed device and draw it as an interactive HTML diagram.

The tool SSHes into the seed, runs the platform's `show lldp neighbors detail` equivalent, parses it with
Netmiko's built-in TextFSM support (`use_textfsm=True`), queues every neighbor it is allowed to log in to,
and repeats until the network is mapped. The result is a NetworkX graph, a JSON snapshot a single
self-contained HTML file (Pyvis / vis-network) that opens offline, and a PNG picture of the same topology
(Graphviz) for reports and tickets.

Structure, CLI, logging, error handling and config management deliberately mirror `ospf-lsdb-monitor`.

## Features

- Breadth-first crawl with a queue and a `visited` set: no device is logged in to twice, loops are harmless.
- **Scope guard**: the crawler only contacts addresses inside `crawl.allowed_subnets` (mandatory), within
  `max_depth` hops and `max_devices` sessions. It sends your credentials nowhere else.
- **Several seeds** (`--seeds A,B,...` or `crawl.seeds`): networks in disconnected islands or several data centers
  are crawled into one topology. All seeds share one queue, one visited set and one alias table, so a device
  reachable from two seeds is polled once; each device records which seed led to it (`seed_origin`). A seed outside
  `allowed_subnets` is never contacted: it is skipped and reported as an out-of-scope blind spot.
- Per-device failures (timeout, bad credentials, rejected command, parse mismatch) never stop the crawl. Each one
  is classified (`SSH auth failed`, `SSH timeout`, ...), written under the red node, listed in a **Problems**
  panel with a one-line fix hint, and explained in full when you click the node.
- One node per physical device: names, management IPs and chassis IDs are aliases of each other, so
  `SW1.corp.local`, `sw1` and `0c4d.e900.0a01` are the same box.
- **Hierarchical layout**: Edge, Core, Distribution, Access and End devices in rows, with the tier named at the left.
- **Device icons** by type (switch, router, firewall, AP, phone, endpoint), coloured by crawl status.
- A small line under each name with the management IP and OS (`10.0.1.1 · NX-OS 9.3(8)`).
- **Port-channels**: member links of a LAG are merged into one thicker link labelled `Po1 ... Po10` and
  `LACP x2`, with the members and their states in the details panel.
- **Link checks**: each device is asked what its ports really run at, and links get a warning triangle when
  a Gigabit port runs at 100M, the two ends disagree on speed or duplex, a port is half duplex, or the members of a
  port-channel run at different speeds.
- **VLAN checks**: each switch is asked for the mode, native VLAN and allowed VLANs of its ports (Cisco IOS, IOS-XE
  and NX-OS), and the two ends of every link are compared. A trunk against an access port and a native VLAN mismatch
  are `mismatch`; allowed-VLAN lists that differ are `degraded`. They carry a `⚠ [VLAN]` tag on the diagram and in
  the picture, and the link panel shows the mode, native VLAN and allowed VLANs of both ends. A bundle is judged
  once, on its port-channels.
- **Crawl history**: every crawl is recorded in `data/history/history.db` with a diff against the one before
  (devices and links added or removed, cables moved to another port, speed changes, new blind spots). List it with
  `--history`; served by `--serve`, the page gets a scrubber that steps back through the recorded crawls.
- **Web server mode** (`--serve`): the explorer, a JSON API and an on-demand crawl, with the standard library only.
  See [Web server mode](#web-server-mode).
- **Several discovery protocols**: LLDP and Cisco CDP are read in the same session and merged. CDP finds neighbors
  that LLDP does not see (phones, older switches) and keeps a device crawlable when its LLDP is off or unsupported.
  Links seen only through CDP are drawn dashed.
- Link colour and thickness by the speed the ports run at where a device reports it, otherwise by the port type in
  the interface name (`Gi` 1G, `Te` / `xe-` 10G, ...).
- An **interactive legend**: click an item (a status, device type, tier, speed, port-channel, CDP-only or a warning
  class) to highlight it and dim the rest. It collapses, and lists every warning and problem with a jump link.
- Interface names are written along every link at both ends. Hover a device or click a device or link for details.
- Neighbor device type is guessed from the system description or CDP platform (NX-OS, IOS-XR, IOS/XE, EOS, AOS-CX, VRP),
  with rules and per-device overrides in `settings.yaml`.
- Offline replay (`--replay`) and capture (`--save-raw`) of the raw CLI output, for tests and demos.
- **Telegram alerting & daemon scheduling**: Real-time dispatch of crawl completions, partial warnings, and crash events with concurrency-safe scheduling. See [docs/TELEGRAM_ALERTING.md](docs/TELEGRAM_ALERTING.md).

## Standalone Executable (Zero-Dependency & Air-Gapped)

No Python, pip, or virtual environment is required on the target machine:

1. Download the standalone executable from [GitHub Releases](https://github.com/duc-mt/lldp-topoviz/releases) under **Latest Build**:
   - `lldp-topoviz-windows-amd64.exe` (Windows 64-bit)
   - `lldp-topoviz-linux-amd64` (Linux 64-bit)
2. Place the single binary in an empty directory on the target machine.
3. Generate template configuration:
   ```cmd
   lldp-topoviz-windows-amd64.exe --init   :: (or ./lldp-topoviz-linux-amd64 --init)
   ```
4. Edit the generated `settings.yaml` with your seed router IP, credentials, and allowed subnets.
5. Run the crawl:
   ```cmd
   lldp-topoviz-windows-amd64.exe          :: (or ./lldp-topoviz-linux-amd64)
   ```

## Quick start (Source / Developer)

The easiest way to run is via the universal launcher script (`./run` on Linux/macOS or `.\scripts\run.bat` on native Windows CMD), which automatically initializes `.venv`, installs locked dependencies, and creates `config/settings.yaml` from template on first run:

```bash
# Try the full multi-vendor demo (6 devices across Cisco, Arista, Huawei, Aruba):
./run --config samples/demo/settings.yaml --replay samples/demo

# Or replay specific vendor samples:
./run --config samples/cisco_ios/settings.yaml --replay samples/cisco_ios
./run --config samples/cisco_nxos/settings.yaml --replay samples/cisco_nxos
./run --config samples/arista_eos/settings.yaml --replay samples/arista_eos
./run --config samples/huawei/settings.yaml --replay samples/huawei
./run --config samples/aruba_aoscx/settings.yaml --replay samples/aruba_aoscx
./run --config samples/vyos/settings.yaml --replay samples/vyos
./run --config samples/juniper_junos/settings.yaml --replay samples/juniper_junos
./run --config samples/hp_procurve/settings.yaml --replay samples/hp_procurve

open output/topology.html          # the interactive page (or xdg-open / start)
open output/topology.png           # the same topology as a still picture

# Real network:
export LLDP_MAPPER_USERNAME=admin
export LLDP_MAPPER_PASSWORD='...'
./run --seed 10.0.0.1 --device-type cisco_ios --allow 10.0.0.0/16
```

You can also run directly with Python:
```bash
python3 -m venv .venv && source .venv/bin/activate      # Python 3.10+
pip install -r requirements.lock
python3 main.py --config samples/demo/settings.yaml --replay samples/demo
```

Put your own values in `config/settings.yaml` (seed, `crawl.allowed_subnets`, ...). Leave `device.username` and
`device.password` empty there: the `LLDP_MAPPER_*` environment variables always override the file, so the
password never has to be written to disk.

## How it works

```
seed -> Crawler queue -> DevicePoller (Netmiko, use_textfsm=True) -> LldpParser (normalise)
          ^                                                              |
          +--- enqueue in-scope neighbours <------- Crawler <------------+
                                                        |
                                              GraphEngine (NetworkX MultiGraph)
                                                        |
                                       data/current_state.json  +  Pyvis -> output/topology.html
                                                          +  Graphviz -> output/topology.png
```

| File | Role |
|---|---|
| `main.py` | CLI, pipeline orchestration, summary, exit codes |
| `src/config.py` | YAML + env-var settings, seed and SSH connection settings |
| `src/poller.py` | `DevicePoller` (SSH), `FilePoller` (replay), `RawCapture` (`--save-raw`) |
| `src/textparse.py` | TextFSM runner that tells "no records" from "wrong output" (Netmiko cannot) |
| `src/linkcheck.py` | Link health checks: speed / duplex mismatch, half duplex, port below its type, bundle speed mix |
| `src/parser.py` | TextFSM rows -> merged neighbors (LLDP + CDP), `PortChannel` and `InterfaceStatus` records |
| `src/vendors/` | Per-platform profile: discovery protocols, port-channel and interface-status commands, templates, column maps |
| `src/crawler.py` | Queue, visited set, scope guard, failure handling |
| `src/graph_engine.py` | Device identity, link de-duplication, status, JSON snapshot |
| `src/enrichment.py` | Device type, OS label, tiers, nominal link speed, failure wording (pure functions) |
| `src/icons.py` | Inline SVG icons per device type |
| `src/visualizer.py` | Pyvis HTML: icons, hierarchy, bundles, legend, tooltips; `render_image` for the picture |
| `src/diagram_image.py` | The Graphviz picture (PNG / SVG / PDF): tiers, speeds, port-channels, findings, endpoint groups |
| `src/diagram_assets.py` | The CSS and JavaScript injected into the page (labels, panels) |
| `templates/` | Your own TextFSM templates (see "Adding a platform") |
| `samples/demo/` | Replayable captures for the demo and the tests |

### Reading the diagram

| Look | Meaning |
|---|---|
| Icon | Device type: switch (square, arrows), router (circle, arrows), firewall (brick wall), AP (waves), phone, endpoint, `?` unknown |
| Green icon | Logged in, LLDP table read |
| Red icon | Tried and failed. A red line under the name says why (`SSH auth failed`, ...) |
| Grey icon | Seen as a neighbor only. A grey line says why (`outside scope`, `no mgmt IP`, `end device`, ...) |
| Gold ring | The seed device |
| Rows with a name at the left | Hierarchy tier: Edge, Core, Distribution, Access, End devices (only rows in use are drawn) |
| Line under the name | `management IP · OS version` |
| Text along a link | The interface on the node at that end (short form: `Gi1/0/1`, `Eth1/1`); for a port-channel, the bundle name (`Po1`). Shown above zoom 1.20 and on the links of the device you hover or select; *Filter > Always show interface names* keeps them on |
| Thick link labelled `LACP x2` / `static x2` | A port-channel with 2 member links (`x1/2`: only one of two members is up) |
| Link colour and thickness | Speed: 100M light grey, 1G slate, 10G blue, 25G teal, 40G purple, 100G magenta, `?` pale grey. It is what the ports run at where a device reports it, otherwise the port type from the name |
| Dashed link | Seen only through CDP (LLDP did not report it) |
| Orange triangle with a glow | Degraded link: it works but below what its ports can do (a Gigabit port at 100M, half duplex) |
| Red triangle with a glow | Mismatch: the two ends disagree (speed or duplex), or port-channel members run at different speeds |

**Hover** a device for a summary including its full platform description. **Click** a device to open the details
panel: type, tier, OS, address, management IPs, device type, chassis ID, capabilities, the platform description
(as advertised over LLDP and captured by the TextFSM template; the Cisco IOS template keeps only its first
line), the failure and what to do about it, its port-channels with member states, and every link with both
interface names. Neighbor names in the panel are buttons that jump to that device. **Click a link** for its
interfaces with what each end runs at (`1G full`), the warnings, which protocols saw it and, for a bundle,
protocol and members. The **Warnings** and **Problems** lists in the legend jump to each link or failed device.
The legend collapses with its `-` button; click one of its items to highlight what it describes and click again to
clear. `Esc` or a click on empty space closes the panel.

#### The picture (PNG)

Every run also writes `output/topology.png`: the same topology as a still picture, drawn by Graphviz for a report,
a ticket or a wiki. Core is on top with one labelled row per tier; devices are coloured by crawl status (the seed
has a gold ring); links keep their speed colour and thickness, port-channels read `LACP x2`, a link with a finding
carries `▲ degraded` / `▲ mismatch`, interface names sit at both ends, and phones, access points and hosts fold
into one badge per switch like on the page. `--output topology.svg` or `.pdf` gives those formats instead.

Graphviz is a **system program** (`apt install graphviz`, `brew install graphviz`) besides the `graphviz` Python
package. Without it a default run still writes the interactive page and says how to get the picture (the DOT source is
kept as `topology.gv`); asking for a picture by name with `--output` makes the missing program an error. The
standalone executables cannot carry Graphviz: on a machine without it they write the page only. The picture shows the
current topology; the snapshot comparison is a feature of the page.

#### Explorer shell and operations features

The page has a top bar, a sidebar (summary, the legend as collapsible groups) and a tabbed inspector; dark and light
themes follow the system and are kept in the browser (`lldp-explorer:theme`).

| What | How |
|---|---|
| Search | `/` or *Search*: name, IP, port, type, OS; the result list ranks the device's own name first; `Enter` flies to it (`network.focus`) and opens it |
| Inspector | Click a device or link: **Overview**, **Interfaces & Peers** (filterable), **CLI Diagnostics** with copy-ready read-only commands for Cisco IOS / NX-OS, Arista EOS, Junos, VyOS and Huawei. Each end of a link uses its own device's platform unless you pick one; check the syntax against your software version |
| Zoom levels | Below 0.50: names only, backbone links (Core/Distribution) stay, the rest fade. 0.50-1.20: name, `management IP`, badge. Above 1.20: interface names |
| Focus mode | Hover or select a device: its 1-hop and 2-hop neighbourhood stays lit (2-hop softer), the rest fades to 15 %. *Filter* turns it off |
| Minimap | The 160 x 100 radar at the bottom right: click or drag to move the view |
| Simulate | `S`: click a device or cable. Besides failure there is **Drain for maintenance** (planned removal, shown amber). The blast radius: **red** lost every path to the Core, **amber** lost redundancy (one uplink left, or fewer uplinks), **blue** still connected but more hops from the Core, with the new path drawn as moving dashes; rings spread from what was taken out |
| Snapshot | With a previous run (`main.py` reads `current_state.json` before it is replaced) the bottom bar offers **Current State**, **Previous State** and **Diff Overlay**: added green, removed red and dashed, changed orange. It covers devices, cables, port-channel member counts and a port reporting down; a change of speed is not diffed |
| Documentation | *Export > Export Documentation Bundle*: a clean vector SVG (light, no controls, any resolution) and `TOPOLOGY_SPEC.md` (device table; link matrix with interfaces, speed and redundancy). Software version is the OS and version found in the platform description |
| Keys | `/` search, `F` fit, `S` simulate, `Esc` deselect |

#### How the diagram decides things

LLDP does not say what role a device plays, and it has no "firewall" capability, so a few things are inferred.
Every inference can be overridden in `visualization:` in `settings.yaml`.

- **Tier**: by hostname regex. Defaults: `core*`, `dist*` / `agg*`, `acc*` / `sw<N>` / `leaf`, and `isp*` / `wan*` for
  Edge. Firewalls go to Edge, APs, phones and endpoints to End devices. A device that matches nothing is placed
  one row below its highest placed neighbor. If no name matches anywhere, the seed (or `visualization.root`) is
  Core and the rest is arranged by hops from it. Replace a tier's patterns with `visualization.tiers`.
- **Icon**: your `visualization.node_types` regexes first, then firewall keywords in the description or hostname
  (pfSense, FortiGate, Check Point, ASA, ... or `fw` in the name), router hints, then LLDP capabilities: WLAN is an AP,
  telephone is a phone, Bridge is a switch (an L3 switch advertising Bridge and Router is drawn as a switch), Router
  alone is a router. The Arista template has no capabilities, so EOS devices fall back to their platform.
- **Link speed**: what the ports run at when a device reports it (the slower of the two ends), otherwise the port
  *type* from the interface name: `Gi`, `GigabitEthernet`, `GE` and `ge-` are 1G; `Te`, `xe-`, `XGigabitEthernet` are
  10G; `Twe` 25G; `Fo` 40G; `Hu` 100G. `Ethernet1/1` (NX-OS, EOS) and `1/1/1` (AOS-CX) say nothing about speed, so
  without interface data such a link is `?`. A bundle shows `2x1G`, or `100M + 1G` when its members differ.
- **Link checks** (`link_checks:` in `settings.yaml`): per link, from the ports that are up. *Mismatch*: different speed
  or different duplex on the two ends; port-channel members at different speeds. *Degraded*: a port running below its
  type (`Gi` at 100M; shown only when the two ends do not already disagree), or half duplex. A port that is down is
  never judged. A Gigabit port at 100M is sometimes intentional (an old device on the other end): list such ports under
  `link_checks.ignore_ports`.
- **CDP and LLDP are merged**: the same neighbor on the same port reported by both becomes one link that remembers both.
  Two neighbors with different names are never merged, whatever their ports say.
- **Port-channels** need a second command per device (below). Without that data, member links are drawn
  individually, never guessed to be a LAG: LLDP alone cannot tell a LAG from independent links.

## Command line

| Option | Meaning |
|---|---|
| `--config PATH` | Settings file (default `config/settings.yaml`) |
| `--seed HOST` / `--device-type TYPE` | Override `seed.host` / `seed.device_type` |
| `--seeds HOST[,HOST...]` | Several seed devices, crawled into one topology (islands, data centers). Takes precedence over `--seed` and over `seed.host` / `crawl.seeds`; all seeds use `--device-type` |
| `--allow CIDR` | Add an allowed subnet (repeatable) |
| `--max-depth N` / `--max-devices N` | Override the crawl limits (`--max-depth 0` = seed only) |
| `--protocols LIST` | Discovery protocols to read, comma separated, in priority order (default `lldp,cdp`) |
| `--no-link-checks` | Do not read interface speed / duplex and do not check links (saves a command per device) |
| `--no-vlan-checks` | Do not read switchport / VLAN data and do not compare VLANs (saves a command per device) |
| `--no-port-channels` | Do not read port-channels (saves one command per device; links are not bundled) |
| `--replay DIR` | Read saved CLI output instead of using SSH |
| `--save-raw DIR` | Also store each device's raw CLI output (one extra command per device) |
| `--output PATH` | Where the diagram goes. Default: `output/topology.png` **and** `output/topology.html`. A `.html` path writes the interactive page only; any other extension (`png`, `svg`, `pdf`, ...) writes that picture and the page next to it under the same name |
| `--dry-run` | Crawl and draw, but do not write `data/current_state.json` or add to the history |
| `--history [N]` | List the `N` most recent recorded crawls (default 20, `0` = all) with what changed in each, and exit |
| `--serve` | Host the explorer and the JSON API instead of crawling now (see below) |
| `--port PORT` / `--bind ADDR` | Port (default 8080, or `LLDP_MONITOR_PORT` / `web.port`) and address (default `127.0.0.1`, or `LLDP_MONITOR_BIND` / `web.bind`) of `--serve` |
| `-v` | Debug logging |

| Exit code | Meaning |
|---|---|
| 0 | Every device that was attempted was crawled |
| 1 | Expected failure: bad config, unreachable seed, unusable output |
| 2 | Unexpected error (traceback in the log) |
| 3 | Map written, but some devices failed or `max_devices` cut the crawl short |

Example log:

```
INFO    src.crawler: [1] Crawling 10.0.0.1 (cisco_ios) depth=0 queue=0
INFO    src.crawler: Not crawling isp-edge: management IP 203.0.113.1 is outside crawl.allowed_subnets
INFO    src.crawler: core1: 5 LLDP neighbor(s), 3 queued
ERROR   src.crawler: Failed to crawl 10.0.0.250: Authentication failed for 10.0.0.250 ...
```

## Web server mode

```bash
python3 main.py --serve                # http://127.0.0.1:8080/
python3 main.py --serve --port 9000
```

`--serve` hosts `output/topology.html` and a small JSON API (standard library `http.server`, nothing to install).
Every answer is `{"status": "ok", "data": ...}` or `{"status": "error", "error": "..."}` with a standard status code.

| Endpoint | Answer |
|---|---|
| `GET /` | The explorer page. When served, it shows a **History** bar: drag the slider to see the topology as it was in an earlier crawl (what did not exist then is hidden), **Changes** lists what differed from the crawl before |
| `GET /api/status` | Crawl state (`idle` / `running`, how the last one ended), device and link counts, blind spots, warning counts (with the VLAN ones), the latest history run |
| `GET /api/history` | The recorded crawls, newest first, each with its change summary |
| `GET /api/history/<run_id>` | One crawl: its full diff and its full graph JSON (404 for an unknown ID, 400 for a malformed one) |
| `POST /api/crawl` | Start a crawl in a background thread with the options `--serve` was started with (`--replay`, `--allow`, ...). 409 while one is running; the lock file `data/.crawl.lock` also stops it overlapping a crawl started elsewhere |

`POST /api/crawl` logs in to your devices with your credentials, so the server listens on `127.0.0.1` unless you
say otherwise. It refuses requests from other web sites (cross-origin `Origin`, and on a loopback address any
`Host` that is not `localhost` / `127.0.0.1` / `::1`), takes no options in the request body (it must be empty or
`{}`, with `Content-Type: application/json`), and has no login of its own: if you bind it to a network address, put
something that authenticates people in front of it. Stop it with Ctrl+C.

## Platform support

Built-in profiles use the templates that ship with `ntc-templates`. Each profile passes its template to
Netmiko explicitly, so the mismatch between Netmiko device types and ntc platform names (`huawei` vs
`huawei_vrp`) does not matter.

| `device_type` | Discovery | Port-channel command | Interface status command | Notes |
|---|---|---|---|---|
| `cisco_ios`, `cisco_xe` | LLDP, CDP | `show etherchannel summary` | `show interfaces status` | Tested against bundled captures |
| `cisco_nxos` | LLDP, CDP | `show port-channel summary` | `show interface status` | Tested against bundled captures |
| `cisco_xr` | LLDP, CDP | none | `show interfaces` | Column maps only; no bundled capture |
| `arista_eos` | LLDP | `show port-channel summary` | `show interfaces status` | The LLDP template has no capabilities column |
| `aruba_aoscx` | LLDP | none | `show interface` | Its bundles are known only from the other end |
| `huawei`, `huawei_vrp`, `huawei_vrpv8` | LLDP | `display eth-trunk` | `display interface` | Tested against bundled captures |
| `juniper_junos` | LLDP | `show lacp interfaces` | `show interfaces` | Tested against bundled captures |
| `vyos` | LLDP | none | `show interfaces` | Tested against bundled captures |
| `hp_procurve` | LLDP, CDP | none | `show interfaces status` | Tested against bundled captures; CDP listen-mode supported |

LLDP is `show lldp neighbors detail` (`show lldp neighbor-info detail` on AOS-CX, `display lldp neighbor-information
verbose` on Huawei); CDP is `show cdp neighbors detail`. Every command runs on the same SSH session. The extra ones
never fail the device: if the port-channel or interface-status command is rejected or missing, the neighbors are kept,
the links are simply not bundled or not checked, and the details panel says why. A discovery protocol that is
rejected, empty or switched off is only a note as long as another one worked, so a device with no LLDP is still
crawled through CDP. Settings: `crawl.discovery` (default `[lldp, cdp]`), `crawl.collect_port_channels`,
`crawl.collect_interface_status`, `link_checks.enabled`, or the matching command-line flags.

`show interfaces status` is cheap. Huawei `display interface` and AOS-CX `show interface` print every counter of every
port, so on large stacks they are the slowest part of a crawl: use `--no-link-checks` there if that matters.

**Not built in, on purpose:**

- **FortiGate / FortiOS**: ntc-templates only has a per-port template (`diagnose lldprx port neighbor details <port>`).
- **pfSense, Check Point, Ruijie**: no template. They show up as grey or red nodes. Give a neighbor an address
  with `crawl.name_to_ip` if it does not advertise one.

A neighbor whose platform has no profile is drawn grey with the reason, and is never logged in to.

### Other vendor discovery protocols

Researched for this project (the vendor documentation, `ntc-templates`, and the output samples they print):

| Protocol | Result |
|---|---|
| **CDP** (Cisco) | Built in for IOS / IOS-XE, NX-OS and IOS-XR, using the ntc-templates CDP templates. |
| **FDP** (Ruckus / Brocade FastIron) | Documented output has the CDP layout. `templates/ruckus_fastiron_show_fdp_neighbors_detail.textfsm` reads both documented variants, checked against the samples printed in the Ruckus documentation only, not against hardware. It is not enabled by default: see the `custom_vendors` example in `config/settings.yaml`. On an ICX it lists the Cisco neighbors learned through CDP; the Ruckus-to-Ruckus layout shown under "Verifying FDP" is not read. |
| **NDP** (H3C Comware) | Not built. H3C's documented `display ndp` output does not show the local port, so no link can be built from it. It is not Huawei VRP, which only uses LLDP. |
| **EDP** (Extreme) | Not built. No documented output sample was found to write a template against. |
| Juniper, Huawei VRP, Fortinet | LLDP only: they have no proprietary neighbor protocol of this kind. |

Any other protocol can be added with a template and a few lines under `custom_vendors` (see below): a platform may
now be defined by `protocols:` alone, with no LLDP part.

### Adding a platform

Drop a TextFSM template into `templates/` and describe it under `custom_vendors` in `settings.yaml`:

```yaml
custom_vendors:
  juniper_junos:
    command: show lldp neighbors
    template: templates/juniper_junos_show_lldp_neighbors_mgmt.textfsm
    fields:                       # your template's column names (a list = first non-empty wins)
      local_interface: LOCAL_INTERFACE
      neighbor_name: NEIGHBOR_NAME
      neighbor_interface: NEIGHBOR_INTERFACE
      chassis_id: CHASSIS_ID
      mgmt_address: MGMT_ADDRESS
    error_markers: ["syntax error"]
```

Field names are `local_interface`, `neighbor_name`, `neighbor_interface`, `chassis_id`, `mgmt_address`,
`description`, `capabilities` and `platform`. The template must produce at least a neighbor name, chassis ID or management
address per row. Use `--save-raw` against one real device to get text to write the template against.

To read another discovery protocol of a platform, add `protocols:` with the same three keys per protocol (the name
is yours: `cdp`, `fdp`, ...) and list that name in `crawl.discovery`. The LLDP keys can be left out entirely:

```yaml
crawl:
  discovery: [lldp, cdp, fdp]
custom_vendors:
  ruckus_fastiron:
    protocols:
      fdp:
        command: show fdp neighbors detail
        template: templates/ruckus_fastiron_show_fdp_neighbors_detail.textfsm
        fields: {local_interface: LOCAL_INTERFACE, neighbor_name: NEIGHBOR_NAME,
                 neighbor_interface: NEIGHBOR_INTERFACE, mgmt_address: MGMT_ADDRESS,
                 description: NEIGHBOR_DESCRIPTION, capabilities: CAPABILITIES, platform: PLATFORM}
```

A neighbor found this way needs a device type to be logged in to: set it with `crawl.device_overrides`.

## Capturing and replaying real output

```bash
python3 main.py --seed 10.0.0.1 --allow 10.0.0.0/16 --save-raw captures/site-a   # live, one extra command/device
python3 main.py --seed 10.0.0.1 --allow 10.0.0.0/16 --replay captures/site-a     # offline, same result
```

A capture directory holds, per device, `<host>.txt` (LLDP), `<host>.cdp.txt` (any other protocol, named after it),
`<host>.lag.txt` and `<host>.status.txt`, plus an `index.yaml` (host -> files, hostname, device type). A device with no
`<host>.txt` is replayed through its other protocols, like a device whose LLDP is off.
`captures/` is git-ignored: raw output can contain sensitive data.

## Telegram Alerting & Production Scheduling

LLDP TopoViz includes a production-grade Telegram alerting pipeline that dispatches crawl completion notices, unreachable switch warnings, and unhandled crash reports:

- **Instant Connectivity Test**: Verify bot credentials and chat IDs immediately:
  ```bash
  ./run --test-telegram
  ```
- **Concurrency-Safe Cron**: Use [`scripts/run_crawl_cron.sh`](scripts/run_crawl_cron.sh) with non-blocking `flock` to avoid overlapping runs:
  ```cron
  */15 * * * * /home/nms-net/lldp-topoviz/scripts/run_crawl_cron.sh >> /var/log/lldp-topoviz-cron.log 2>&1
  ```
- **Systemd Timer & Service**: Ready-to-use hardened unit files in [`systemd/`](systemd/).

For detailed bot setup, topic threads, cooldown windows, and corporate proxy configuration, refer to the **[Telegram Alerting Operations Guide](docs/TELEGRAM_ALERTING.md)**.

## Security notes

- Credentials come from `LLDP_MAPPER_*` or the settings file, are never logged, and are hidden from `repr()`.
- LLDP strings (names, descriptions) are attacker-controlled by whatever sits on the other end of a cable. They
  are stripped of control characters before they reach the diagram, embedded as JSON with `<`, `>` and `&`
  escaped, and written into the details panel with `textContent` only.
- `allowed_subnets` is your protection against logging in to a customer's or ISP's equipment that happens to
  advertise a management address. Keep it as narrow as your estate allows.
- SSH host keys are not verified (Netmiko's default). Run the tool from a trusted management network.

## Development

```bash
pip install -r requirements-dev.txt
pytest                 # no devices or network needed
ruff check . && ruff format --check .
```

Node.js (>= 16) is recommended for running the full suite. The failure simulation in the HTML viewer is plain
JavaScript, and `tests/test_simulation.py` runs it under `node` against the same data the page embeds; the blast
radius, the CLI runbook and the `TOPOLOGY_SPEC.md` generator are pure functions run the same way by
`tests/test_explorer_ops.py`. Without
`node` on your `PATH` those tests are skipped (`pytest -rs` lists them) and everything else still runs. CI installs
Node 20, so they always run there.

`requirements.lock` records the exact versions the tests were run with (Netmiko 4.8.0, ntc-templates 9.3.0,
NetworkX 3.7, Pyvis 0.3.2).

## Known limitations

- Devices are identified by hostname, management IP and chassis ID. Two different devices that share a hostname
  are treated as one.
- The crawl is sequential (one SSH session at a time), also with several seeds: seeds share one queue and are all
  served before any deeper device, and `max_devices` is shared by all of them. Large networks take a while.
- All seeds use one device type (`--device-type` / `seed.device_type`); to crawl a seed of another platform, list it
  under `crawl.device_overrides` (its `device_type` is used for a seed too).
- VLAN checks need the switchport command of the platform: built in for Cisco IOS, IOS-XE and NX-OS only (other
  platforms are crawled as before, without VLAN data; `custom_vendors` cannot add it yet). Ports that are routed or
  whose mode is not definite (dynamic and down) are not judged, and two access ports with different VLANs are not
  flagged.
- The crawl history keeps one full graph per crawl (compressed); `history.max_runs` bounds it. It is stored in a local
  SQLite file, so several crawlers on different machines do not share one history.
- A link whose ports are unknown on both ends and which sits beside another unknown-port link between the same
  two devices is merged into one.
- Only IPv4 management addresses are used; IPv6-only neighbors are drawn but not crawled.
- Tiers, firewall icons and OS labels are inferred (see "How the diagram decides things"). Name your devices
  `core*` / `dist*` / `acc*`, or set `visualization.tiers` and `visualization.node_types`, for an exact result.
- Link speed is the port type from the interface name, not the negotiated speed, and is unknown for names such as
  `Ethernet1/1`.
- A port-channel is drawn from what each device reports about its own ports. The tool does not check LACP
  negotiation between the two ends, and it cannot see a bundle on a device whose platform has no port-channel
  command (the other end still shows it, with `?` for the unknown side).
- STP blocking is not shown, and there is no SNMP fallback: devices are read over SSH only.
- Link checks need both ends' ports to be up, and judge what the device reports at crawl time. They find a Gigabit
  port that negotiated down, or a forced speed or duplex on one end; they cannot see errors, drops or traffic.
- `Gi` at 100M is flagged even when it is intended; use `link_checks.ignore_ports` for those. A device that is not
  crawled has no interface data, so a link to it is only checked from the other end against the port type.
- An empty answer is accepted when the output says so ("not enabled", "entries displayed: 0") or is only a few lines;
  a long output in which the template finds nothing is treated as the wrong platform.
- The bundled sample captures were written to match the ntc-templates formats, not recorded from live devices.
  Output from real devices of other software versions can differ: check one device with `--save-raw` first.
