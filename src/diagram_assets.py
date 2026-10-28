"""
==============================================================================
Module Name:   diagram_assets.py
Description:   The CSS and JavaScript that src/visualizer.py injects into the
               Pyvis page: device names with an IP / OS line and failure label,
               tier bands, interface names at both ends of every link, a
               problems list, and a details panel for devices and links.
Author:        Mai Tan Duc <ducmai.network@gmail.com>
Created:       2026-10-11
Version:       1.3.0
License:       MIT
==============================================================================
Usage:         imported by src/visualizer.py (not run directly)
Notes:         Requires Python 3.10+. The page needs no network access; all
               device data reaches the script as JSON (window.LLDP_DETAIL) and
               is written into the DOM with textContent only.
==============================================================================
"""

from __future__ import annotations

PAGE_CSS = """
#lldp-legend,#lldp-detail{font:13px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
color:#1f2933;background:rgba(255,255,255,.96);border:1px solid #cbd2d9;border-radius:8px}
#lldp-legend{position:fixed;top:12px;left:12px;z-index:20;width:min(300px,calc(100vw - 24px));
max-height:calc(100vh - 24px);overflow:auto;padding:10px 12px}
#lldp-legend h1{font-size:14px;margin:0 0 2px}
#lldp-legend h2{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:#52606d;margin:10px 0 3px}
#lldp-legend p{margin:0 0 6px;color:#52606d}
#lldp-legend ul{list-style:none;margin:0;padding:0}
#lldp-legend ul.cols{display:grid;grid-template-columns:1fr 1fr;gap:2px 8px}
#lldp-legend li{display:flex;align-items:center;gap:8px;margin:3px 0}
#lldp-legend .sw{width:12px;height:12px;border-radius:50%;flex:none;border:3px solid}
#lldp-legend .ico{width:18px;height:18px;flex:none}
#lldp-legend .ln{width:24px;height:0;flex:none;border-top:4px solid}
#lldp-legend .hint{margin-top:8px;color:#52606d}
#lldp-problems:empty{display:none}
#lldp-problems li{display:block;margin:6px 0}
#lldp-problems .pb-name{border:0;background:none;padding:0;font:inherit;font-weight:600;color:#a61b1b;
cursor:pointer;text-decoration:underline}
#lldp-problems .pb-why{color:#52606d;font-size:12px}
#lldp-detail{position:fixed;top:12px;right:12px;z-index:30;width:min(380px,calc(100vw - 24px));
max-height:calc(100vh - 24px);overflow:auto;padding:12px 14px;box-shadow:0 4px 18px rgba(31,41,51,.18)}
#lldp-detail[hidden]{display:none}
#lldp-detail .ld-head{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}
#lldp-detail h2{font-size:16px;margin:0 0 6px;word-break:break-word}
#lldp-detail h3{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:#52606d;margin:12px 0 4px}
#lldp-detail .ld-close{border:0;background:none;font-size:24px;line-height:1;cursor:pointer;
color:#52606d;padding:0 4px;border-radius:4px}
#lldp-detail .ld-close:focus-visible,#lldp-detail .ld-peer:focus-visible,
#lldp-problems .pb-name:focus-visible{outline:2px solid #2F6FDE;outline-offset:1px}
#lldp-detail .ld-badge{display:inline-block;padding:1px 9px;border-radius:10px;font-size:12px;color:#fff;margin-bottom:8px}
#lldp-detail .ld-crawled{background:#2F9E6E}
#lldp-detail .ld-failed{background:#D64545}
#lldp-detail .ld-discovered{background:#6B7785}
#lldp-detail .ld-link{background:#3E4C59}
#lldp-detail .ld-bundle{background:#2F6FDE}
#lldp-detail dl{display:grid;grid-template-columns:max-content 1fr;gap:3px 12px;margin:0}
#lldp-detail dt{color:#52606d}
#lldp-detail dd{margin:0;word-break:break-word}
#lldp-detail .ld-text{white-space:pre-wrap;word-break:break-word;background:#f5f7fa;border-radius:6px;padding:6px 8px}
#lldp-detail .ld-err{color:#a61b1b;background:#fdecec}
#lldp-detail ul{list-style:none;margin:0;padding:0}
#lldp-detail li{padding:4px 0;border-top:1px solid #e4e7eb;word-break:break-word}
#lldp-detail .ld-port{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px}
#lldp-detail .ld-tag{font-size:11px;color:#2F6FDE;font-weight:600}
#lldp-detail .ld-state{color:#52606d;font-size:12px}
#lldp-detail .ld-peer{border:0;background:none;padding:0;font:inherit;font-weight:600;color:#1f5fc4;
cursor:pointer;text-decoration:underline}
#lldp-legend .lg-head{display:flex;align-items:center;justify-content:space-between;gap:8px}
#lldp-legend #lldp-legend-toggle{border:1px solid #cbd2d9;background:#fff;border-radius:4px;width:24px;height:24px;
line-height:1;cursor:pointer;color:#52606d;font-size:16px;padding:0}
#lldp-legend.collapsed #lldp-legend-body{display:none}
#lldp-legend.collapsed{width:auto}
#lldp-legend li{margin:1px 0}
#lldp-legend .lg-item{display:flex;align-items:center;gap:8px;width:100%;border:1px solid transparent;
background:none;border-radius:5px;padding:2px 4px;font:inherit;color:inherit;text-align:left;cursor:pointer}
#lldp-legend .lg-item:hover{background:#eef2f6}
#lldp-legend .lg-item[aria-pressed="true"]{background:#e3edff;border-color:#2F6FDE}
#lldp-legend .lg-item:focus-visible,#lldp-legend #lldp-legend-toggle:focus-visible,
#lldp-warnings .pb-name:focus-visible{outline:2px solid #2F6FDE;outline-offset:1px}
#lldp-legend .ln.thick{border-top-width:7px}
#lldp-legend .ln.dashed{border-top-style:dashed}
#lldp-legend .wm{display:inline-flex;align-items:center;justify-content:center;width:16px;height:16px;flex:none;
border-radius:3px;color:#fff;font-weight:700;font-size:11px}
#lldp-legend .tier-key{width:14px;height:8px;flex:none;border-radius:2px;background:#d9dee5}
#lldp-warnings:empty{display:none}
#lldp-warnings li{display:block;margin:6px 0}
#lldp-warnings .pb-name{border:0;background:none;padding:0;font:inherit;font-weight:600;cursor:pointer;
text-decoration:underline}
#lldp-warnings .sev-degraded{color:#9a5b00}
#lldp-warnings .sev-mismatch{color:#b3380a}
#lldp-detail .ld-warn{background:#E08A00}
#lldp-detail .ld-mismatch{background:#D9480F}
#lldp-detail li.w-degraded{border-left:4px solid #E08A00;padding-left:8px}
#lldp-detail li.w-mismatch{border-left:4px solid #D9480F;padding-left:8px}
div.vis-tooltip{white-space:pre-wrap!important;max-width:420px;word-break:break-word;
font:12px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif!important;padding:8px 10px!important;
border-radius:6px!important}
"""

# Runs after Pyvis has built the network (the global ``network``).
PAGE_JS = r"""
(function () {
  'use strict';
  var DATA = window.LLDP_DETAIL, STYLE = window.LLDP_STYLE || {};
  var panel = document.getElementById('lldp-detail');
  if (!DATA || !panel || typeof network === 'undefined') { return; }
  var FAMILY = '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
  var MONO = 'ui-monospace, "JetBrains Mono", "Fira Code", Menlo, Consolas, monospace';
  // Colours of what is drawn on the canvas; the theme code (OPS_UI_JS) replaces window.LLDP_PAL on a theme change.
  function pal() {
    return window.LLDP_PAL || { bg: STYLE.bg || '#ffffff', fg: STYLE.fg || '#323f4b', muted: '#6b7785', pill: 'rgba(255,255,255,0.82)',
                                band: 'rgba(31,41,51,0.045)', tier: '#7b8794', edgeText: '#343434', edgeDim: 'rgba(52,52,52,0.15)', mark: '#ffffff' };
  }
  var selected = null;                       // {type, id} of what the inspector shows

  // ------------------------------------------------------------ small DOM helpers
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text !== undefined && text !== null) { e.textContent = text; }
    return e;
  }
  var bodies = {}, cur = panel, tab = 'ov', ctx = null;
  function hide() {
    panel.hidden = true; ctx = null; selected = null;
    if (window.LLDP_EGO) { window.LLDP_EGO(null); }
  }
  function showTab(t) {
    tab = t;
    ['ov', 'if', 'cli'].forEach(function (k) { if (bodies[k]) { bodies[k].hidden = k !== t; } });
    Array.prototype.forEach.call(panel.querySelectorAll('.ld-tab'), function (b) {
      var on = b.getAttribute('data-tab') === t;
      b.classList.toggle('on', on);
      b.setAttribute('aria-selected', String(on));
    });
    if (t === 'cli' && window.LLDP_CLI && ctx && bodies.cli && !bodies.cli.firstChild) { window.LLDP_CLI(bodies.cli, ctx); }
  }
  // Header, status badge and three tabs: Overview | Interfaces & Peers | CLI Diagnostics.
  function begin(title, badgeText, badgeClass, context) {
    while (panel.firstChild) { panel.removeChild(panel.firstChild); }
    var head = el('div', 'ld-head');
    head.appendChild(el('h2', null, title));
    var close = el('button', 'ld-close', '\u00d7');
    close.type = 'button';
    close.setAttribute('aria-label', 'Close details');
    close.addEventListener('click', hide);
    head.appendChild(close);
    panel.appendChild(head);
    panel.appendChild(el('span', 'ld-badge ' + badgeClass, badgeText));
    var tabs = el('div', 'ld-tabs');
    tabs.setAttribute('role', 'tablist');
    bodies = {};
    [['ov', 'Overview'], ['if', 'Interfaces & Peers'], ['cli', 'CLI Diagnostics']].forEach(function (t) {
      var b = el('button', 'ld-tab', t[1]);
      b.type = 'button';
      b.setAttribute('role', 'tab');
      b.setAttribute('data-tab', t[0]);
      b.addEventListener('click', function () { showTab(t[0]); });
      tabs.appendChild(b);
      bodies[t[0]] = el('div', 'ld-body');
      bodies[t[0]].setAttribute('role', 'tabpanel');
    });
    panel.appendChild(tabs);
    ['ov', 'if', 'cli'].forEach(function (k) { panel.appendChild(bodies[k]); });
    ctx = context || null;
    cur = bodies.ov;
    showTab(tab);
  }
  function section(title, text, cls) {
    cur.appendChild(el('h3', null, title));
    cur.appendChild(el('div', cls || 'ld-text', text));
  }
  function facts(rows) {
    var dl = el('dl');
    rows.forEach(function (r) {
      var v = r[1];
      if (v === undefined || v === null || v === '' || (Array.isArray(v) && !v.length)) { return; }
      dl.appendChild(el('dt', null, r[0]));
      dl.appendChild(el('dd', null, Array.isArray(v) ? v.join(', ') : String(v)));
    });
    if (dl.firstChild) { cur.appendChild(dl); }
  }
  window.LLDP_FOCUS = function (id) { focusNode(id); };
  function focusNode(id) {
    if (window.LLDP_REVEAL) { window.LLDP_REVEAL(id); }   // open the endpoint group it is hidden in
    network.selectNodes([id]);
    network.focus(id, { scale: Math.max(network.getScale(), 0.9), animation: { duration: 400 } });
    showNode(id);
  }

  // ------------------------------------------------------------------ device panel
  function showNode(id) {
    var n = DATA.nodes[id];
    if (!n) { hide(); return; }
    begin(n.label, n.status + (n.seed ? ' \u00b7 seed' : ''), 'ld-' + n.status, { kind: 'node', id: id });
    selected = { type: 'node', id: id };
    if (window.LLDP_EGO) { window.LLDP_EGO(id); }
    var change = DATA.diff && DATA.diff.nodes && DATA.diff.nodes[id];
    if (change) { section('Since the previous run', change.status.toUpperCase() + ': ' + change.detail); }
    if (n.error) {
      section(n.badge || 'Error', n.error + (n.hint ? '\n\n' + n.hint : ''), 'ld-text ld-err');
    }
    facts([['Type', n.kind], ['Tier', n.tier], ['OS', n.os], ['Hardware', n.platform],
           ['Address', n.address], ['Management IPs', n.mgmt_ips], ['Device type', n.device_type],
           ['Chassis ID', n.chassis_id], ['Capabilities', n.capabilities],
           ['Discovery', (n.discovery || []).map(function (x) { return x.toUpperCase(); })],
           ['Depth', n.depth],
           ['Reached from seed', DATA.meta && DATA.meta.seeds && DATA.meta.seeds.length > 1 ? n.seed_origin : '']]);
    if (n.skip_reason) { section('Not crawled', n.skip_reason); }
    var noted = Object.keys(n.protocol_notes || {});
    if (n.status === 'crawled' && noted.length) {
      section('Discovery notes', noted.map(function (k) { return k.toUpperCase() + ': ' + n.protocol_notes[k]; }).join('\n'));
    }
    if (n.status === 'crawled' && n.status_note) { section('Link checks', 'No interface data: ' + n.status_note); }
    section('Platform description',
            n.description || 'This device did not advertise a system description over LLDP.');

    cur = bodies['if'];
    var flt = el('input', 'ld-filter');                    // narrow the list of ports and peers
    flt.type = 'search';
    flt.placeholder = 'Filter by port, peer or bundle';
    flt.setAttribute('aria-label', 'Filter ports and peers');
    cur.appendChild(flt);
    if (n.port_channels.length) {
      cur.appendChild(el('h3', null, 'Port-channels (' + n.port_channels.length + ')'));
      var pcs = el('ul');
      n.port_channels.forEach(function (pc) {
        var li = el('li');
        li.appendChild(el('strong', null, pc.name));
        li.appendChild(document.createTextNode(' \u00b7 ' + pc.protocol + (pc.up ? '' : ' \u00b7 down')));
        pc.members.forEach(function (m) {
          var line = el('div');
          line.appendChild(el('span', 'ld-port', m.interface));
          line.appendChild(el('span', 'ld-state', '  ' + m.state));
          li.appendChild(line);
        });
        pcs.appendChild(li);
      });
      cur.appendChild(pcs);
    } else if (n.status === 'crawled') {
      section('Port-channels', n.lag_note ? 'Not collected: ' + n.lag_note : 'None configured.');
    }

    cur.appendChild(el('h3', null, 'Links (' + n.links.length + ')'));
    var ul = el('ul');
    n.links.forEach(function (l) {
      var li = el('li');
      li.appendChild(el('span', 'ld-port', l.local || '?'));
      if (l.bundle) { li.appendChild(el('span', 'ld-tag', ' [' + l.bundle + ']')); }
      li.appendChild(document.createTextNode(' \u2194 '));
      var peer = el('button', 'ld-peer', l.peer_label);
      peer.type = 'button';
      peer.addEventListener('click', function () { focusNode(l.peer); });
      li.appendChild(peer);
      li.appendChild(document.createTextNode(' '));
      li.appendChild(el('span', 'ld-port', l.remote || '?'));
      if (l.peer_bundle) { li.appendChild(el('span', 'ld-tag', ' [' + l.peer_bundle + ']')); }
      ul.appendChild(li);
    });
    cur.appendChild(ul);
    flt.addEventListener('input', function () {
      var q = flt.value.toLowerCase();
      Array.prototype.forEach.call(ul.children, function (li) { li.hidden = q !== '' && li.textContent.toLowerCase().indexOf(q) < 0; });
    });
    panel.hidden = false;
  }

  // -------------------------------------------------------------------- link panel
  function speedText(k) {
    if (k.speed === '?') { return 'unknown (no interface data, and the port names carry no speed)'; }
    return k.speed + (k.speed_source === 'operating' ? ' (in use)' : k.speed_source === 'nominal' ? ' (port type)' : '');
  }
  function showLink(id) {
    var k = DATA.links[id];
    if (!k) { hide(); return; }
    var b = k.bundle;
    var badge = b ? b.protocol + ' port-channel \u00b7 ' + b.links + (b.total > b.links ? ' of ' + b.total : '') + ' links'
                  : (k.pairs.length > 1 ? k.pairs.length + ' parallel links' : 'link');
    begin(b ? k.a + ' ' + (b.a || '?') + ' \u2194 ' + k.b + ' ' + (b.b || '?') : k.a + ' \u2194 ' + k.b,
          badge, k.warn === 'mismatch' ? 'ld-mismatch' : k.warn === 'degraded' ? 'ld-warn' : (b ? 'ld-bundle' : 'ld-link'), { kind: 'link', id: id });
    selected = { type: 'link', id: id };
    if (window.LLDP_EGO) { window.LLDP_EGO(null); }
    if (k.diff) { section('Since the previous run', k.diff.status.toUpperCase() + ': ' + k.diff.detail); }
    if (k.warnings.length) {
      cur.appendChild(el('h3', null, 'Warnings (' + k.warnings.length + ')'));
      var wl = el('ul');
      k.warnings.forEach(function (w) { wl.appendChild(el('li', 'w-' + w.severity, w.text)); });
      cur.appendChild(wl);
    }
    facts([['Speed', speedText(k)], ['Bundle state', b ? (b.up ? 'up' : 'down') : ''],
           ['Seen by', (k.sources || []).map(function (x) { return x.toUpperCase(); }).join(' + ')],
           ['Link checks', k.checked ? '' : 'no interface speed/duplex data for this link']]);
    if (k.vlan && k.vlan.length) {
      cur.appendChild(el('h3', null, 'VLAN'));
      var vl = el('ul');
      k.vlan.forEach(function (v) {
        [['a', k.a], ['b', k.b]].forEach(function (side) {
          var info = v[side[0]];
          if (!info) { return; }
          var text = info.mode === 'trunk'
            ? 'trunk, native VLAN ' + (info.native_vlan || '?') + ', allowed ' + (info.allowed_vlans || '?')
            : info.mode === 'access' ? 'access, VLAN ' + (info.access_vlan || '?') : info.mode;
          vl.appendChild(el('li', k.vlan_warn ? 'w-' + k.vlan_warn : null, side[1] + ' ' + info.interface + ': ' + text));
        });
      });
      cur.appendChild(vl);
    }
    cur = bodies['if'];
    cur.appendChild(el('h3', null, b ? 'Member links' : 'Interfaces'));
    var ul = el('ul');
    k.pairs.forEach(function (p) {
      var li = el('li');
      li.appendChild(document.createTextNode(k.a + ' '));
      li.appendChild(el('span', 'ld-port', p[0] || '?'));
      if (b) { li.appendChild(el('span', 'ld-state', ' (' + (p[2] || '?') + ')')); }
      if (p[4]) { li.appendChild(el('span', 'ld-state', ' [' + p[4] + ']')); }
      li.appendChild(document.createTextNode('  \u2194  ' + k.b + ' '));
      li.appendChild(el('span', 'ld-port', p[1] || '?'));
      if (b) { li.appendChild(el('span', 'ld-state', ' (' + (p[3] || '?') + ')')); }
      if (p[5]) { li.appendChild(el('span', 'ld-state', ' [' + p[5] + ']')); }
      ul.appendChild(li);
    });
    cur.appendChild(ul);
    panel.hidden = false;
  }
  function focusEdge(id) {
    var k = DATA.links[id];
    network.selectEdges([id]);
    network.fit({ nodes: [k.from, k.to], animation: { duration: 400 } });
    showLink(id);
  }

  network.on('click', function (p) {
    if (window.LLDP_SIM && window.LLDP_SIM.active) { return; }   // the simulation handles clicks
    if (p.nodes && p.nodes.length) { showNode(p.nodes[0]); }
    else if (p.edges && p.edges.length) { showLink(p.edges[0]); }
    else { hide(); }
  });
  network.once('stabilizationIterationsDone', function () {
    network.setOptions({ physics: false });
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !(e.target && /INPUT|TEXTAREA|SELECT/.test(e.target.tagName || ''))) { hide(); network.unselectAll(); }
  });

  // -------------------------------------------------- problems list in the legend
  var box = document.getElementById('lldp-problems');
  if (box && DATA.problems && DATA.problems.length) {
    box.appendChild(el('h2', null, 'Problems (' + DATA.problems.length + ')'));
    var plist = el('ul');
    DATA.problems.forEach(function (pr) {
      var li = el('li');
      var name = el('button', 'pb-name', pr.label);
      name.type = 'button';
      name.addEventListener('click', function () { focusNode(pr.id); });
      li.appendChild(name);
      li.appendChild(document.createTextNode(' \u2014 ' + pr.badge));
      li.appendChild(el('div', 'pb-why', (pr.address ? pr.address + ': ' : '') + pr.hint));
      plist.appendChild(li);
    });
    box.appendChild(plist);
  }

  // ------------------------------------------- blind spots (uncrawled infrastructure) in the legend
  var bbox = document.getElementById('lldp-blindspots');
  if (bbox && DATA.blind_spots && DATA.blind_spots.length) {
    bbox.appendChild(el('h2', null, 'Blind spots (' + DATA.blind_spots.length + ')'));
    var blist = el('ul');
    DATA.blind_spots.forEach(function (b) {
      var li = el('li');
      var name = el('button', 'pb-name', b.hostname || b.chassis_id || b.management_ip || '?');
      name.type = 'button';
      if (DATA.nodes[b.node_id]) { name.addEventListener('click', function () { focusNode(b.node_id); }); }
      li.appendChild(name);
      li.appendChild(document.createTextNode(' \u2014 ' + b.reason));
      li.appendChild(el('div', 'pb-why', 'seen on ' + (b.discovered_via || '?') + (b.parent_port ? ' ' + b.parent_port : '') +
                        (b.management_ip ? ' \u00b7 ' + b.management_ip : '')));
      blist.appendChild(li);
    });
    bbox.appendChild(blist);
  }

  // ------------------------------------------------- warnings list + legend behaviour
  var wbox = document.getElementById('lldp-warnings');
  if (wbox && DATA.warnings && DATA.warnings.length) {
    wbox.appendChild(el('h2', null, 'Warnings (' + DATA.warnings.length + ')'));
    var wlist = el('ul');
    DATA.warnings.forEach(function (w) {
      var li = el('li');
      var b = el('button', 'pb-name sev-' + w.severity, w.a + ' \u2194 ' + w.b);
      b.type = 'button';
      b.addEventListener('click', function () { focusEdge(w.id); });
      li.appendChild(b);
      li.appendChild(el('div', 'pb-why', (w.vlan ? '\u26a0 [VLAN] ' : '') + w.text));
      wlist.appendChild(li);
    });
    wbox.appendChild(wlist);
  }
  var legend = document.getElementById('lldp-legend'), toggle = document.getElementById('lldp-legend-toggle');
  if (legend && toggle) {
    toggle.addEventListener('click', function () {
      var collapsed = legend.classList.toggle('collapsed');
      toggle.setAttribute('aria-expanded', String(!collapsed));
      toggle.textContent = collapsed ? '+' : '\u2013';
    });
  }

  // Click a legend item: highlight what it describes and dim everything else.
  var WARN = { degraded: '#E08A00', mismatch: '#D9480F' };
  var filter = null, dimNodes = {}, dimEdges = {}, lvlN = {}, lvlE = {}, ego = null, lodLow = false;
  var baseEdge = {};
  network.body.data.edges.forEach(function (e) { baseEdge[e.id] = { width: e.width, dashes: e.dashes || false }; });
  function nodeHit(id, type, val) {
    var n = DATA.nodes[id];
    if (type === 'status') { return n.status === val; }
    if (type === 'kind') { return n.kind === val; }
    if (type === 'tier') { return n.tier === val; }
    return type === 'seed' ? n.seed : false;
  }
  function edgeHit(eid, type, val) {
    var k = DATA.links[eid];
    if (type === 'speed') { return k.speed_style === val; }
    if (type === 'bundle') { return !!k.bundle; }
    if (type === 'src') { return k.sources.length > 0 && k.sources.indexOf('lldp') < 0; }
    if (type === 'warn') { return val === 'vlan' ? !!k.vlan_warn : k.warn === val; }
    return false;
  }
  // Focus mode: the 1-hop and 2-hop neighbourhood of one device stays lit (2-hop a little softer), the rest drops to 15 %.
  function hood(center) {
    var live = Object.keys(DATA.links).filter(function (eid) { return !DATA.links[eid].ghost; });
    var n = {}, e = {};
    n[center] = 0;
    live.forEach(function (eid) {
      var k = DATA.links[eid];
      if (k.from === center || k.to === center) { e[eid] = 1; var o = k.from === center ? k.to : k.from; if (n[o] === undefined) { n[o] = 1; } }
    });
    live.forEach(function (eid) {
      var k = DATA.links[eid];
      [[k.from, k.to], [k.to, k.from]].forEach(function (p) {
        if (n[p[0]] === 1 && !e[eid]) { e[eid] = 2; if (n[p[1]] === undefined) { n[p[1]] = 2; } }
      });
    });
    return { n: n, e: e };
  }
  function coreLink(k) {                       // backbone: both ends in the top two tiers
    var a = DATA.nodes[k.from], b = DATA.nodes[k.to];
    return !!a && !!b && a.rank <= 2 && b.rank <= 2;
  }
  var fadeTimer = null;
  function applyFilter(f) { filter = f; refreshDim(); }
  function refreshDim() {
    var f = filter, nodeIds = Object.keys(DATA.nodes), edgeIds = Object.keys(DATA.links), keepN = {}, keepE = {};
    var P = pal();
    if (f) {
      var type = f.split(':')[0], val = f.split(':').slice(1).join(':');
      if (['status', 'kind', 'tier', 'seed'].indexOf(type) >= 0) {
        nodeIds.forEach(function (id) { if (nodeHit(id, type, val)) { keepN[id] = true; } });
        edgeIds.forEach(function (eid) {
          var k = DATA.links[eid];
          if (keepN[k.from] || keepN[k.to]) { keepE[eid] = true; }
        });
      } else {
        edgeIds.forEach(function (eid) {
          if (edgeHit(eid, type, val)) { keepE[eid] = true; keepN[DATA.links[eid].from] = true; keepN[DATA.links[eid].to] = true; }
        });
      }
    }
    var simOn = !!(window.LLDP_SIM && window.LLDP_SIM.active);
    var h = ego && DATA.nodes[ego] && window.LLDP_EGO_ON !== false && !simOn ? hood(ego) : null;
    var goalN = {}, goalE = {};
    nodeIds.forEach(function (id) {
      var v = f && !keepN[id] ? 0.18 : 1;
      if (h) { v = Math.min(v, h.n[id] === undefined ? 0.15 : h.n[id] === 2 ? 0.6 : 1); }
      goalN[id] = v;
    });
    edgeIds.forEach(function (eid) {
      var v = f && !keepE[eid] ? 0.12 : 1;
      if (h) { v = Math.min(v, h.e[eid] === 1 ? 1 : h.e[eid] === 2 ? 0.6 : 0.15); }
      if (lodLow && !coreLink(DATA.links[eid])) { v = Math.min(v, 0.3); }      // zoomed far out: backbone only
      goalE[eid] = v;
    });
    var startN = {}, startE = {}, frames = 0, still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    nodeIds.forEach(function (id) { startN[id] = lvlN[id] === undefined ? 1 : lvlN[id]; });
    edgeIds.forEach(function (eid) { startE[eid] = lvlE[eid] === undefined ? 1 : lvlE[eid]; });
    function paint(t) {                          // t: 0 -> 1, the fade between the old and the new level
      network.body.data.nodes.update(nodeIds.map(function (id) {
        lvlN[id] = startN[id] + (goalN[id] - startN[id]) * t;
        dimNodes[id] = lvlN[id] < 1;
        var diffNode = window.LLDP_NODE_STYLE && window.LLDP_NODE_STYLE(id);
        return { id: id, opacity: diffNode && diffNode.opacity !== undefined ? Math.min(lvlN[id], diffNode.opacity) : lvlN[id] };
      }));
      network.body.data.edges.update(edgeIds.map(function (eid) {
        var k = DATA.links[eid], ov = window.LLDP_EDGE_STYLE && window.LLDP_EDGE_STYLE(eid), base = baseEdge[eid] || {};
        lvlE[eid] = startE[eid] + (goalE[eid] - startE[eid]) * t;
        dimEdges[eid] = lvlE[eid] < 1;
        var dim = lvlE[eid] < 1;
        return { id: eid, color: { color: ov ? ov.color : k.color, opacity: lvlE[eid] },
                 width: (base.width || 1) + (ov && ov.extra ? ov.extra : 0), dashes: ov ? ov.dashes : base.dashes,
                 font: { size: 12, strokeWidth: 4, strokeColor: P.bg,
                         color: dim ? P.edgeDim : P.edgeText,
                         vadjust: (k.warn && k.label) ? -16 : 0 },
                 shadow: (k.warn && !dim) ? { enabled: true, color: WARN[k.warn], size: 14, x: 0, y: 0 } : false };
      }));
      network.redraw();
    }
    if (fadeTimer) { window.cancelAnimationFrame(fadeTimer); fadeTimer = null; }
    var many = nodeIds.length + edgeIds.length > 700;
    if (still || many) { paint(1); }
    else {
      (function step() {
        frames += 1;
        var t = Math.min(1, frames / 7);
        paint(t * t * (3 - 2 * t));
        fadeTimer = t < 1 ? window.requestAnimationFrame(step) : null;
      })();
    }
    Array.prototype.forEach.call(document.querySelectorAll('#lldp-legend .lg-item'), function (b) {
      b.setAttribute('aria-pressed', String(!!f && b.getAttribute('data-filter') === f));
    });
  }
  window.LLDP_EGO = function (center) { ego = center || null; refreshDim(); };
  window.LLDP_RESTYLE = refreshDim;
  Array.prototype.forEach.call(document.querySelectorAll('#lldp-legend .lg-item'), function (b) {
    b.addEventListener('click', function () {
      var key = b.getAttribute('data-filter');
      applyFilter(filter === key ? null : key);
    });
  });
  network.on('hoverNode', function (p) { if (DATA.nodes[p.node] && !selected) { ego = p.node; refreshDim(); } });
  network.on('blurNode', function () { var was = ego; ego = selected && selected.type === 'node' ? selected.id : null; if (was !== ego) { refreshDim(); } });

  // ------------------------------------------- hover / selection highlight on devices
  network.setOptions({ nodes: { chosen: { node: function (values, id, selected, hovering) {
    if (selected || hovering) {
      values.shadow = true;
      values.shadowColor = 'rgba(47,111,222,0.55)';
      values.shadowSize = 16;
      values.shadowX = 0;
      values.shadowY = 0;
    }
  } } } });

  // ------------------------------------------------------- tier bands (hierarchy)
  var TIERS = DATA.tiers || [];
  function tierRows() {
    var pos = network.getPositions();
    return TIERS.map(function (t) {
      var ys = t.nodes.map(function (id) { return pos[id] ? pos[id].y : null; })
                      .filter(function (y) { return y !== null; });
      var y = ys.length ? ys.reduce(function (a, b) { return a + b; }, 0) / ys.length : null;
      return { name: t.name, level: t.level, y: y };
    }).filter(function (r) { return r.y !== null; });
  }
  function viewBox() {
    var c = network.canvas.frame.canvas;
    return { tl: network.DOMtoCanvas({ x: 0, y: 0 }),
             br: network.DOMtoCanvas({ x: c.clientWidth, y: c.clientHeight }) };
  }
  var HALF_ROW = 105;
  window.LLDP_TIER_ROWS = function () { return tierRows(); };
  window.LLDP_HALF_ROW = HALF_ROW;
  network.on('beforeDrawing', function (ctx) {
    if (!TIERS.length) { return; }
    var v = viewBox();
    ctx.save();
    ctx.fillStyle = pal().band;
    tierRows().forEach(function (r) {
      if (r.level % 2 === 1) { ctx.fillRect(v.tl.x, r.y - HALF_ROW, v.br.x - v.tl.x, 2 * HALF_ROW); }
    });
    ctx.restore();
  });

  // ------------------------------------------- text under devices + tier names
  function haloText(ctx, text, x, y) { ctx.strokeText(text, x, y); ctx.fillText(text, x, y); }
  // Distance from the node centre to the bottom of the text written under it.
  function textBottom(n, size) { return size + 20 + (n.sub ? 15 : 0) + (n.badge ? 14 : 0); }

  function drawNodeText(ctx) {
    var scale = network.getScale();
    if (scale < 0.25) { return; }
    var pos = network.getPositions(), P = pal();
    ctx.save();
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    Object.keys(DATA.nodes).forEach(function (id) {
      var n = DATA.nodes[id], p = pos[id], bn = network.body.nodes[id];
      if (!p || !bn || bn.options.hidden) { return; }
      ctx.globalAlpha = lvlN[id] === undefined || lvlN[id] === 1 ? 1 : lvlN[id] < 0.5 ? 0.22 : 0.65;
      var size = bn.options.size || 22;
      // Line 1: bold name. Line 2: management IP and OS, muted. Line 3: status or role badge. Zoomed out
      // (below 0.50) only the name stays.
      var lines = [{ t: n.label, font: '700 13px ' + FAMILY, color: P.fg }];
      if (scale >= 0.5 && n.sub) { lines.push({ t: n.sub, font: '11px ' + MONO, color: P.muted }); }
      if (scale >= 0.5 && (n.badge || n.tier)) {
        lines.push({ t: n.badge ? (n.tone === 'failed' ? '\u25cf ' : '') + n.badge : n.tier, font: '700 10px ' + FAMILY,
                     color: n.tone === 'failed' ? '#C23030' : P.muted, pill: true });
      }
      var widest = 0;
      lines.forEach(function (l) { ctx.font = l.font; widest = Math.max(widest, ctx.measureText(l.t).width); });
      var top = p.y + size + 6, height = 15 * lines.length + 2;
      ctx.fillStyle = P.pill;                            // pill: links pass behind the text
      ctx.fillRect(p.x - widest / 2 - 4, top, widest + 8, height);
      lines.forEach(function (l, i) {
        ctx.font = l.font;
        ctx.fillStyle = l.color;
        ctx.fillText(l.t, p.x, top + 8 + i * 15);
      });
    });
    ctx.restore();
  }
  function drawTierNames(ctx) {
    if (!TIERS.length) { return; }
    var v = viewBox(), scale = network.getScale();
    ctx.save();
    ctx.font = '600 ' + (11 / scale) + 'px ' + FAMILY;
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = pal().tier;
    tierRows().forEach(function (r) {
      ctx.fillText(r.name.toUpperCase(), v.tl.x + 14 / scale, r.y);
    });
    ctx.restore();
  }

  // ---------------------------------------------- interface names on the links
  var LINKS = DATA.draw || [];
  var FONT = '11px ' + FAMILY;

  function pointOn(edge, p, a, b) {
    // A point at fraction p along the (possibly curved) edge, falling back to a straight line.
    try {
      var pt = edge.edgeType.getPoint(p);
      if (pt && isFinite(pt.x) && isFinite(pt.y)) { return pt; }
    } catch (err) { /* use the straight line */ }
    return { x: a.x + (b.x - a.x) * p, y: a.y + (b.y - a.y) * p };
  }
  function drawEnd(ctx, ports, edge, a, b, len, atFrom, size, taken, below) {
    // Interface names for one end of a link, written along the link just clear of the node.
    if (!ports.length) { return; }
    var shown = ports.length > 3 ? ports.slice(0, 2).concat(['+' + (ports.length - 2) + ' more']) : ports;
    var widest = Math.max.apply(null, shown.map(function (t) { return ctx.measureText(t).width; }));
    var ux = (atFrom ? b.x - a.x : a.x - b.x) / len;
    var uy = (atFrom ? b.y - a.y : a.y - b.y) / len;           // is the link leaving downward?
    var dist = size + 10 + widest / 2;
    if (uy > 0.2) {                                            // leaving downward: stay below the
      dist = Math.max(dist, (below + 10) / uy + (uy > 0.5 ? widest / 2 : 0));  // text under the node
    }
    var heading = Math.atan2(uy, ux);
    taken.forEach(function (other) {                           // links leaving at nearly the same
      if (Math.abs(Math.atan2(Math.sin(heading - other), Math.cos(heading - other))) < 0.5) {
        dist += widest + 14;                                   // angle: push this label further out
      }
    });
    taken.push(heading);
    var f = Math.min(0.46, dist / len);
    var p = atFrom ? f : 1 - f;
    var pt = pointOn(edge, p, a, b);
    var before = pointOn(edge, Math.max(0, p - 0.02), a, b);
    var after = pointOn(edge, Math.min(1, p + 0.02), a, b);
    var angle = Math.atan2(after.y - before.y, after.x - before.x);
    if (angle > Math.PI / 2 || angle < -Math.PI / 2) { angle += Math.PI; }  // keep the text upright
    ctx.save();
    ctx.translate(pt.x, pt.y);
    ctx.rotate(angle);
    shown.forEach(function (text, i) {
      var y = -9 - i * 12;
      ctx.strokeText(text, 0, y);
      ctx.fillText(text, 0, y);
    });
    ctx.restore();
  }
  // Interface names show above zoom 1.20, or on the links of the device under the pointer / selected, or on the
  // selected link; "Always show interface names" in the filter panel keeps them on at every zoom.
  function portsWanted(lk) {
    var scale = network.getScale();
    if (scale < 0.3) { return false; }
    if (scale > 1.2 || window.LLDP_IFACES_ON) { return true; }
    if (selected && selected.type === 'link' && selected.id === lk.id) { return true; }
    var centre = ego || (selected && selected.type === 'node' ? selected.id : null);
    return !!centre && (lk.from === centre || lk.to === centre);
  }
  function drawPortLabels(ctx) {
    if (network.getScale() < 0.3) { return; }  // too small to read: skip rather than clutter
    ctx.save();
    ctx.font = FONT;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.lineJoin = 'round';
    ctx.lineWidth = 4;
    ctx.strokeStyle = pal().bg;
    ctx.fillStyle = pal().fg;
    ctx.font = '11px ' + MONO;
    var taken = {};  // node id -> headings already used by labels around that node
    LINKS.forEach(function (lk) {
      var pos = network.getPositions([lk.from, lk.to]);
      var a = pos[lk.from], b = pos[lk.to], edge = network.body.edges[lk.id];
      if (!a || !b || !edge || edge.options.hidden || !portsWanted(lk)) { return; }
      ctx.globalAlpha = dimEdges[lk.id] ? 0.2 : 1;
      var len = Math.sqrt(Math.pow(b.x - a.x, 2) + Math.pow(b.y - a.y, 2)) || 1;
      taken[lk.from] = taken[lk.from] || [];
      taken[lk.to] = taken[lk.to] || [];
      drawEnd(ctx, lk.fromPorts, edge, a, b, len, true,
              network.body.nodes[lk.from].options.size || 22, taken[lk.from],
              textBottom(DATA.nodes[lk.from], network.body.nodes[lk.from].options.size || 22));
      drawEnd(ctx, lk.toPorts, edge, a, b, len, false,
              network.body.nodes[lk.to].options.size || 22, taken[lk.to],
              textBottom(DATA.nodes[lk.to], network.body.nodes[lk.to].options.size || 22));
    });
    ctx.restore();
  }

  function drawWarnMarkers(ctx) {
    // A small triangle on the middle of every link with a finding (amber: degraded, red: mismatch).
    Object.keys(DATA.links).forEach(function (eid) {
      var k = DATA.links[eid];
      if (!k.warn) { return; }
      var edge = network.body.edges[eid], pos = network.getPositions([k.from, k.to]);
      var a = pos[k.from], b = pos[k.to];
      if (!edge || !a || !b || edge.options.hidden) { return; }
      var pt = pointOn(edge, 0.5, a, b), r = 10;
      ctx.save();
      ctx.globalAlpha = dimEdges[eid] ? 0.2 : 1;
      ctx.beginPath();
      ctx.moveTo(pt.x, pt.y - r);
      ctx.lineTo(pt.x + r * 0.95, pt.y + r * 0.75);
      ctx.lineTo(pt.x - r * 0.95, pt.y + r * 0.75);
      ctx.closePath();
      ctx.lineJoin = 'round';
      ctx.lineWidth = 3;
      ctx.strokeStyle = pal().mark;
      ctx.stroke();
      ctx.fillStyle = WARN[k.warn];
      ctx.fill();
      ctx.fillStyle = '#ffffff';
      ctx.font = '700 11px ' + FAMILY;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('!', pt.x, pt.y + 3);
      if (k.vlan_warn) {                           // a VLAN finding: the triangle gets a ⚠ [VLAN] tag under it
        var tag = '\u26a0 [VLAN]';
        ctx.font = '700 10px ' + FAMILY;
        var tw = ctx.measureText(tag).width + 10;
        ctx.fillStyle = WARN[k.vlan_warn];
        ctx.fillRect(pt.x - tw / 2, pt.y + r + 3, tw, 15);
        ctx.fillStyle = '#ffffff';
        ctx.fillText(tag, pt.x, pt.y + r + 11);
      }
      ctx.restore();
    });
  }

  network.on('zoom', function () {
    var low = network.getScale() < 0.5;
    if (low !== lodLow) { lodLow = low; refreshDim(); }
  });
  network.on('afterDrawing', function (ctx) {
    drawNodeText(ctx);
    drawTierNames(ctx);
    drawPortLabels(ctx);
    drawWarnMarkers(ctx);
  });
  network.redraw();
})();
"""

# ----------------------------------------------------------------------------------------
# Failure simulation, SPOF detection and endpoint grouping (toolbar above the diagram).
# ----------------------------------------------------------------------------------------
SIM_CSS = """
#lldp-toolbar,#lldp-sim-banner,#lldp-sim-pop{font:13px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
color:#1f2933;border-radius:8px;box-sizing:border-box}
#lldp-toolbar{position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:25;display:flex;align-items:center;
gap:12px;padding:6px 10px;background:rgba(255,255,255,.96);border:1px solid #cbd2d9}
#lldp-toolbar button,#lldp-sim-banner button,#lldp-sim-pop button{font:inherit;border:1px solid #9aa5b1;background:#fff;
color:#1f2933;border-radius:5px;padding:3px 10px;cursor:pointer}
#lldp-toolbar button:hover,#lldp-sim-banner button:hover,#lldp-sim-pop button:hover{background:#eef2f6}
#lldp-toolbar button[aria-pressed="true"]{background:#fff4d6;border-color:#E0A100}
#lldp-toolbar label{display:flex;align-items:center;gap:6px;cursor:pointer;white-space:nowrap}
#lldp-toolbar label[hidden]{display:none}
#lldp-sim-banner{position:fixed;top:56px;left:50%;transform:translateX(-50%);z-index:25;width:min(720px,calc(100vw - 24px));
padding:8px 12px;background:#fff4d6;border:1px solid #E0A100;color:#5c3d00}
#lldp-sim-banner[hidden],#lldp-sim-pop[hidden]{display:none}
#lldp-sim-banner .sb-row{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px}
#lldp-sim-banner .sb-tag{font-weight:700;letter-spacing:.03em}
#lldp-sim-banner .sb-counts{font-weight:600}
#lldp-sim-banner .sb-btns{display:flex;gap:6px;margin-left:auto}
#lldp-sim-banner button[aria-pressed="true"]{background:#E0A100;border-color:#b88300;color:#2b1c00}
#lldp-sim-banner .sb-line{margin-top:4px;word-break:break-word}
#lldp-sim-pop{position:fixed;z-index:40;padding:8px 10px;background:#fff;border:1px solid #9aa5b1;
box-shadow:0 4px 18px rgba(31,41,51,.22);max-width:300px}
#lldp-sim-pop .sp-title{font-weight:600;margin-bottom:6px;word-break:break-word}
#lldp-toolbar button:focus-visible,#lldp-sim-banner button:focus-visible,#lldp-sim-pop button:focus-visible,
#lldp-toolbar input:focus-visible{outline:2px solid #2F6FDE;outline-offset:1px}
#lldp-blindspots:empty{display:none}
#lldp-blindspots li{display:block;margin:6px 0}
#lldp-blindspots .pb-name{border:0;background:none;padding:0;font:inherit;font-weight:600;color:#9a5b00;
cursor:pointer;text-decoration:underline}
#lldp-blindspots .pb-name:focus-visible{outline:2px solid #2F6FDE;outline-offset:1px}
#lldp-blindspots .pb-why{color:#52606d;font-size:12px}
"""

# Pure reachability / SPOF logic: no DOM, so it also runs under Node (tests/test_simulation.py).
SIM_ENGINE_JS = r"""
(function (root) {
  'use strict';

  // Index the page data once. ``nodes`` is DATA.nodes (rank: 0 = top tier; core; leaf; spof_ok) and
  // ``links`` is DATA.links (one drawn link may carry several cables: pairs.length).
  function build(allNodes, links) {
    var nodes = {};                                 // devices and links flagged ``ghost`` were removed since the previous run
    Object.keys(allNodes).forEach(function (id) { if (!allNodes[id].ghost) { nodes[id] = allNodes[id]; } });
    var model = { nodes: nodes, edges: [], roots: [], isRoot: {} };
    Object.keys(links).forEach(function (id) {
      var k = links[id];
      if (!k.ghost && nodes[k.from] && nodes[k.to]) {
        model.edges.push({ id: id, from: k.from, to: k.to, count: Math.max(1, (k.pairs || []).length) });
      }
    });
    var ids = Object.keys(nodes);
    ids.forEach(function (id) { if (nodes[id].core) { model.roots.push(id); } });
    if (!model.roots.length) {                      // no Core tier: whatever has nothing above it
      var hasUp = {};
      model.edges.forEach(function (e) {
        var a = nodes[e.from].rank, b = nodes[e.to].rank;
        if (a < b) { hasUp[e.to] = true; } else if (b < a) { hasUp[e.from] = true; }
      });
      ids.forEach(function (id) { if (!hasUp[id]) { model.roots.push(id); } });
    }
    model.roots.forEach(function (id) { model.isRoot[id] = true; });
    return model;
  }

  // Which devices still reach the Core when ``failedNodes`` (ids) and ``failedLinks`` (link ids,
  // both Sets) are down. Returns arrays of ids:
  //   failed   - the devices that were taken down
  //   isolated - the blast radius: alive, but no path to any live Core device
  //   active   - alive and connected
  //   spof     - active, non-Core devices left with exactly one working cable towards a higher tier
  function evaluate(model, failedNodes, failedLinks) {
    var nodes = model.nodes, adj = {}, up = {}, seen = {}, queue = [];
    var out = { failed: [], isolated: [], active: [], spof: [] };
    function alive(id) { return !failedNodes.has(id); }
    var live = model.edges.filter(function (e) {
      return !failedLinks.has(e.id) && alive(e.from) && alive(e.to);
    });
    live.forEach(function (e) {
      (adj[e.from] = adj[e.from] || []).push(e.to);
      (adj[e.to] = adj[e.to] || []).push(e.from);
    });
    model.roots.forEach(function (id) { if (alive(id)) { seen[id] = true; queue.push(id); } });
    for (var i = 0; i < queue.length; i++) {        // breadth-first from the live Core devices
      if (nodes[queue[i]].leaf) { continue; }       // an end device is reached, never a path through
      (adj[queue[i]] || []).forEach(function (next) {
        if (!seen[next]) { seen[next] = true; queue.push(next); }
      });
    }
    Object.keys(nodes).sort().forEach(function (id) {
      if (!alive(id)) { out.failed.push(id); } else if (!seen[id]) { out.isolated.push(id); }
      else { out.active.push(id); }
    });
    live.forEach(function (e) {                     // working uplinks: cables to a higher, connected tier
      if (!seen[e.from] || !seen[e.to]) { return; }
      var a = nodes[e.from].rank, b = nodes[e.to].rank;
      if (a < b) { up[e.to] = (up[e.to] || 0) + e.count; }
      else if (b < a) { up[e.from] = (up[e.from] || 0) + e.count; }
    });
    out.active.forEach(function (id) {
      if (nodes[id].spof_ok && !model.isRoot[id] && up[id] === 1) { out.spof.push(id); }
    });
    return out;
  }

  root.LLDPSim = { build: build, evaluate: evaluate };
})(typeof window !== 'undefined' ? window : globalThis);
"""

# Toolbar, banner, click-to-fail popovers, canvas overlays and endpoint grouping.
SIM_UI_JS = r"""
(function () {
  'use strict';
  var DATA = window.LLDP_DETAIL, Sim = window.LLDPSim;
  var bar = document.getElementById('lldp-toolbar');
  if (!DATA || !Sim || !bar || typeof network === 'undefined') { return; }
  var FAMILY = '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
  var RED = '#D64545', AMBER = '#E08A00', BLUE = '#3B82F6';
  var model = Sim.build(DATA.nodes, DATA.links);
  // drained: devices taken out on purpose (maintenance); they count as down, but are drawn and worded as planned.
  var sim = { active: false, failedNodes: new Set(), failedLinks: new Set(), drained: new Set(), showSpof: false,
              result: null, state: {}, blast: null };
  function timeHidden(kind, id) { var h = window.LLDP_TIMEHIDE; return !!(h && h[kind] && h[kind][id]); }
  window.LLDP_SIM = sim;
  sim.refresh = function () { refresh(); };
  var nodesDS = network.body.data.nodes, edgesDS = network.body.data.edges;
  var hierarchical = !!(DATA.tiers && DATA.tiers.length);

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text !== undefined && text !== null) { e.textContent = text; }
    return e;
  }
  function name(id) { return DATA.nodes[id] ? DATA.nodes[id].label : id; }
  function names(ids, max) {
    var text = ids.slice(0, max).map(name).join(', ');
    return ids.length > max ? text + ' +' + (ids.length - max) + ' more' : text;
  }

  // ------------------------------------------------------------ endpoint grouping
  // The badges (nodes ``grp:...`` and their link to the switch) are drawn hidden by the page;
  // DATA.groups says which devices each one stands for. Here they are only shown or hidden.
  var groups = {}, groupOf = {};
  (DATA.groups || []).forEach(function (g) {
    g.expanded = false;
    g.edgeIds = Object.keys(DATA.links).filter(function (eid) {    // the member-to-switch links
      var k = DATA.links[eid];
      return (k.from === g.sw && g.members.indexOf(k.to) >= 0) || (k.to === g.sw && g.members.indexOf(k.from) >= 0);
    });
    groups[g.id] = g;
    g.members.forEach(function (m) { groupOf[m] = g.id; });
  });
  var groupIds = Object.keys(groups);
  var box = document.getElementById('lldp-group-toggle'), boxLabel = document.getElementById('lldp-group-label');
  function grouped() { return !!(box && box.checked && groupIds.length); }
  if (boxLabel) { boxLabel.hidden = !groupIds.length; }

  function applyGrouping() {
    var on = grouped(), nu = [], eu = [];
    groupIds.forEach(function (gid) {
      var g = groups[gid], collapsed = on && !g.expanded;
      g.members.forEach(function (m) { nu.push({ id: m, hidden: collapsed || timeHidden('nodes', m) }); });
      g.edgeIds.forEach(function (e) { eu.push({ id: e, hidden: collapsed || timeHidden('edges', e) }); });
      nu.push({ id: gid, hidden: !on });
      eu.push({ id: g.edge, hidden: !on });
    });
    nodesDS.update(nu);
    edgesDS.update(eu);
    if (hierarchical) { network.setOptions({ layout: { hierarchical: { enabled: true } } }); }
    refresh();
  }
  window.LLDP_REVEAL = function (id) {             // make a grouped device visible before focusing it
    var gid = groupOf[id];
    if (gid && grouped() && !groups[gid].expanded) { groups[gid].expanded = true; applyGrouping(); }
  };
  if (box) { box.addEventListener('change', applyGrouping); }
  window.LLDP_REGROUP = function () { if (groupIds.length) { applyGrouping(); } };

  function updateGroups() {
    var up = [];
    groupIds.forEach(function (gid) {
      var g = groups[gid], lost = 0, weak = 0, label = (g.expanded ? '\u2212 ' : '') + g.text;
      var gp = window.LLDP_PAL || {}, border = gp.groupBorder || '#9aa5b1', fill = g.expanded ? (gp.groupOpen || '#ffffff') : (gp.groupFill || '#eef2f6');
      g.members.forEach(function (m) {
        var s = sim.state[m];
        if (s === 'failed' || s === 'isolated') { lost++; } else if (s === 'spof' && sim.showSpof) { weak++; }
      });
      if (lost) {
        label += '\n[DISCONNECTED' + (lost < g.members.length ? ' ' + lost + '/' + g.members.length : '') + ']';
        border = RED; fill = '#fdecec';
      } else if (weak) {
        label += '\n\u26a0 SPOF (No Redundancy)' + (weak < g.members.length ? ' ' + weak + '/' + g.members.length : '');
        border = AMBER; fill = '#fff4d6';
      }
      up.push({ id: gid, label: label, color: { background: fill, border: border }, font: { size: 13, color: gp.groupInk || '#1F2933' } });
    });
    if (up.length) { nodesDS.update(up); }
  }

  // --------------------------------------------------------------------- the simulation
  var toggleBtn = document.getElementById('lldp-sim-toggle');
  var banner = document.getElementById('lldp-sim-banner');
  var countsEl = document.getElementById('lldp-sim-counts'), linesEl = document.getElementById('lldp-sim-lines');
  var spofBtn = document.getElementById('lldp-sim-spof'), pop = document.getElementById('lldp-sim-pop');

  // Impact radius: rings spread from every device taken down and every cable cut while the simulation runs.
  var pulseAt = 0, pulseTimer = null;
  function reducedMotion() { return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches); }
  function pulsing() { return sim.active && (sim.failedNodes.size > 0 || sim.failedLinks.size > 0) && !reducedMotion(); }
  function pulseLoop() {
    pulseTimer = null;
    if (!pulsing()) { network.redraw(); return; }
    pulseAt = (Date.now() % 1800) / 1800;
    network.redraw();
    pulseTimer = window.setTimeout(pulseLoop, 60);
  }
  function refresh() {
    sim.result = sim.active ? Sim.evaluate(model, sim.failedNodes, sim.failedLinks) : null;
    sim.blast = sim.active && window.LLDPOps ? window.LLDPOps.blast(model, sim.failedNodes, sim.failedLinks) : null;
    if (pulseTimer === null && pulsing()) { pulseTimer = window.setTimeout(pulseLoop, 60); }
    if (window.LLDP_RESTYLE) { window.LLDP_RESTYLE(); }          // focus mode stands down while simulating
    sim.state = {};
    if (sim.result) {
      sim.result.failed.forEach(function (id) { sim.state[id] = 'failed'; });
      sim.result.isolated.forEach(function (id) { sim.state[id] = 'isolated'; });
      sim.result.spof.forEach(function (id) { sim.state[id] = 'spof'; });
    }
    renderBanner();
    updateGroups();
    network.redraw();
  }
  function renderBanner() {
    toggleBtn.setAttribute('aria-pressed', String(sim.active));
    var top = document.getElementById('b-sim');
    if (top) { top.setAttribute('aria-pressed', String(sim.active)); }
    banner.hidden = !sim.active;
    if (!sim.active) { return; }
    var r = sim.result;
    countsEl.textContent = 'Isolated: ' + r.isolated.length + ' | SPOF: ' + r.spof.length;
    spofBtn.setAttribute('aria-pressed', String(sim.showSpof));
    while (linesEl.firstChild) { linesEl.removeChild(linesEl.firstChild); }
    function line(text) { linesEl.appendChild(el('div', 'sb-line', text)); }
    if (r.failed.length) { line('Down: ' + names(r.failed, 6)); }
    sim.failedLinks.forEach(function (eid) { line('Cable cut: ' + DATA.links[eid].a + ' \u2194 ' + DATA.links[eid].b); });
    if (r.isolated.length) { line('Blast radius: ' + names(r.isolated, 8)); }
    else if (sim.failedNodes.size || sim.failedLinks.size) { line('No device loses its path to the Core.'); }
    else { line('Click a device or a link to simulate its failure or a maintenance drain.'); }
    var bl = sim.blast;
    if (bl && bl.active) {
      if (bl.spof.length) { line('Lost redundancy (single-homed now): ' + names(bl.spof, 8)); }
      if (bl.degraded.length) { line('Fewer uplinks: ' + names(bl.degraded, 8)); }
      if (bl.rerouted.length) {
        line('Rerouted over a longer path: ' + bl.rerouted.slice(0, 6).map(function (id) { return name(id) + ' +' + bl.delta[id] + (bl.delta[id] === 1 ? ' hop' : ' hops'); }).join(', ') + (bl.rerouted.length > 6 ? ' +' + (bl.rerouted.length - 6) + ' more' : ''));
      }
    }
  }
  function flip(set, key) {
    if (set.has(key)) { set.delete(key); } else { set.add(key); }
    if (set === sim.failedNodes) { sim.drained.delete(key); }   // a restored device is no longer drained
    refresh();
  }
  function drain(id) {
    if (sim.drained.has(id)) { sim.drained.delete(id); sim.failedNodes.delete(id); }
    else { sim.drained.add(id); sim.failedNodes.add(id); }
    refresh();
  }
  function enter() {
    sim.active = true;
    if (window.LLDP_ON_SIM) { window.LLDP_ON_SIM(true); }   // simulating shows the current state, the other snapshots lock
    var detail = document.getElementById('lldp-detail');
    if (detail) { detail.hidden = true; }
    refresh();
  }
  function clear() { sim.failedNodes.clear(); sim.failedLinks.clear(); sim.drained.clear(); }
  function leave() { sim.active = false; sim.showSpof = false; clear(); closePop(); refresh(); if (window.LLDP_ON_SIM) { window.LLDP_ON_SIM(false); } }
  toggleBtn.addEventListener('click', function () { if (sim.active) { leave(); } else { enter(); } });
  spofBtn.addEventListener('click', function () { sim.showSpof = !sim.showSpof; refresh(); });
  document.getElementById('lldp-sim-reset').addEventListener('click', function () { clear(); closePop(); refresh(); });
  document.getElementById('lldp-sim-exit').addEventListener('click', leave);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { closePop(); } });
  sim.refresh = refresh;

  // ----------------------------------------------------------- click-to-fail popovers
  function closePop() { pop.hidden = true; }
  function openPop(at, title, actions) {
    while (pop.firstChild) { pop.removeChild(pop.firstChild); }
    pop.appendChild(el('div', 'sp-title', title));
    var first = null;
    actions.forEach(function (a) {
      var b = el('button', 'sp-action', a[0]);
      b.type = 'button';
      b.addEventListener('click', function () { closePop(); a[1](); });
      pop.appendChild(b);
      if (!first) { first = b; }
    });
    pop.hidden = false;
    var f = network.canvas.frame.getBoundingClientRect();
    pop.style.left = Math.max(8, Math.min(f.left + at.x + 10, window.innerWidth - pop.offsetWidth - 8)) + 'px';
    pop.style.top = Math.max(8, Math.min(f.top + at.y + 10, window.innerHeight - pop.offsetHeight - 8)) + 'px';
    first.focus();
  }
  network.on('click', function (p) {
    var id = p.nodes && p.nodes[0], eid = p.edges && p.edges[0];
    if (id && groups[id]) {                         // a group pill: expand / collapse it
      closePop();
      groups[id].expanded = !groups[id].expanded;
      applyGrouping();
      return;
    }
    if (!sim.active) { return; }                    // outside a simulation the details panel handles clicks
    if (id && DATA.nodes[id]) {
      var down = sim.failedNodes.has(id), drained = sim.drained.has(id);
      openPop(p.pointer.DOM, name(id), down
        ? [[drained ? 'End maintenance drain' : 'Restore device', function () { flip(sim.failedNodes, id); }]]
        : [['Simulate Switch Failure / Blast Radius', function () { flip(sim.failedNodes, id); }],
           ['Drain for maintenance (planned)', function () { drain(id); }]]);
    } else if (!id && eid && DATA.links[eid]) {
      var k = DATA.links[eid], n = (k.pairs || []).length;
      openPop(p.pointer.DOM, k.a + ' \u2194 ' + k.b,
              [[sim.failedLinks.has(eid) ? 'Restore cable' : (n > 1 ? 'Simulate Cable Cut (all ' + n + ' links)' : 'Simulate Cable Cut'),
                function () { flip(sim.failedLinks, eid); }]]);
    } else { closePop(); }
  });

  // ----------------------------------------------------------------- canvas overlays
  function edgePoints(eid) {
    var k = DATA.links[eid], edge = network.body.edges[eid], pos = network.getPositions([k.from, k.to]);
    var a = pos[k.from], b = pos[k.to], pts = [], i, t, pt;
    if (!edge || !a || !b || edge.options.hidden) { return null; }
    for (i = 0; i <= 10; i++) {
      t = i / 10;
      pt = null;
      try { pt = edge.edgeType.getPoint(t); } catch (err) { /* straight line instead */ }
      pts.push(pt && isFinite(pt.x) && isFinite(pt.y) ? pt : { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });
    }
    return pts;
  }
  function cross(ctx, x, y, r) {
    ctx.beginPath();
    ctx.moveTo(x - r, y - r); ctx.lineTo(x + r, y + r);
    ctx.moveTo(x + r, y - r); ctx.lineTo(x - r, y + r);
    ctx.stroke();
  }
  function tag(ctx, text, x, y, fill, ink, edge) {
    ctx.font = '700 11px ' + FAMILY;
    var w = ctx.measureText(text).width + 12;
    ctx.fillStyle = fill; ctx.strokeStyle = edge; ctx.lineWidth = 1;
    ctx.fillRect(x - w / 2, y - 9, w, 18);
    ctx.strokeRect(x - w / 2, y - 9, w, 18);
    ctx.fillStyle = ink;
    ctx.fillText(text, x, y + 1);
  }
  network.on('afterDrawing', function (ctx) {
    if (!sim.active) { return; }
    var pos = network.getPositions(), words = network.getScale() >= 0.35;
    ctx.save();
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.lineCap = 'round';
    ctx.strokeStyle = RED;
    ctx.lineWidth = 4;
    ctx.setLineDash([8, 6]);
    Object.keys(DATA.links).forEach(function (eid) {           // dead cables, and cables of a dead device
      var k = DATA.links[eid], cut = sim.failedLinks.has(eid);
      if (!cut && !sim.failedNodes.has(k.from) && !sim.failedNodes.has(k.to)) { return; }
      var pts = edgePoints(eid);
      if (!pts) { return; }
      ctx.strokeStyle = !cut && (sim.drained.has(k.from) || sim.drained.has(k.to)) ? AMBER : RED;
      ctx.setLineDash([8, 6]);
      ctx.beginPath();
      pts.forEach(function (q, i) { if (i) { ctx.lineTo(q.x, q.y); } else { ctx.moveTo(q.x, q.y); } });
      ctx.stroke();
      if (cut) { ctx.setLineDash([]); cross(ctx, pts[5].x, pts[5].y, 9); }
    });
    ctx.setLineDash([]);
    Object.keys(sim.state).forEach(function (id) {
      var s = sim.state[id], bn = network.body.nodes[id], p = pos[id];
      if (!bn || !p || bn.options.hidden || (s === 'spof' && !sim.showSpof)) { return; }
      var r = (bn.options.size || 22) + 5, top = p.y - r - 12;
      if (s === 'failed') {
        var drained = sim.drained.has(id);
        ctx.fillStyle = drained ? 'rgba(224,138,0,0.55)' : 'rgba(214,69,69,0.55)';
        ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, 2 * Math.PI); ctx.fill();
        ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 4; cross(ctx, p.x, p.y, r * 0.5);
        if (words) { tag(ctx, drained ? '[DRAINED]' : '[FAILED]', p.x, top, drained ? AMBER : RED, '#ffffff', drained ? AMBER : RED); }
      } else if (s === 'isolated') {
        ctx.strokeStyle = RED; ctx.lineWidth = 4;
        ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, 2 * Math.PI); ctx.stroke();
        if (words) { tag(ctx, '[DISCONNECTED]', p.x, top, '#fdecec', '#a61b1b', RED); }
      } else if (s === 'spof') {
        ctx.strokeStyle = AMBER; ctx.lineWidth = 4; ctx.setLineDash([6, 4]);
        ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, 2 * Math.PI); ctx.stroke();
        ctx.setLineDash([]);
        if (words) { tag(ctx, '\u26a0 SPOF (No Redundancy)', p.x, top, '#FFE3A3', '#6b4400', AMBER); }
      }
    });
    var bl = sim.blast;
    if (bl && bl.active) {
      function ring(x, y, radius, colour) {
        ctx.save();
        ctx.lineWidth = 3; ctx.strokeStyle = colour; ctx.setLineDash([]);
        [0, 0.5].forEach(function (o) {
          var t = (pulseAt + o) % 1;
          ctx.globalAlpha = reducedMotion() ? 0.45 : (1 - t) * 0.8;
          ctx.beginPath(); ctx.arc(x, y, radius + (reducedMotion() ? 14 : t * 46), 0, 2 * Math.PI); ctx.stroke();
        });
        ctx.restore();
      }
      sim.failedNodes.forEach(function (id) {
        var bn = network.body.nodes[id], p = pos[id];
        if (bn && p && !bn.options.hidden) { ring(p.x, p.y, (bn.options.size || 22) + 8, sim.drained.has(id) ? AMBER : RED); }
      });
      sim.failedLinks.forEach(function (eid) { var pts = edgePoints(eid); if (pts) { ring(pts[5].x, pts[5].y, 12, RED); } });
      bl.newLinks.forEach(function (eid) {                        // the way the traffic goes now
        var pts = edgePoints(eid);
        if (!pts) { return; }
        [[12, 0.22, []], [4.5, 1, [10, 7]]].forEach(function (st) {
          ctx.save();
          ctx.lineWidth = st[0]; ctx.globalAlpha = st[1]; ctx.strokeStyle = BLUE; ctx.setLineDash(st[2]);
          ctx.lineDashOffset = -pulseAt * 34;
          ctx.beginPath();
          pts.forEach(function (q, i) { if (i) { ctx.lineTo(q.x, q.y); } else { ctx.moveTo(q.x, q.y); } });
          ctx.stroke();
          ctx.restore();
        });
      });
      Object.keys(bl.state).forEach(function (id) {
        var s = bl.state[id], bn = network.body.nodes[id], p = pos[id];
        if (!bn || !p || bn.options.hidden || s === 'failed' || s === 'isolated' || (s === 'spof' && sim.showSpof)) { return; }
        var r = (bn.options.size || 22) + 5, top = p.y - r - 12;
        ctx.strokeStyle = s === 'rerouted' ? BLUE : AMBER; ctx.lineWidth = s === 'rerouted' ? 3 : 4;
        ctx.setLineDash(s === 'degraded' ? [2, 5] : s === 'spof' ? [6, 4] : []);
        ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, 2 * Math.PI); ctx.stroke();
        ctx.setLineDash([]);
        if (!words) { return; }
        if (s === 'spof') { tag(ctx, '\\u26a0 SPOF (No Redundancy)', p.x, top, '#FFE3A3', '#6b4400', AMBER); }
        else if (s === 'degraded') { tag(ctx, '\\u25bc DEGRADED ' + bl.uplinks[id][0] + '\\u2192' + bl.uplinks[id][1], p.x, top, '#FFF4D6', '#6b4400', AMBER); }
        else { tag(ctx, '\\u21c4 +' + bl.delta[id] + (bl.delta[id] === 1 ? ' hop' : ' hops'), p.x, top, '#E0ECFF', '#1d4ed8', BLUE); }
      });
    }
    ctx.restore();
  });

  window.LLDP_DRAIN = drain;
  applyGrouping();
})();
"""

# Design system, operations engine and operations UI added on top of the page above.
OPS_CSS = r"""
/* ---- one design system for the explorer: tokens, top bar, sidebar, glass surfaces, pills, tabbed inspector ---- */
:root,[data-theme="light"]{color-scheme:light;--bg:#f8fafc;--panel:#ffffff;--panel2:#f1f5f9;--line:#e2e8f0;--text:#0f172a;--muted:#64748b;--accent:#2563eb;
--crit:#ef4444;--warn:#f59e0b;--ok:#22c55e;--glass:rgba(255,255,255,.90);--glass-line:rgba(15,23,42,.10);--shadow:0 12px 32px -10px rgba(15,23,42,.28);
--canvas:#ffffff;--hover:rgba(15,23,42,.05);--strong:#0f172a;--ui:"Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
--mono:ui-monospace,"JetBrains Mono","Fira Code",Menlo,Consolas,monospace}
[data-theme="dark"]{color-scheme:dark;--bg:#0b1324;--panel:#111b2f;--panel2:#16233b;--line:#22324d;--text:#e6edf7;--muted:#8da2c0;--accent:#3b82f6;
--glass:rgba(17,27,47,.85);--glass-line:rgba(255,255,255,.08);--shadow:0 16px 40px -8px rgba(0,0,0,.45);--canvas:#0b1324;--hover:rgba(255,255,255,.06);--strong:#ffffff}
[hidden]{display:none!important}
html,body{height:100%;margin:0;background:var(--bg);color:var(--text);font:13px/1.45 var(--ui);font-variant-numeric:tabular-nums;overflow:hidden}
center{display:none}
.card{position:fixed!important;top:52px;left:340px;right:0;bottom:0;width:auto!important;height:auto!important;border:0!important;margin:0!important;background:var(--canvas)!important}
body.side-off .card{left:0}
#mynetwork{position:absolute!important;top:0;left:0;right:0;bottom:0;width:100%!important;height:100%!important;float:none!important;border:0!important;background:var(--canvas)!important}
.num,.ip{font-family:var(--mono);font-variant-numeric:tabular-nums}
#top{position:fixed;top:0;left:0;right:0;height:52px;z-index:50;display:flex;align-items:center;gap:8px;padding:0 14px;box-sizing:border-box;background:var(--panel);border-bottom:1px solid var(--line)}
#brand{display:flex;align-items:center;gap:10px;color:var(--accent)}
#brand .brand-title{color:var(--strong);font-weight:800;letter-spacing:.06em;font-size:15px}
#top .top-gap{flex:1}
#top button,#p-more button{font:inherit;border:1px solid var(--line);background:var(--panel2);color:var(--text);border-radius:8px;padding:6px 12px;cursor:pointer}
#top button:hover,#p-more button:hover{background:var(--hover)}
#top button[aria-pressed="true"],#top button[aria-expanded="true"]{border-color:var(--accent);color:var(--strong)}
#top #b-sim[aria-pressed="true"]{background:var(--warn);border-color:var(--warn);color:#1a1200;font-weight:700}
#top-search{position:relative}
#q{width:260px;box-sizing:border-box;font:inherit;color:var(--text);background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:6px 10px}
#q::placeholder{color:var(--muted)}
#top button:focus-visible,#q:focus-visible,#p-more button:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
#search-results,#p-more,#lldp-toolbar,#lldp-detail,#lldp-sim-banner,#lldp-sim-pop,#minimap,#diff-bar,#hotkeys,#toast{background:var(--glass);backdrop-filter:blur(12px) saturate(160%);-webkit-backdrop-filter:blur(12px) saturate(160%);border:1px solid var(--glass-line);box-shadow:var(--shadow);border-radius:10px;color:var(--text)}
#search-results{position:absolute;top:calc(100% + 6px);right:0;width:300px;max-height:320px;overflow:auto;padding:4px;z-index:60}
#search-results button{display:flex;gap:8px;width:100%;text-align:left;border:0;background:transparent;border-radius:7px;padding:5px 8px;color:inherit;font:inherit;cursor:pointer}
#search-results button:hover,#search-results button.cur{background:var(--hover)}
#search-results small{color:var(--muted);margin-left:auto}
#search-results .sr-none{padding:6px 8px;color:var(--muted)}
#p-more{position:fixed;top:58px;right:14px;width:290px;padding:10px 12px;z-index:60;box-sizing:border-box}
#p-more h3{margin:0 0 6px;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
#p-more p{margin:6px 0;color:var(--muted);font-size:12px}
#p-more button{display:block;width:100%;text-align:left;margin:4px 0}
#side{position:fixed;top:52px;left:0;bottom:0;width:340px;z-index:20;overflow:auto;box-sizing:border-box;padding:12px;background:var(--panel);border-right:1px solid var(--line)}
body.side-off #side{display:none}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin:0 0 8px}
.metric{background:var(--panel2);border:1px solid var(--line);border-radius:9px;padding:7px 8px}
.metric b{display:block;font-size:17px;color:var(--strong)}
.metric span{font-size:10px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted)}
.mrow{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 12px}
.empty{color:var(--muted);background:var(--panel2);border:1px dashed var(--line);border-radius:9px;padding:10px 12px;margin-bottom:12px;font-size:12px}
.empty kbd{font-family:var(--mono);color:var(--text);font-weight:700}
.pill{display:inline-flex;align-items:center;gap:5px;padding:1px 9px;border-radius:99px;border:1px solid;font-size:11px;font-weight:700;letter-spacing:.03em;white-space:nowrap;line-height:1.5}
.pill.up{background:rgba(34,197,94,.12);border-color:#22c55e;color:#4ade80}
.pill.warn{background:rgba(245,158,11,.12);border-color:#f59e0b;color:#fbbf24}
.pill.down{background:rgba(239,68,68,.15);border-color:#ef4444;color:#f87171}
.pill.info{background:rgba(59,130,246,.14);border-color:#3b82f6;color:#93c5fd}
[data-theme="light"] .pill.up{color:#15803d}[data-theme="light"] .pill.warn{color:#b45309}[data-theme="light"] .pill.down{color:#b91c1c}[data-theme="light"] .pill.info{color:#1d4ed8}
/* the legend lives in the sidebar */
#lldp-legend{position:static!important;width:auto!important;max-height:none!important;padding:0!important;border:0!important;background:none!important;box-shadow:none!important;color:var(--text);font:inherit}
#lldp-legend h1{font-size:13px;color:var(--strong)}
#lldp-legend p,#lldp-legend .hint{color:var(--muted)}
details.group{border-top:1px solid var(--line);padding:6px 0}
details.group>summary{cursor:pointer;font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);font-weight:700;padding:2px 0}
#lldp-legend .lg-item:hover{background:var(--hover)}
#lldp-legend .lg-item[aria-pressed="true"]{background:rgba(59,130,246,.16);border-color:var(--accent)}
#lldp-legend #lldp-legend-toggle{background:var(--panel2);border-color:var(--line);color:var(--muted)}
#lldp-legend .tier-key{background:var(--line)}
#lldp-problems .pb-name{color:#ef4444}
#lldp-problems .pb-why,#lldp-blindspots .pb-why{color:var(--muted)}
[data-theme="dark"] #lldp-warnings .sev-degraded,[data-theme="dark"] #lldp-blindspots .pb-name{color:#fbbf24}
[data-theme="dark"] #lldp-warnings .sev-mismatch{color:#fb923c}
[data-theme="dark"] #lldp-problems .pb-name{color:#f87171}
/* tabbed inspector: Overview | Interfaces & Peers | CLI Diagnostics */
#lldp-detail{top:64px;right:14px;width:min(380px,calc(100vw - 28px));max-height:calc(100vh - 80px);padding:0;font:13px/1.45 var(--ui);z-index:30}
#lldp-detail .ld-head{padding:12px 14px 2px}
#lldp-detail .ld-badge{margin:0 14px 6px}
#lldp-detail h2{color:var(--strong)}
#lldp-detail h3,#lldp-detail dt,#lldp-detail .ld-state,#lldp-detail .ld-close{color:var(--muted)}
#lldp-detail li{border-top-color:var(--line)}
#lldp-detail .ld-text{background:var(--panel2)}
#lldp-detail .ld-err{color:#ef4444;background:rgba(239,68,68,.12)}
#lldp-detail .ld-peer{color:var(--accent)}
#lldp-detail .ld-tag{color:var(--accent)}
#lldp-detail .ld-port{font-family:var(--mono)}
.ld-tabs{display:flex;border-bottom:1px solid var(--line);padding:0 8px}
.ld-tab{border:0;border-bottom:2px solid transparent;border-radius:0;background:transparent;padding:6px 8px;font:inherit;font-size:12px;color:var(--muted);cursor:pointer}
.ld-tab.on{color:var(--strong);border-bottom-color:var(--accent);font-weight:700}
.ld-tab:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
.ld-body{padding:10px 14px 14px}
.ld-filter{width:100%;box-sizing:border-box;font:inherit;color:var(--text);background:var(--panel2);border:1px solid var(--line);border-radius:6px;padding:4px 8px;margin:0 0 6px}
.cli{margin:8px 0;border:1px solid var(--line);border-radius:8px;background:var(--panel2)}
.cli-head{display:flex;align-items:center;gap:8px;padding:5px 8px;color:var(--muted)}
.cli-head span{flex:1;word-break:break-word}
.cli-head button,.cli-all{font:inherit;font-size:11px;border:1px solid var(--line);background:var(--panel);color:var(--text);border-radius:6px;padding:1px 8px;cursor:pointer}
.cli pre{margin:0;padding:6px 9px 8px;font:11.5px/1.55 var(--mono);white-space:pre-wrap;word-break:break-all;color:var(--text)}
.cli-note{color:var(--muted);margin:8px 0 0;font-size:12px}
/* filter panel (the old toolbar), simulation banner and popover */
#lldp-toolbar{position:fixed;top:58px;right:14px;left:auto;transform:none;flex-direction:column;align-items:flex-start;gap:8px;padding:10px 12px;z-index:55}
#lldp-toolbar #lldp-sim-toggle{display:none}
#lldp-toolbar button{background:var(--panel2);border-color:var(--line);color:var(--text)}
#lldp-sim-banner{top:60px;z-index:45}
[data-theme="dark"] #lldp-sim-banner{background:rgba(245,158,11,.16);color:#fde68a;border-color:#b45309}
#lldp-sim-banner button,#lldp-sim-pop button{background:var(--panel2);border-color:var(--line);color:var(--text)}
#lldp-sim-banner button[aria-pressed="true"]{background:var(--warn);color:#1a1200}
#lldp-sim-pop{z-index:65}
/* snapshot bar, minimap, hotkey legend, toast */
#diff-bar{position:fixed;z-index:22;left:354px;bottom:50px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:6px 10px;max-width:calc(100vw - 580px)}
body.side-off #diff-bar{left:14px}
#diff-bar .seg{display:flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
#diff-bar .seg button{border:0;border-radius:0;background:transparent;color:var(--text);padding:4px 11px;font:inherit;font-size:12px;cursor:pointer}
#diff-bar .seg button.on{background:var(--hover);color:var(--strong);font-weight:700}
#diff-bar .seg button[disabled]{opacity:.4;cursor:not-allowed}
#diff-sum{color:var(--muted);font-size:12px}
#diff-bar .key i{display:inline-block;width:14px;height:0;border-top:3px solid;margin:0 4px 2px 8px;vertical-align:middle}
#diff-bar .key i.k-add{border-top-color:#22c55e}#diff-bar .key i.k-rm{border-top-color:#ef4444;border-top-style:dashed}#diff-bar .key i.k-ch{border-top-color:#f59e0b}
#minimap{position:fixed;z-index:22;right:14px;bottom:44px;width:160px;height:100px;padding:0;border:0;outline:1px solid var(--glass-line);outline-offset:-1px;cursor:crosshair;touch-action:none}
#hotkeys{position:fixed;z-index:22;right:14px;bottom:12px;padding:5px 10px;font:11px/1.3 var(--mono);color:var(--muted);white-space:nowrap}
#hotkeys .hk{color:var(--text);font-weight:700}
#toast{position:fixed;left:50%;bottom:104px;transform:translateX(-50%);z-index:80;padding:6px 14px;border-radius:99px;font-size:12px}
div.vis-tooltip{background:var(--panel)!important;color:var(--text)!important;border:1px solid var(--line)!important;font-family:var(--ui)!important}
div.vis-network div.vis-navigation div.vis-button.vis-zoomIn,div.vis-network div.vis-navigation div.vis-button.vis-zoomOut,div.vis-network div.vis-navigation div.vis-button.vis-zoomExtends{margin-right:170px}
@media print{#top,#side,#minimap,#hotkeys,#diff-bar,#toast,#p-more,#lldp-toolbar{display:none!important}.card{position:static!important}}
"""

# Blast radius, CLI runbook and spec generator: pure, so Node can run them (tests/test_simulation.py).
OPS_ENGINE_JS = r"""
(function (root) {
  'use strict';

  // ======================================================== blast radius (red / amber / blue)
  // Pure functions over the model made by LLDPSim.build; they need no DOM, so Node can run them (tests/test_simulation.py).

  // Hops from the live Core devices, and the links that lie on a shortest path.
  function reach(model, failedNodes, failedLinks) {
    var nodes = model.nodes, adj = {}, dist = {}, used = {}, queue = [];
    function alive(id) { return !failedNodes.has(id); }
    var live = model.edges.filter(function (e) { return !failedLinks.has(e.id) && alive(e.from) && alive(e.to); });
    live.forEach(function (e) {
      (adj[e.from] = adj[e.from] || []).push(e.to);
      (adj[e.to] = adj[e.to] || []).push(e.from);
    });
    model.roots.forEach(function (id) { if (alive(id)) { dist[id] = 0; queue.push(id); } });
    for (var i = 0; i < queue.length; i++) {
      var u = queue[i];
      if (nodes[u].leaf) { continue; }                    // an end device is reached, never a path through
      (adj[u] || []).forEach(function (next) { if (dist[next] === undefined) { dist[next] = dist[u] + 1; queue.push(next); } });
    }
    live.forEach(function (e) {
      var a = dist[e.from], b = dist[e.to];
      if (a !== undefined && b !== undefined && Math.abs(a - b) === 1) { used[e.id] = true; }
    });
    return { dist: dist, used: used, live: live };
  }

  // Working cables towards a higher, connected tier (the same count the simulation uses for its SPOF rule).
  function uplinks(model, r) {
    var up = {};
    r.live.forEach(function (e) {
      if (r.dist[e.from] === undefined || r.dist[e.to] === undefined) { return; }
      var a = model.nodes[e.from].rank, b = model.nodes[e.to].rank;
      if (a < b) { up[e.to] = (up[e.to] || 0) + e.count; } else if (b < a) { up[e.from] = (up[e.from] || 0) + e.count; }
    });
    return up;
  }

  // What the failed devices and cables (or the drained ones) do to everything else:
  //   isolated  - lost every path to the Core (red)
  //   spof      - still connected, but down to ONE uplink, which it was not before (amber)
  //   degraded  - fewer uplinks than before, still two or more (amber)
  //   rerouted  - still connected, but more hops from the Core than before (blue); newLinks carry the traffic now
  // state[id] holds the worst of these for a device: failed > isolated > spof > degraded > rerouted.
  function blast(model, failedNodes, failedLinks) {
    var Sim = root.LLDPSim, none = new Set();
    var out = { active: failedNodes.size + failedLinks.size > 0, failed: [], isolated: [], spof: [], degraded: [], rerouted: [],
                delta: {}, uplinks: {}, newLinks: [], lostLinks: [], state: {} };
    if (!out.active || !Sim) { return out; }
    var before = Sim.evaluate(model, none, none), after = Sim.evaluate(model, failedNodes, failedLinks);
    var r0 = reach(model, none, none), r1 = reach(model, failedNodes, failedLinks), u0 = uplinks(model, r0), u1 = uplinks(model, r1);
    var spof0 = {};
    before.spof.forEach(function (id) { spof0[id] = true; });
    out.failed = after.failed; out.isolated = after.isolated;
    after.failed.forEach(function (id) { out.state[id] = 'failed'; });
    after.isolated.forEach(function (id) { out.state[id] = 'isolated'; });
    after.active.forEach(function (id) {
      var n = model.nodes[id], a = u0[id] || 0, c = u1[id] || 0;
      if (after.spof.indexOf(id) >= 0 && !spof0[id]) { out.spof.push(id); out.state[id] = 'spof'; out.uplinks[id] = [a, c]; }
      else if (n.spof_ok && !model.isRoot[id] && c >= 2 && c < a) { out.degraded.push(id); out.state[id] = 'degraded'; out.uplinks[id] = [a, c]; }
      var h0 = r0.dist[id], h1 = r1.dist[id];
      if (h0 !== undefined && h1 !== undefined && h1 > h0) {
        out.delta[id] = h1 - h0;
        if (!out.state[id]) { out.rerouted.push(id); out.state[id] = 'rerouted'; }
      }
    });
    Object.keys(r1.used).forEach(function (l) { if (!r0.used[l]) { out.newLinks.push(l); } });
    Object.keys(r0.used).forEach(function (l) { if (!r1.used[l] && !failedLinks.has(l)) { out.lostLinks.push(l); } });
    return out;
  }

  // ======================================================== multi-vendor CLI runbook (pure)
  var VENDORS = [['cisco_ios', 'Cisco IOS'], ['cisco_nxos', 'Cisco NX-OS'], ['arista_eos', 'Arista EOS'], ['juniper_junos', 'Juniper Junos'], ['vyos', 'VyOS'], ['huawei', 'Huawei VRP']];
  function vendorName(key) { var v = VENDORS.filter(function (x) { return x[0] === key; })[0]; return v ? v[1] : ''; }
  function vendorOf(node) {
    var t = [node.device_type, node.os, node.platform].join(' ').toLowerCase();
    if (/nx-?os/.test(t)) { return 'cisco_nxos'; }
    if (/arista|\beos\b/.test(t)) { return 'arista_eos'; }
    if (/junos|juniper/.test(t)) { return 'juniper_junos'; }
    if (/vyos|vyatta/.test(t)) { return 'vyos'; }
    if (/huawei|vrp/.test(t)) { return 'huawei'; }
    if (/cisco|\bios\b/.test(t)) { return 'cisco_ios'; }
    return null;
  }
  // {if} = the port, {bundle} = the port-channel, {lagnum} = its number. Standard L2 show commands of each platform;
  // check the exact syntax against your software version.
  var TABLE = {
    cisco_ios: { nbr: 'show lldp neighbors detail', nbrIf: 'show lldp neighbors {if} detail', ifs: 'show interfaces status', ifOne: 'show interfaces {if}',
      lag: 'show etherchannel summary', lagIf: 'show interfaces {bundle}' },
    cisco_nxos: { nbr: 'show lldp neighbors detail', nbrIf: 'show lldp neighbors interface {if} detail', ifs: 'show interface status', ifOne: 'show interface {if}',
      lag: 'show port-channel summary', lagIf: 'show interface {bundle}' },
    arista_eos: { nbr: 'show lldp neighbors detail', nbrIf: 'show lldp neighbors {if} detail', ifs: 'show interfaces status', ifOne: 'show interfaces {if}',
      lag: 'show port-channel summary', lagIf: 'show lacp interface {if}' },
    juniper_junos: { nbr: 'show lldp neighbors', nbrIf: 'show lldp neighbors interface {if}', ifs: 'show interfaces terse', ifOne: 'show interfaces {if} extensive',
      lag: 'show lacp interfaces', lagIf: 'show lacp interfaces {bundle}' },
    vyos: { nbr: 'show lldp neighbors detail', nbrIf: 'show lldp neighbors interface {if}', ifs: 'show interfaces', ifOne: 'show interfaces ethernet {if}',
      lag: 'show interfaces bonding', lagIf: 'show interfaces bonding {bundle}' },
    huawei: { nbr: 'display lldp neighbor verbose', nbrIf: 'display lldp neighbor interface {if} verbose', ifs: 'display interface brief', ifOne: 'display interface {if}',
      lag: 'display eth-trunk', lagIf: 'display eth-trunk {lagnum}' }
  };
  function fill(cmd, o) {
    return cmd.replace('{if}', (o && o.iface) || '<intf>').replace('{bundle}', (o && o.bundle) || '<port-channel>')
              .replace('{lagnum}', ((String((o && o.bundle) || '').match(/\d+/)) || ['<id>'])[0]);
  }
  function pick(choice, detected) { return TABLE[choice] ? choice : (detected && TABLE[detected] ? detected : 'cisco_ios'); }
  // choice: a platform key, or anything else for "auto" (each device in its own platform).
  // ctx: {kind: 'node', label, vendor, hasBundles} | {kind: 'link', bundled, ends: [{label, vendor, iface, bundle}]}  ->  [{title, vendor, cmds}]
  function runbook(choice, ctx) {
    var blocks = [];
    if (ctx.kind === 'link') {
      (ctx.ends || []).forEach(function (e) {
        var v = pick(choice, e.vendor), t = TABLE[v];
        blocks.push({ title: 'LLDP link on ' + e.label + (e.iface ? ' (' + e.iface + ')' : '') + ' \u00b7 ' + vendorName(v), vendor: v, cmds: [fill(t.nbrIf, e), fill(t.ifOne, e)] });
        if (ctx.bundled) { blocks.push({ title: 'Port-channel ' + (e.bundle || '') + ' on ' + e.label, vendor: v, cmds: [t.lag, fill(t.lagIf, e)] }); }
      });
    } else {
      var v = pick(choice, ctx.vendor), t = TABLE[v];
      blocks.push({ title: 'LLDP neighbours of ' + ctx.label + ' \u00b7 ' + vendorName(v), vendor: v, cmds: [t.nbr] });
      blocks.push({ title: 'Interface status', vendor: v, cmds: [t.ifs] });
      if (ctx.hasBundles) { blocks.push({ title: 'Port-channels', vendor: v, cmds: [t.lag] }); }
    }
    return blocks;
  }

  // ======================================================== documentation spec (pure): TOPOLOGY_SPEC.md
  function cell(v) { return String(v === undefined || v === null || v === '' ? '\u2014' : v).replace(/\|/g, '\\|').replace(/\s*[\r\n]+\s*/g, ' '); }
  function software(n) {
    var m = /version\s+([A-Za-z0-9().\-]+)/i.exec(n.description || '');
    return ((n.os || '') + (m ? ' ' + m[1] : '')).trim();
  }
  // opts.time: 'current' (what is there now) or 'previous' (what was there at the previous run).
  function buildSpec(data, opts) {
    var time = opts && opts.time === 'previous' ? 'previous' : 'current', diff = data.diff || {}, baseline = !!diff.baseline;
    function nodeStatus(id) { return (diff.nodes && diff.nodes[id] && diff.nodes[id].status) || ''; }
    function shownNode(id) { return time === 'previous' ? nodeStatus(id) !== 'added' : !data.nodes[id].ghost; }
    function shownLink(k) { return time === 'previous' ? !(k.diff && k.diff.status === 'added') : !k.ghost; }
    var ids = Object.keys(data.nodes).filter(shownNode).sort(function (a, b) {
      var x = data.nodes[a], y = data.nodes[b];
      return x.rank !== y.rank ? x.rank - y.rank : (x.label < y.label ? -1 : x.label > y.label ? 1 : 0);
    });
    var shown = {};
    ids.forEach(function (id) { shown[id] = data.nodes[id]; });
    var eids = Object.keys(data.links).filter(function (eid) {
      var k = data.links[eid];
      return shownLink(k) && shown[k.from] && shown[k.to];
    });
    function labelOf(id) { return data.nodes[id] ? data.nodes[id].label : id; }
    function isBridge(eid) {                       // the only way between the two sides, with no bundle or parallel cable
      var k = data.links[eid];
      if (k.bundle || (k.pairs || []).length > 1) { return false; }
      var adj = {};
      eids.forEach(function (o) { if (o !== eid) { (adj[data.links[o].from] = adj[data.links[o].from] || []).push(data.links[o].to); (adj[data.links[o].to] = adj[data.links[o].to] || []).push(data.links[o].from); } });
      var seen = {}, queue = [k.from];
      seen[k.from] = true;
      for (var i = 0; i < queue.length; i++) {
        if (queue[i] === k.to) { return false; }
        (adj[queue[i]] || []).forEach(function (n) { if (!seen[n]) { seen[n] = true; queue.push(n); } });
      }
      return true;
    }
    var meta = data.meta || {}, out = ['# Topology specification', '',
      '- **Snapshot:** ' + (time === 'previous' ? 'previous run (devices and links added since are left out)' : 'current state (devices and links removed since the previous run are left out)'),
      '- **Generated:** ' + cell(meta.generated) + (meta.seed ? ' from seed ' + cell(meta.seed) : ''),
      '- **Size:** ' + ids.length + ' devices, ' + eids.length + ' links', '',
      '## Devices', '', '| Hostname | IP | Role / Tier | Vendor | Software version |', '| --- | --- | --- | --- | --- |'];
    ids.forEach(function (id) {
      var n = data.nodes[id], v = vendorOf(n);
      out.push('| ' + [n.label, n.address || (n.mgmt_ips || [])[0], (n.tier ? n.tier + ' \u00b7 ' : '') + n.kind, v ? vendorName(v) : n.device_type, software(n)].map(cell).join(' | ') + ' |');
    });
    out.push('', '## Link interconnect matrix', '', '| Local device | Local interface | Remote device | Remote interface | Speed | Redundancy' + (baseline ? ' | Change' : '') + ' |',
             '| --- | --- | --- | --- | --- | --- |' + (baseline ? ' --- |' : ''));
    var rows = [];
    eids.forEach(function (eid) {
      var k = data.links[eid], n = (k.pairs || []).length, b = k.bundle, red;
      if (b) { red = 'LAG ' + b.protocol + ': ' + b.links + (b.total > b.links ? '/' + b.total : '') + ' members' + (b.up ? '' : ' (down)') + (b.links > 1 ? ', redundant' : ', single member'); }
      else if (n > 1) { red = 'Parallel \u00d7' + n + ' (redundant)'; }
      else { red = isBridge(eid) ? 'Single link (SPOF)' : 'Redundant (alternate path exists)'; }
      if (k.warn) { red += ' \u00b7 ' + k.warn; }
      (k.pairs && k.pairs.length ? k.pairs : [['', '']]).forEach(function (p) {
        rows.push([labelOf(k.from), p[0], labelOf(k.to), p[1], k.speed, red].concat(baseline ? [k.diff ? k.diff.status + ': ' + k.diff.detail : ''] : []));
      });
    });
    rows.sort(function (a, b) { return a[0] === b[0] ? (a[2] < b[2] ? -1 : a[2] > b[2] ? 1 : 0) : (a[0] < b[0] ? -1 : 1); });
    rows.forEach(function (r) { out.push('| ' + r.map(cell).join(' | ') + ' |'); });
    if (baseline) {
      var s = diff.summary || {}, added = Object.keys(diff.nodes || {}).filter(function (id) { return diff.nodes[id].status === 'added'; }),
          gone = Object.keys(diff.nodes || {}).filter(function (id) { return diff.nodes[id].status === 'removed'; });
      out.push('', '## Changes since the previous snapshot', '');
      out.push('- Added devices: ' + (added.map(labelOf).join(', ') || 'none'));
      out.push('- Removed devices: ' + (gone.map(labelOf).join(', ') || 'none'));
      out.push('- Links: +' + (s.links_added || 0) + ' / \u2212' + (s.links_removed || 0) + ' / changed ' + (s.links_changed || 0));
    }
    return out.join('\n') + '\n';
  }

  root.LLDPOps = { blast: blast, runbook: runbook, vendorOf: vendorOf, vendorName: vendorName, VENDORS: VENDORS, TABLE: TABLE, buildSpec: buildSpec };
})(typeof window !== 'undefined' ? window : globalThis);
"""

# Theme, search, tabbed-inspector CLI tab, snapshot time travel, minimap, documentation export and hotkeys.
OPS_UI_JS = r"""
(function () {
  'use strict';
  var DATA = window.LLDP_DETAIL, Ops = window.LLDPOps;
  if (!DATA || !Ops || typeof network === 'undefined') { return; }
  var FAMILY = '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
  var MONO = 'ui-monospace, "JetBrains Mono", "Fira Code", Menlo, Consolas, monospace';
  var GREEN = '#22c55e', RED = '#ef4444', ORANGE = '#f59e0b';
  var nodesDS = network.body.data.nodes, edgesDS = network.body.data.edges;
  var STYLE = window.LLDP_STYLE || {};

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text !== undefined && text !== null) { e.textContent = text; }
    return e;
  }
  function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }
  function typing(t) { return !!t && /INPUT|TEXTAREA|SELECT/.test(t.tagName || ''); }
  function reduced() { return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches); }
  function store(key, value) { try { if (value === undefined) { return window.localStorage.getItem(key); } window.localStorage.setItem(key, value); } catch (e) { /* storage blocked */ } return null; }

  var toastBox = el('div', null);
  toastBox.id = 'toast'; toastBox.setAttribute('role', 'status'); toastBox.hidden = true;
  document.body.appendChild(toastBox);
  var toastTimer = null;
  function toast(text) {
    toastBox.textContent = text; toastBox.hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () { toastBox.hidden = true; }, 2600);
  }

  // ================================================================== theme (dark / light, kept in localStorage)
  var lightBg = STYLE.bg || '#ffffff';
  var PALS = {
    light: { bg: lightBg, fg: STYLE.fg || '#323f4b', muted: '#6b7785', pill: 'rgba(255,255,255,0.82)', band: 'rgba(31,41,51,0.045)', tier: '#7b8794',
             edgeText: '#343434', edgeDim: 'rgba(52,52,52,0.15)', mark: '#ffffff', groupFill: '#eef2f6', groupOpen: '#ffffff', groupBorder: '#9aa5b1', groupInk: '#1F2933' },
    dark: { bg: '#0b1324', fg: '#e6edf7', muted: '#8da2c0', pill: 'rgba(17,27,47,0.82)', band: 'rgba(255,255,255,0.045)', tier: '#8da2c0',
            edgeText: '#cbd5e1', edgeDim: 'rgba(203,213,225,0.15)', mark: '#0b1324', groupFill: '#16233b', groupOpen: '#111b2f', groupBorder: '#3b4f73', groupInk: '#e6edf7' }
  };
  var theme = 'light';
  function applyTheme(mode) {
    theme = mode === 'dark' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', theme);
    document.documentElement.style.setProperty('--canvas', PALS[theme].bg);
    window.LLDP_PAL = PALS[theme];
    store('lldp-explorer:theme', theme);
    var b = $('b-theme');
    if (b) { b.setAttribute('aria-label', 'Theme: ' + theme + ' (click for ' + (theme === 'dark' ? 'light' : 'dark') + ')'); b.textContent = theme === 'dark' ? '\u263e' : '\u2600'; }
    if (window.LLDP_SIM && window.LLDP_SIM.refresh) { window.LLDP_SIM.refresh(); } else if (window.LLDP_RESTYLE) { window.LLDP_RESTYLE(); }
    network.redraw();
  }
  var saved = store('lldp-explorer:theme');
  applyTheme(saved === 'dark' || saved === 'light' ? saved : (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
  $('b-theme').addEventListener('click', function () { applyTheme(theme === 'dark' ? 'light' : 'dark'); });

  // ================================================================== shell: sidebar, filter panel, fullscreen, export menu
  function resize() { window.dispatchEvent(new Event('resize')); network.setSize('100%', '100%'); network.redraw(); scheduleMinimap(); }
  $('b-side').addEventListener('click', function () {
    var off = document.body.classList.toggle('side-off');
    $('b-side').setAttribute('aria-pressed', String(!off));
    resize();
  });
  function toggle(panelId, buttonId) {
    var panel = $(panelId), open = panel.hidden;
    panel.hidden = !open;
    $(buttonId).setAttribute('aria-expanded', String(open));
  }
  $('b-filter').addEventListener('click', function () { toggle('lldp-toolbar', 'b-filter'); });
  $('b-more').addEventListener('click', function () { toggle('p-more', 'b-more'); });
  $('b-sim').addEventListener('click', function () { $('lldp-sim-toggle').click(); });
  $('b-full').addEventListener('click', function () {
    try {
      if (document.fullscreenElement) { document.exitFullscreen(); } else { document.documentElement.requestFullscreen().catch(function () { toast('Full screen was refused by the browser'); }); }
    } catch (e) { toast('Full screen is not available here'); }
  });
  document.addEventListener('fullscreenchange', function () { window.setTimeout(resize, 50); });
  document.addEventListener('click', function (e) {
    var menu = $('p-more');
    if (!menu.hidden && !menu.contains(e.target) && e.target !== $('b-more')) { menu.hidden = true; $('b-more').setAttribute('aria-expanded', 'false'); }
  });
  var egoBox = $('f-ego'), ifBox = $('f-ifaces');
  window.LLDP_EGO_ON = !egoBox || egoBox.checked;
  if (egoBox) { egoBox.addEventListener('change', function () { window.LLDP_EGO_ON = egoBox.checked; if (window.LLDP_RESTYLE) { window.LLDP_RESTYLE(); } }); }
  window.LLDP_IFACES_ON = !!(ifBox && ifBox.checked);
  if (ifBox) { ifBox.addEventListener('change', function () { window.LLDP_IFACES_ON = ifBox.checked; network.redraw(); }); }

  // ================================================================== search: results list, network.focus on the pick
  var q = $('q'), box = $('search-results'), hits = [], at = -1;
  function haystack(id) {
    var n = DATA.nodes[id];
    return [n.label, n.address, (n.mgmt_ips || []).join(' '), n.chassis_id, n.kind, n.tier, n.os, n.platform,
            (n.links || []).map(function (l) { return l.local + ' ' + l.remote + ' ' + l.peer_label; }).join(' ')].join(' ').toLowerCase();
  }
  function search(text) {
    var t = text.trim().toLowerCase();
    if (!t) { return []; }
    function rank(id) {                           // its own name beats its address beats what its links mention
      var n = DATA.nodes[id], l = n.label.toLowerCase();
      if (l === t) { return 0; }
      if (l.indexOf(t) === 0) { return 1; }
      if (l.indexOf(t) >= 0) { return 2; }
      return [n.address, (n.mgmt_ips || []).join(' '), n.chassis_id].join(' ').toLowerCase().indexOf(t) >= 0 ? 3 : 4;
    }
    return Object.keys(DATA.nodes).filter(function (id) { return !DATA.nodes[id].ghost || mode !== 'current'; })
      .filter(function (id) { return haystack(id).indexOf(t) >= 0; })
      .sort(function (a, b) { return rank(a) - rank(b) || (DATA.nodes[a].label < DATA.nodes[b].label ? -1 : 1); });
  }
  function closeResults() { box.hidden = true; at = -1; q.setAttribute('aria-expanded', 'false'); }
  function go(id) { closeResults(); q.value = ''; q.blur(); if (window.LLDP_FOCUS) { window.LLDP_FOCUS(id); } }
  function mark(i) {
    at = i;
    Array.prototype.forEach.call(box.children, function (b, j) { b.classList.toggle('cur', j === i); });
  }
  function showResults() {
    var all = search(q.value);
    hits = all.slice(0, 8);
    clear(box);
    if (!hits.length) { if (q.value.trim()) { box.appendChild(el('div', 'sr-none', 'No device matches')); box.hidden = false; } else { closeResults(); } return; }
    hits.forEach(function (id) {
      var n = DATA.nodes[id], b = el('button', null);
      b.type = 'button'; b.setAttribute('role', 'option');
      b.appendChild(el('b', null, n.label));
      b.appendChild(el('small', null, [n.tier, n.address || (n.mgmt_ips || [])[0]].filter(Boolean).join(' \u00b7 ')));
      b.addEventListener('mousedown', function (e) { e.preventDefault(); });
      b.addEventListener('click', function () { go(id); });
      box.appendChild(b);
    });
    if (all.length > hits.length) { box.appendChild(el('div', 'sr-none', '+' + (all.length - hits.length) + ' more: keep typing')); }
    box.hidden = false; q.setAttribute('aria-expanded', 'true');
  }
  q.addEventListener('input', showResults);
  q.addEventListener('blur', closeResults);
  q.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowDown' && hits.length) { e.preventDefault(); mark((at + 1) % hits.length); }
    else if (e.key === 'ArrowUp' && hits.length) { e.preventDefault(); mark((at + hits.length - 1) % hits.length); }
    else if (e.key === 'Enter') { if (hits.length) { go(hits[Math.max(at, 0)]); } else { toast('No device matches'); } }
    else if (e.key === 'Escape') { q.value = ''; closeResults(); q.blur(); }
  });
  $('b-search').addEventListener('click', function () { q.focus(); q.select(); });

  // ================================================================== CLI Diagnostics tab of the inspector
  function copyText(text, button) {
    function done(ok) {
      if (button) { var old = button.textContent; button.textContent = ok ? 'Copied' : 'Press Ctrl+C'; window.setTimeout(function () { button.textContent = old; }, 1400); }
      toast(ok ? 'Copied to the clipboard' : 'Could not copy: select the text and press Ctrl+C');
    }
    function fallback() {
      var ta = el('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      var ok = false; try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
      document.body.removeChild(ta); done(ok);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(text).then(function () { done(true); }, fallback); } else { fallback(); }
  }
  function runbookFor(choice, ctx) {
    if (ctx.kind === 'node') {
      var n = DATA.nodes[ctx.id];
      return Ops.runbook(choice, { kind: 'node', label: n.label, vendor: Ops.vendorOf(n), hasBundles: (n.port_channels || []).length > 0 });
    }
    var k = DATA.links[ctx.id], first = (k.pairs || [])[0] || ['', ''], b = k.bundle || {}, extra = (k.pairs || []).length > 1 && !k.bundle ? ' +' + ((k.pairs || []).length - 1) + ' more' : '';
    return Ops.runbook(choice, { kind: 'link', bundled: !!k.bundle, ends: [
      { label: k.a, vendor: Ops.vendorOf(DATA.nodes[k.from] || {}), iface: first[0] ? first[0] + extra : '', bundle: b.a },
      { label: k.b, vendor: Ops.vendorOf(DATA.nodes[k.to] || {}), iface: first[1] ? first[1] + extra : '', bundle: b.b }] });
  }
  window.LLDP_CLI = function (body, ctx) {
    function draw() {
      clear(body);
      var choice = store('lldp-explorer:vendor') || 'auto';
      var sel = el('select', 'ld-filter');
      sel.setAttribute('aria-label', 'Platform');
      var auto = el('option', null, 'Auto: each device on its own platform'); auto.value = 'auto'; sel.appendChild(auto);
      Ops.VENDORS.forEach(function (v) { var o = el('option', null, v[1]); o.value = v[0]; sel.appendChild(o); });
      sel.value = choice;
      sel.addEventListener('change', function () { store('lldp-explorer:vendor', sel.value); draw(); });
      body.appendChild(sel);
      var blocks = runbookFor(choice, ctx);
      blocks.forEach(function (b) {
        var card = el('div', 'cli'), head = el('div', 'cli-head'), btn = el('button', null, 'Copy CLI');
        btn.type = 'button';
        btn.addEventListener('click', function () { copyText(b.cmds.join('\n'), btn); });
        head.appendChild(el('span', null, b.title)); head.appendChild(btn);
        card.appendChild(head); card.appendChild(el('pre', null, b.cmds.join('\n')));
        body.appendChild(card);
      });
      if (blocks.length > 1) {
        var all = el('button', 'cli-all', 'Copy all'); all.type = 'button';
        all.addEventListener('click', function () { copyText(blocks.map(function (b) { return '# ' + b.title + '\n' + b.cmds.join('\n'); }).join('\n\n'), all); });
        body.appendChild(all);
      }
      body.appendChild(el('p', 'cli-note', 'Read-only show commands in each platform\u2019s standard syntax: check them against your software version.'));
    }
    draw();
  };

  // ================================================================== snapshot time travel (current / previous / diff overlay)
  var diff = DATA.diff || {}, has = !!diff.baseline, mode = has ? 'overlay' : 'current';
  var H = window.LLDP_TIMEHIDE = { nodes: {}, edges: {} };
  var memberOf = {}, groupEdge = {};
  (DATA.groups || []).forEach(function (g) {
    g.members.forEach(function (m) { memberOf[m] = true; });
    Object.keys(DATA.links).forEach(function (eid) {
      var k = DATA.links[eid];
      if ((k.from === g.sw && g.members.indexOf(k.to) >= 0) || (k.to === g.sw && g.members.indexOf(k.from) >= 0)) { groupEdge[eid] = true; }
    });
  });
  function nodeStatus(id) { var d = diff.nodes && diff.nodes[id]; return d ? d.status : ''; }
  function linkStatus(eid) { var k = DATA.links[eid]; return k && k.diff ? k.diff.status : ''; }
  function hiddenIn(m, status) { return m === 'current' ? status === 'removed' : m === 'previous' ? status === 'added' : false; }
  window.LLDP_NODE_STYLE = function (id) { return mode === 'overlay' && nodeStatus(id) === 'removed' ? { opacity: 0.7 } : null; };
  window.LLDP_EDGE_STYLE = function (eid) {
    if (mode !== 'overlay') { return null; }
    var s = linkStatus(eid);
    return s === 'added' ? { color: GREEN, extra: 2, dashes: false } : s === 'removed' ? { color: RED, extra: 0, dashes: [8, 6] } : s === 'changed' ? { color: ORANGE, extra: 1, dashes: false } : null;
  };
  var buttons = Array.prototype.slice.call(document.querySelectorAll('#diff-bar [data-time]'));
  function syncBar() {
    var simOn = !!(window.LLDP_SIM && window.LLDP_SIM.active);
    buttons.forEach(function (b) {
      var m = b.getAttribute('data-time');
      b.classList.toggle('on', m === mode); b.setAttribute('aria-checked', String(m === mode));
      b.disabled = simOn && m !== 'current';
    });
  }
  function applyTime(m) {
    mode = m;
    var nu = [], eu = [];
    Object.keys(DATA.nodes).forEach(function (id) {
      var hide = hiddenIn(m, nodeStatus(id));
      H.nodes[id] = hide;
      if (!memberOf[id]) { nu.push({ id: id, hidden: hide }); }
    });
    Object.keys(DATA.links).forEach(function (eid) {
      var k = DATA.links[eid], hide = hiddenIn(m, linkStatus(eid)) || H.nodes[k.from] || H.nodes[k.to];
      H.edges[eid] = !!hide;
      if (!groupEdge[eid]) { eu.push({ id: eid, hidden: !!hide }); }
    });
    nodesDS.update(nu); edgesDS.update(eu);
    if (window.LLDP_REGROUP) { window.LLDP_REGROUP(); }
    if (window.LLDP_RESTYLE) { window.LLDP_RESTYLE(); }
    syncBar(); network.redraw(); scheduleMinimap();
  }
  var NOTE = { current: 'Current state: what was removed since the previous run is hidden', previous: 'Previous state: what was added since is hidden', overlay: 'Diff overlay: green added, red dashed removed, orange changed' };
  function setTime(m) {
    if (!has) { toast('No earlier snapshot to compare with yet'); return; }
    if (window.LLDP_SIM && window.LLDP_SIM.active && m !== 'current') { toast('Leave the simulation to compare snapshots'); return; }
    applyTime(m); toast(NOTE[m]);
  }
  window.LLDP_SET_TIME = setTime;
  window.LLDP_ON_SIM = function (on) {             // a simulation is about the network as it is now
    if (on && has && mode !== 'current') { applyTime('current'); }
    syncBar();
  };
  var bar = $('diff-bar');
  if (has) {
    bar.hidden = false;
    var s = diff.summary || {}, bits = [];
    if (s.nodes_added) { bits.push('+' + s.nodes_added + ' device' + (s.nodes_added > 1 ? 's' : '')); }
    if (s.nodes_removed) { bits.push('\u2212' + s.nodes_removed + ' device' + (s.nodes_removed > 1 ? 's' : '')); }
    if (s.links_added) { bits.push('+' + s.links_added + ' link' + (s.links_added > 1 ? 's' : '')); }
    if (s.links_removed) { bits.push('\u2212' + s.links_removed + ' link' + (s.links_removed > 1 ? 's' : '')); }
    if (s.links_changed) { bits.push(s.links_changed + ' changed link' + (s.links_changed > 1 ? 's' : '')); }
    if (s.nodes_changed) { bits.push(s.nodes_changed + ' changed device' + (s.nodes_changed > 1 ? 's' : '')); }
    $('diff-sum').textContent = bits.join('  ') || 'No changes since the previous run';
    buttons.forEach(function (b) { b.addEventListener('click', function () { setTime(b.getAttribute('data-time')); }); });
    applyTime('overlay');
  }

  function edgeAt(eid, t) {
    var k = DATA.links[eid], edge = network.body.edges[eid], pos = network.getPositions([k.from, k.to]), a = pos[k.from], b = pos[k.to], pt = null;
    if (!a || !b) { return null; }
    try { pt = edge.edgeType.getPoint(t); } catch (err) { /* straight line instead */ }
    return pt && isFinite(pt.x) && isFinite(pt.y) ? pt : { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t };
  }
  function pillAt(ctx, text, x, y, colour) {
    ctx.font = '700 11px ' + FAMILY;
    var w = ctx.measureText(text).width + 12;
    ctx.fillStyle = colour; ctx.fillRect(x - w / 2, y - 9, w, 18);
    ctx.fillStyle = '#ffffff'; ctx.fillText(text, x, y + 1);
  }
  function drawDiff(ctx) {
    if (mode !== 'overlay' || !has) { return; }
    var pos = network.getPositions(), words = network.getScale() >= 0.35;
    ctx.save();
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    Object.keys(DATA.nodes).forEach(function (id) {
      var s = nodeStatus(id), bn = network.body.nodes[id], p = pos[id];
      if (!s || !bn || !p || bn.options.hidden) { return; }
      var col = s === 'added' ? GREEN : s === 'removed' ? RED : ORANGE, r = (bn.options.size || 22) + 6;
      ctx.strokeStyle = col; ctx.lineWidth = 3; ctx.setLineDash(s === 'removed' ? [6, 4] : []);
      ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, 2 * Math.PI); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = col; ctx.beginPath(); ctx.arc(p.x - r * 0.75, p.y - r * 0.75, 9, 0, 2 * Math.PI); ctx.fill();
      ctx.fillStyle = '#ffffff'; ctx.font = '800 14px ' + FAMILY;
      ctx.fillText(s === 'added' ? '+' : s === 'removed' ? '\u2212' : '\u0394', p.x - r * 0.75, p.y - r * 0.75 + 1);
    });
    if (words) {
      Object.keys(DATA.links).forEach(function (eid) {
        var s = linkStatus(eid), edge = network.body.edges[eid];
        if (!s || !edge || edge.options.hidden) { return; }
        var pt = edgeAt(eid, 0.62);
        if (pt) { pillAt(ctx, s === 'added' ? 'NEW' : s === 'removed' ? 'GONE' : '\u0394 ' + (DATA.links[eid].diff.detail || 'changed').slice(0, 26), pt.x, pt.y, s === 'added' ? GREEN : s === 'removed' ? RED : ORANGE); }
      });
    }
    ctx.restore();
  }

  // ================================================================== minimap radar (160 x 100)
  var mm = $('minimap'), mx = mm.getContext ? mm.getContext('2d') : null, MMW = 160, MMH = 100, frozen = null, dragging = false, raf = 0;
  function mmSetup() {
    if (!mx) { return; }
    var dpr = window.devicePixelRatio || 1;
    mm.width = Math.round(MMW * dpr); mm.height = Math.round(MMH * dpr);
    mx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  function viewRect() {
    var c = network.canvas.frame.canvas, tl = network.DOMtoCanvas({ x: 0, y: 0 }), br = network.DOMtoCanvas({ x: c.clientWidth, y: c.clientHeight });
    return { x0: tl.x, y0: tl.y, x1: br.x, y1: br.y };
  }
  function shownIds() { return Object.keys(DATA.nodes).filter(function (id) { var b = network.body.nodes[id]; return b && !b.options.hidden; }); }
  function frame(ids, pos) {
    var v = viewRect(), f = { x0: v.x0, y0: v.y0, x1: v.x1, y1: v.y1 };
    ids.forEach(function (id) { var p = pos[id]; if (p) { f.x0 = Math.min(f.x0, p.x); f.x1 = Math.max(f.x1, p.x); f.y0 = Math.min(f.y0, p.y); f.y1 = Math.max(f.y1, p.y); } });
    var pad = 8, sc = Math.min((MMW - 2 * pad) / Math.max(1, f.x1 - f.x0), (MMH - 2 * pad) / Math.max(1, f.y1 - f.y0));
    return { sc: sc, ox: (MMW - (f.x1 - f.x0) * sc) / 2 - f.x0 * sc, oy: (MMH - (f.y1 - f.y0) * sc) / 2 - f.y0 * sc };
  }
  function drawMinimap() {
    if (!mx) { return; }
    var pos = network.getPositions(), ids = shownIds(), f = dragging && frozen ? frozen : frame(ids, pos), v = viewRect(), P = window.LLDP_PAL || PALS.light;
    function X(x) { return x * f.sc + f.ox; }
    function Y(y) { return y * f.sc + f.oy; }
    mx.clearRect(0, 0, MMW, MMH);
    mx.lineWidth = 1; mx.strokeStyle = P.muted; mx.globalAlpha = 0.4;
    Object.keys(DATA.links).forEach(function (eid) {
      var k = DATA.links[eid], e = network.body.edges[eid];
      if (!e || e.options.hidden || !pos[k.from] || !pos[k.to]) { return; }
      mx.beginPath(); mx.moveTo(X(pos[k.from].x), Y(pos[k.from].y)); mx.lineTo(X(pos[k.to].x), Y(pos[k.to].y)); mx.stroke();
    });
    mx.globalAlpha = 1;
    var sim = window.LLDP_SIM, bl = sim && sim.active ? sim.blast : null;
    ids.forEach(function (id) {
      var p = pos[id], n = DATA.nodes[id], st = nodeStatus(id), bs = bl && bl.state ? bl.state[id] : '';
      mx.fillStyle = bs === 'failed' || bs === 'isolated' ? RED : bs === 'spof' || bs === 'degraded' ? ORANGE : bs === 'rerouted' ? '#3b82f6'
        : mode === 'overlay' && st === 'added' ? GREEN : mode === 'overlay' && st === 'removed' ? RED : n.status === 'failed' ? RED : n.status === 'discovered' ? P.muted : (theme === 'dark' ? '#60a5fa' : '#2563eb');
      mx.beginPath(); mx.arc(X(p.x), Y(p.y), n.core ? 3 : 2, 0, 6.2832); mx.fill();
    });
    mx.fillStyle = theme === 'dark' ? '#3b82f6' : '#2563eb'; mx.globalAlpha = 0.14;
    mx.fillRect(X(v.x0), Y(v.y0), (v.x1 - v.x0) * f.sc, (v.y1 - v.y0) * f.sc);
    mx.globalAlpha = 1; mx.strokeStyle = theme === 'dark' ? '#3b82f6' : '#2563eb'; mx.lineWidth = 1.5;
    mx.strokeRect(X(v.x0), Y(v.y0), (v.x1 - v.x0) * f.sc, (v.y1 - v.y0) * f.sc);
  }
  function scheduleMinimap() { if (raf) { return; } raf = window.requestAnimationFrame(function () { raf = 0; drawMinimap(); }); }
  function mmGo(e) {                               // centre the view on the clicked point of the radar
    var f = frozen || frame(shownIds(), network.getPositions()), r = mm.getBoundingClientRect();
    var px = (e.clientX - r.left) * MMW / (r.width || MMW), py = (e.clientY - r.top) * MMH / (r.height || MMH);
    network.moveTo({ position: { x: (px - f.ox) / f.sc, y: (py - f.oy) / f.sc }, animation: false });
  }
  mm.addEventListener('mousedown', function (e) { frozen = frame(shownIds(), network.getPositions()); dragging = true; mmGo(e); e.preventDefault(); e.stopPropagation(); });
  window.addEventListener('mousemove', function (e) { if (dragging) { mmGo(e); } });
  window.addEventListener('mouseup', function () { if (dragging) { dragging = false; frozen = null; scheduleMinimap(); } });
  window.addEventListener('resize', function () { mmSetup(); scheduleMinimap(); });
  mmSetup();
  network.on('afterDrawing', function (ctx) { drawDiff(ctx); scheduleMinimap(); });
  network.on('zoom', scheduleMinimap);
  network.on('dragEnd', scheduleMinimap);

  // ================================================================== documentation export: clean vector SVG + TOPOLOGY_SPEC.md
  function xml(v) { return String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); }
  function download(name, text, type) {
    var a = el('a'), url = URL.createObjectURL(new Blob([text], { type: type }));
    a.href = url; a.download = name; document.body.appendChild(a); a.click(); document.body.removeChild(a);
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
  }
  function docTime() { return has && mode === 'previous' ? 'previous' : 'current'; }
  function specText() { return Ops.buildSpec(DATA, { time: docTime() }); }
  // The diagram as it would sit in an RFC, Confluence page or wiki: light colours on white whatever the screen theme, no
  // controls, selection or simulation marks, every interface name, and a viewBox so it scales to any resolution.
  var drawBy = {};                                   // per link: the interface names at each end (what the canvas writes along it)
  (DATA.draw || []).forEach(function (d) { drawBy[d.id] = d; });
  function cleanSvg() {
    var pos = network.getPositions(), out = [], bounds = { x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity };
    function hidden(kind, id) {
      var b = kind === 'n' ? network.body.nodes[id] : network.body.edges[id];
      if (!b || b.options.hidden) { return true; }
      var ghost = kind === 'n' ? DATA.nodes[id].ghost : DATA.links[id].ghost;
      return mode === 'overlay' && !!ghost;
    }
    function grow(x, y, r) { bounds.x0 = Math.min(bounds.x0, x - r); bounds.x1 = Math.max(bounds.x1, x + r); bounds.y0 = Math.min(bounds.y0, y - r); bounds.y1 = Math.max(bounds.y1, y + r); }
    var ids = Object.keys(DATA.nodes).filter(function (id) { return !hidden('n', id) && pos[id]; });
    ids.forEach(function (id) { grow(pos[id].x, pos[id].y, (network.body.nodes[id].options.size || 22) + 70); });
    var eids = Object.keys(DATA.links).filter(function (eid) { var k = DATA.links[eid]; return !hidden('e', eid) && !hidden('n', k.from) && !hidden('n', k.to); });
    var pad = 40, W = bounds.x1 - bounds.x0 + 2 * pad, Hh = bounds.y1 - bounds.y0 + 2 * pad;
    if (!ids.length) { return ''; }
    var font = 'font-family="' + xml(FAMILY) + '"', mono = 'font-family="' + xml(MONO) + '"';
    out.push('<?xml version="1.0" encoding="UTF-8"?>');
    out.push('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="' + [bounds.x0 - pad, bounds.y0 - pad, W, Hh].map(Math.round).join(' ') + '" width="' + Math.round(W) + '" height="' + Math.round(Hh) + '" role="img" aria-label="LLDP topology: ' + ids.length + ' devices, ' + eids.length + ' links">');
    out.push('<title>LLDP topology (' + docTime() + ' state)</title><style>text{font-variant-numeric:tabular-nums}</style>');
    out.push('<rect x="' + (bounds.x0 - pad) + '" y="' + (bounds.y0 - pad) + '" width="' + W + '" height="' + Hh + '" fill="#ffffff"/>');
    if (window.LLDP_TIER_ROWS) {
      window.LLDP_TIER_ROWS().forEach(function (r) {
        var half = window.LLDP_HALF_ROW || 105;
        if (r.level % 2 === 1) { out.push('<rect x="' + (bounds.x0 - pad) + '" y="' + (r.y - half) + '" width="' + W + '" height="' + (2 * half) + '" fill="#1f2933" fill-opacity="0.045"/>'); }
        out.push('<text x="' + (bounds.x0 - pad + 10) + '" y="' + (r.y - half + 18) + '" ' + font + ' font-size="12" font-weight="700" fill="#7b8794" letter-spacing="1">' + xml(String(r.name).toUpperCase()) + '</text>');
      });
    }
    eids.forEach(function (eid) {
      var k = DATA.links[eid], de = network.body.data.edges.get(eid) || {}, pts = [], i, t;
      for (i = 0; i <= 12; i++) { t = i / 12; pts.push(edgeAt(eid, t)); }
      if (pts.some(function (p) { return !p; })) { return; }
      var dash = k.sources && k.sources.length && k.sources.indexOf('lldp') < 0 ? ' stroke-dasharray="9 6"' : '';
      out.push('<polyline fill="none" stroke="' + xml(k.color) + '" stroke-width="' + (de.width || 2) + '" stroke-linecap="round" stroke-linejoin="round"' + dash + ' points="' + pts.map(function (p) { return Math.round(p.x * 10) / 10 + ',' + Math.round(p.y * 10) / 10; }).join(' ') + '"/>');
      var mid = pts[6];
      if (k.label) { out.push('<text x="' + mid.x + '" y="' + (mid.y + (k.warn ? -16 : 4)) + '" text-anchor="middle" ' + font + ' font-size="12" fill="#343434" stroke="#ffffff" stroke-width="4" paint-order="stroke">' + xml(k.label) + '</text>'); }
      if (k.warn) {
        out.push('<path d="M' + mid.x + ',' + (mid.y - 9) + ' L' + (mid.x + 9) + ',' + (mid.y + 7) + ' L' + (mid.x - 9) + ',' + (mid.y + 7) + ' Z" fill="' + (k.warn === 'mismatch' ? '#d64545' : '#e08a00') + '" stroke="#ffffff" stroke-width="2"/>');
      }
      var dr = drawBy[eid] || {};
      [[pts[2], (dr.fromPorts || []).slice(0, 3).join(', ')], [pts[10], (dr.toPorts || []).slice(0, 3).join(', ')]].forEach(function (e) {
        if (e[1]) { out.push('<text x="' + e[0].x + '" y="' + (e[0].y + 4) + '" text-anchor="middle" ' + mono + ' font-size="10" fill="#323f4b" stroke="#ffffff" stroke-width="4" paint-order="stroke">' + xml(e[1]) + '</text>'); }
      });
    });
    ids.forEach(function (id) {
      var n = DATA.nodes[id], bn = network.body.nodes[id], p = pos[id], size = bn.options.size || 22, img = bn.options.image;
      if (typeof img === 'object' && img) { img = img.unselected || img.selected; }
      if (bn.options.shape === 'box') {                                            // an endpoint group badge
        var label = String(bn.options.label || n.label).split('\n')[0], w = label.length * 7.4 + 22;
        out.push('<rect x="' + (p.x - w / 2) + '" y="' + (p.y - 15) + '" width="' + w + '" height="30" rx="14" fill="#eef2f6" stroke="#9aa5b1" stroke-width="2"/>');
        out.push('<text x="' + p.x + '" y="' + (p.y + 5) + '" text-anchor="middle" ' + font + ' font-size="13" fill="#1f2933">' + xml(label) + '</text>');
        return;
      }
      if (img) { out.push('<image x="' + (p.x - size) + '" y="' + (p.y - size) + '" width="' + 2 * size + '" height="' + 2 * size + '" href="' + xml(img) + '" xlink:href="' + xml(img) + '"/>'); }
      var y = p.y + size + 16;
      out.push('<text x="' + p.x + '" y="' + y + '" text-anchor="middle" ' + font + ' font-size="13" font-weight="700" fill="#1f2933" stroke="#ffffff" stroke-width="4" paint-order="stroke">' + xml(n.label) + '</text>');
      if (n.sub) { y += 15; out.push('<text x="' + p.x + '" y="' + y + '" text-anchor="middle" ' + mono + ' font-size="11" fill="#6b7785" stroke="#ffffff" stroke-width="4" paint-order="stroke">' + xml(n.sub) + '</text>'); }
      var badge = n.badge || n.tier;
      if (badge) { y += 14; out.push('<text x="' + p.x + '" y="' + y + '" text-anchor="middle" ' + font + ' font-size="10" font-weight="700" fill="' + (n.tone === 'failed' ? '#c23030' : '#6b7785') + '" stroke="#ffffff" stroke-width="4" paint-order="stroke">' + xml(badge) + '</text>'); }
    });
    out.push('</svg>');
    return out.join('\n');
  }
  window.LLDP_CLEAN_SVG = cleanSvg;
  window.LLDP_SPEC = specText;
  function exportSvg() { var s = cleanSvg(); if (s) { download('lldp-topology-clean.svg', s, 'image/svg+xml'); } else { toast('Nothing to export'); } }
  function exportMd() { download('TOPOLOGY_SPEC.md', specText(), 'text/markdown'); }
  $('m-docsvg').addEventListener('click', exportSvg);
  $('m-docmd').addEventListener('click', exportMd);
  $('m-docs').addEventListener('click', function () {
    exportSvg();
    window.setTimeout(exportMd, 450);
    toast('Documentation bundle: lldp-topology-clean.svg + TOPOLOGY_SPEC.md');
  });

  // ================================================================== hotkeys: / search, F fit, S simulate, Esc deselect
  document.addEventListener('keydown', function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) { return; }
    if (e.key === '/' && !typing(e.target)) { e.preventDefault(); q.focus(); q.select(); return; }
    if (typing(e.target)) { return; }
    if (e.key === 'f' || e.key === 'F') { network.fit({ animation: reduced() ? false : { duration: 350, easingFunction: 'easeInOutQuad' } }); }
    else if (e.key === 's' || e.key === 'S') { $('b-sim').click(); }
    else if (e.key === 'Escape') {
      $('p-more').hidden = true; $('b-more').setAttribute('aria-expanded', 'false');
      closeResults();
    }
  });
  window.LLDP_MINIMAP = drawMinimap;
  scheduleMinimap();
})();
"""


# Crawl history: the pure part (cable identity, what existed in a recorded run, change wording) has
# no DOM, so Node can run it (tests/test_history_client.py); the UI below fetches the server's API.
HISTORY_ENGINE_JS = r"""
(function (root) {
  'use strict';

  function norm(port) { return String(port || '').trim().toLowerCase(); }

  // Identity of one cable: its two (device, port) ends, in a fixed order.
  function linkKey(a, portA, b, portB) {
    var x = a + '\u0000' + norm(portA), y = b + '\u0000' + norm(portB);
    return x < y ? x + '\u0001' + y : y + '\u0001' + x;
  }

  // Which devices and drawn links of the page existed in a recorded graph (the snapshot document).
  // ``nodes`` is DATA.nodes, ``links`` is DATA.links (one drawn link may carry several cables).
  // ``missing`` names the devices the recorded run had that this page does not draw at all.
  function presence(graph, nodes, links) {
    var inGraph = {}, cables = {}, outNodes = {}, outLinks = {};
    (graph.nodes || []).forEach(function (n) { inGraph[n.id] = n; });
    (graph.links || []).forEach(function (l) { cables[linkKey(l.source, l.source_port, l.target, l.target_port)] = true; });
    Object.keys(nodes).forEach(function (id) { outNodes[id] = !!inGraph[id]; });
    Object.keys(links).forEach(function (eid) {
      var k = links[eid];
      outLinks[eid] = !!(outNodes[k.from] && outNodes[k.to] &&
        (k.pairs || []).some(function (p) { return cables[linkKey(k.from, p[0], k.to, p[1])]; }));
    });
    var missing = Object.keys(inGraph).filter(function (id) { return !(id in nodes); })
      .map(function (id) { return inGraph[id].label || id; }).sort();
    return { nodes: outNodes, links: outLinks, missing: missing };
  }

  function plural(n, noun) { return n + ' ' + noun + (n === 1 ? '' : 's'); }

  // One line for the summary of a run's diff, e.g. "+1 device, 1 cable moved, 1 speed downgrade".
  function describe(diff) {
    diff = diff || {};
    if (diff.first_run) { return 'first recorded run'; }
    var bits = [];
    [['devices_added', '+', 'device'], ['devices_removed', '\u2212', 'device'],
     ['links_added', '+', 'link'], ['links_removed', '\u2212', 'link']].forEach(function (r) {
      if (diff[r[0]]) { bits.push(r[1] + plural(diff[r[0]], r[2])); }
    });
    if (diff.reconnections) { bits.push(plural(diff.reconnections, 'cable') + ' moved'); }
    if (diff.speed_downgrades) { bits.push(plural(diff.speed_downgrades, 'speed downgrade')); }
    var up = (diff.speed_changes || 0) - (diff.speed_downgrades || 0);
    if (up > 0) { bits.push(plural(up, 'speed upgrade')); }
    if (diff.new_blind_spots) { bits.push(plural(diff.new_blind_spots, 'new blind spot')); }
    return bits.join(', ') || 'no changes';
  }

  function speed(mbps) {
    if (!mbps) { return '?'; }
    return mbps >= 1000 ? (mbps / 1000) + 'G' : mbps + 'M';
  }

  // The detailed changes of a run (the full diff the API returns), one readable line each.
  function lines(diff) {
    diff = diff || {};
    var out = [];
    function cable(c) { return c.a + ' ' + c.a_port + ' \u2194 ' + c.b + ' ' + c.b_port; }
    (diff.devices_added || []).forEach(function (d) { out.push('+ device ' + d.label); });
    (diff.devices_removed || []).forEach(function (d) { out.push('\u2212 device ' + d.label + ' (disappeared)'); });
    (diff.links_added || []).forEach(function (c) { out.push('+ link ' + cable(c)); });
    (diff.links_removed || []).forEach(function (c) { out.push('\u2212 link ' + cable(c)); });
    (diff.reconnections || []).forEach(function (r) {
      out.push('cable moved on ' + r.device + ' ' + r.port + ': ' + r.from_device + ' ' + r.from_port +
               ' \u2192 ' + r.to_device + ' ' + r.to_port);
    });
    (diff.speed_changes || []).forEach(function (c) {
      out.push('speed ' + (c.direction === 'down' ? 'downgrade' : 'upgrade') + ' ' + cable(c) + ': ' + speed(c.from) + ' \u2192 ' + speed(c.to));
    });
    (diff.new_blind_spots || []).forEach(function (b) {
      out.push('new blind spot ' + b.hostname + (b.reason ? ' (' + b.reason + ')' : '') + (b.discovered_via ? ' via ' + b.discovered_via : ''));
    });
    return out;
  }

  root.LLDPHistory = { linkKey: linkKey, presence: presence, describe: describe, lines: lines };
})(typeof window !== 'undefined' ? window : globalThis);
"""

# The scrubber bar: only active when the page is served by ``main.py --serve`` (http), where it asks
# /api/history for the recorded crawls and /api/history/<run_id> for the graph of the one picked.
HISTORY_UI_JS = r"""
(function () {
  'use strict';
  var DATA = window.LLDP_DETAIL, Hist = window.LLDPHistory;
  if (!DATA || !Hist || typeof network === 'undefined' || typeof fetch !== 'function') { return; }
  if (!/^https?:$/.test(location.protocol)) { return; }          // opened from disk: there is no server to ask
  var nodesDS = network.body.data.nodes, edgesDS = network.body.data.edges;
  var H = window.LLDP_TIMEHIDE = window.LLDP_TIMEHIDE || { nodes: {}, edges: {} };
  var runs = [], shown = -1, cache = {}, timer = null, detailOn = false;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text !== undefined && text !== null) { e.textContent = text; }
    return e;
  }
  function api(path) {
    return fetch(path, { headers: { Accept: 'application/json' }, cache: 'no-store' }).then(function (r) {
      return r.json().then(function (body) { return r.ok && body && body.status === 'ok' ? body.data : null; });
    });
  }
  function when(run) { return String(run.timestamp || '').replace('T', ' ').slice(0, 19); }

  var memberOf = {}, groupEdge = {};
  (DATA.groups || []).forEach(function (g) {
    g.members.forEach(function (m) { memberOf[m] = true; });
    Object.keys(DATA.links).forEach(function (eid) {
      var k = DATA.links[eid];
      if ((k.from === g.sw && g.members.indexOf(k.to) >= 0) || (k.to === g.sw && g.members.indexOf(k.from) >= 0)) { groupEdge[eid] = true; }
    });
  });

  var bar = el('div'), slider = el('input'), label = el('span', 'hb-label'), sum = el('span', 'hb-sum');
  var live = el('button', null, 'Live'), more = el('button', null, 'Changes'), fresh = el('button', 'hb-new', 'New crawl \u2013 reload');
  var detail = el('div');
  bar.id = 'hist-bar'; bar.hidden = true;
  bar.setAttribute('role', 'group'); bar.setAttribute('aria-label', 'Crawl history');
  slider.type = 'range'; slider.min = '0'; slider.step = '1';
  slider.setAttribute('aria-label', 'Recorded crawl');
  live.type = 'button'; more.type = 'button'; fresh.type = 'button'; fresh.hidden = true;
  live.title = 'Back to the newest recorded crawl'; more.title = 'What changed in the crawl shown';
  detail.id = 'hist-detail'; detail.hidden = true; detail.setAttribute('role', 'region'); detail.setAttribute('aria-label', 'Changes in the crawl shown');
  bar.appendChild(el('b', 'hb-title', 'History'));
  [slider, label, sum, live, more, fresh].forEach(function (n) { bar.appendChild(n); });
  document.body.appendChild(bar); document.body.appendChild(detail);

  function setVisibility(vis) {
    var nu = [], eu = [];
    Object.keys(DATA.nodes).forEach(function (id) {
      var hide = !vis.nodes[id]; H.nodes[id] = hide;
      if (!memberOf[id]) { nu.push({ id: id, hidden: hide }); }
    });
    Object.keys(DATA.links).forEach(function (eid) {
      var hide = !vis.links[eid]; H.edges[eid] = hide;
      if (!groupEdge[eid]) { eu.push({ id: eid, hidden: hide }); }
    });
    nodesDS.update(nu); edgesDS.update(eu);
    if (window.LLDP_REGROUP) { window.LLDP_REGROUP(); }
    if (window.LLDP_RESTYLE) { window.LLDP_RESTYLE(); }
    network.redraw();
  }
  function showLive() {
    if (DATA.diff && DATA.diff.baseline && window.LLDP_SET_TIME) { window.LLDP_SET_TIME('overlay'); return; }
    var all = { nodes: {}, links: {} };
    Object.keys(DATA.nodes).forEach(function (id) { all.nodes[id] = !DATA.nodes[id].ghost; });
    Object.keys(DATA.links).forEach(function (eid) { all.links[eid] = !DATA.links[eid].ghost; });
    setVisibility(all);
  }
  function render(run, full) {
    label.textContent = when(run) + '  \u00b7  ' + run.run_id;
    var head = run.devices + ' devices, ' + run.links + ' links  \u00b7  ';
    sum.textContent = head + Hist.describe(run.diff);
    detail.textContent = '';
    if (!full) { return; }
    var rows = Hist.lines(full.diff), vis = Hist.presence(full.graph, DATA.nodes, DATA.links);
    detail.appendChild(el('h3', null, 'Changes in this crawl (vs the one before)'));
    var ul = el('ul');
    (rows.length ? rows : ['no changes']).forEach(function (t) { ul.appendChild(el('li', null, t)); });
    detail.appendChild(ul);
    if (vis.missing.length) { detail.appendChild(el('p', 'hb-note', 'Not on this map: ' + vis.missing.join(', '))); }
  }
  function select(i) {
    shown = i;
    var run = runs[i], newest = i === runs.length - 1;
    slider.value = String(i); live.disabled = newest;
    if (newest) { render(run, null); showLive(); sum.textContent += '  (live)'; return; }
    render(run, null); sum.textContent += '  \u00b7  loading\u2026';
    var token = run.run_id;
    (cache[token] ? Promise.resolve(cache[token]) : api('/api/history/' + encodeURIComponent(token))).then(function (full) {
      if (runs[shown].run_id !== token) { return; }            // the slider moved on
      if (!full) { sum.textContent = 'could not load this crawl'; return; }
      cache[token] = full;
      render(run, full); setVisibility(Hist.presence(full.graph, DATA.nodes, DATA.links));
    }).catch(function () { sum.textContent = 'could not load this crawl'; });
  }

  slider.addEventListener('input', function () {
    var i = parseInt(slider.value, 10);
    label.textContent = when(runs[i]) + '  \u00b7  ' + runs[i].run_id;
    clearTimeout(timer); timer = setTimeout(function () { select(i); }, 120);
  });
  live.addEventListener('click', function () { select(runs.length - 1); });
  more.addEventListener('click', function () { detailOn = !detailOn; detail.hidden = !detailOn; more.setAttribute('aria-pressed', String(detailOn)); });
  fresh.addEventListener('click', function () { location.reload(); });

  api('/api/history').then(function (list) {
    if (!list || !list.length) { return; }
    runs = list.slice().reverse();                              // oldest first: the slider runs left to right in time
    slider.max = String(runs.length - 1); slider.disabled = runs.length < 2;
    bar.hidden = false; select(runs.length - 1);
    setInterval(function () {                                   // tell the viewer when a newer crawl was recorded
      api('/api/history').then(function (now) {
        if (now && now.length && now[0].run_id !== runs[runs.length - 1].run_id) { fresh.hidden = false; }
      }).catch(function () {});
    }, 30000);
  }).catch(function () {});
})();
"""

HISTORY_CSS = r"""
#hist-bar{position:fixed;z-index:22;left:354px;bottom:96px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;padding:6px 10px;max-width:calc(100vw - 580px);background:var(--glass);backdrop-filter:blur(12px) saturate(160%);-webkit-backdrop-filter:blur(12px) saturate(160%);border:1px solid var(--glass-line);box-shadow:var(--shadow);border-radius:10px;color:var(--text);font-size:12px}
body.side-off #hist-bar{left:14px}
#hist-bar[hidden],#hist-detail[hidden]{display:none}
#hist-bar .hb-title{color:var(--strong);letter-spacing:.06em;text-transform:uppercase;font-size:11px}
#hist-bar input[type=range]{width:220px;accent-color:var(--accent)}
#hist-bar .hb-label{font-family:var(--mono,monospace);color:var(--strong)}
#hist-bar .hb-sum{color:var(--muted)}
#hist-bar button{font:inherit;font-size:12px;border:1px solid var(--line);background:var(--panel2);color:var(--text);border-radius:8px;padding:3px 10px;cursor:pointer}
#hist-bar button[disabled]{opacity:.4;cursor:not-allowed}
#hist-bar button[aria-pressed="true"]{border-color:var(--accent);color:var(--strong)}
#hist-bar .hb-new{border-color:var(--warn);color:var(--strong)}
#hist-detail{position:fixed;z-index:23;left:354px;bottom:140px;width:440px;max-width:calc(100vw - 380px);max-height:40vh;overflow:auto;box-sizing:border-box;padding:10px 14px;background:var(--glass);backdrop-filter:blur(12px) saturate(160%);-webkit-backdrop-filter:blur(12px) saturate(160%);border:1px solid var(--glass-line);box-shadow:var(--shadow);border-radius:10px;color:var(--text);font-size:12px}
body.side-off #hist-detail{left:14px}
#hist-detail h3{margin:0 0 6px;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
#hist-detail ul{margin:0;padding-left:16px}
#hist-detail .hb-note{margin:8px 0 0;color:var(--muted)}
@media print{#hist-bar,#hist-detail{display:none!important}}
"""
