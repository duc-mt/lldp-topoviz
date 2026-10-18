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
  var FAMILY = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

  // ------------------------------------------------------------ small DOM helpers
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text !== undefined && text !== null) { e.textContent = text; }
    return e;
  }
  function hide() { panel.hidden = true; }
  function begin(title, badgeText, badgeClass) {
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
  }
  function section(title, text, cls) {
    panel.appendChild(el('h3', null, title));
    panel.appendChild(el('div', cls || 'ld-text', text));
  }
  function facts(rows) {
    var dl = el('dl');
    rows.forEach(function (r) {
      var v = r[1];
      if (v === undefined || v === null || v === '' || (Array.isArray(v) && !v.length)) { return; }
      dl.appendChild(el('dt', null, r[0]));
      dl.appendChild(el('dd', null, Array.isArray(v) ? v.join(', ') : String(v)));
    });
    if (dl.firstChild) { panel.appendChild(dl); }
  }
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
    begin(n.label, n.status + (n.seed ? ' \u00b7 seed' : ''), 'ld-' + n.status);
    if (n.error) {
      section(n.badge || 'Error', n.error + (n.hint ? '\n\n' + n.hint : ''), 'ld-text ld-err');
    }
    facts([['Type', n.kind], ['Tier', n.tier], ['OS', n.os], ['Hardware', n.platform],
           ['Address', n.address], ['Management IPs', n.mgmt_ips], ['Device type', n.device_type],
           ['Chassis ID', n.chassis_id], ['Capabilities', n.capabilities],
           ['Discovery', (n.discovery || []).map(function (x) { return x.toUpperCase(); })],
           ['Depth', n.depth]]);
    if (n.skip_reason) { section('Not crawled', n.skip_reason); }
    var noted = Object.keys(n.protocol_notes || {});
    if (n.status === 'crawled' && noted.length) {
      section('Discovery notes', noted.map(function (k) { return k.toUpperCase() + ': ' + n.protocol_notes[k]; }).join('\n'));
    }
    if (n.status === 'crawled' && n.status_note) { section('Link checks', 'No interface data: ' + n.status_note); }
    section('Platform description',
            n.description || 'This device did not advertise a system description over LLDP.');

    if (n.port_channels.length) {
      panel.appendChild(el('h3', null, 'Port-channels (' + n.port_channels.length + ')'));
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
      panel.appendChild(pcs);
    } else if (n.status === 'crawled') {
      section('Port-channels', n.lag_note ? 'Not collected: ' + n.lag_note : 'None configured.');
    }

    panel.appendChild(el('h3', null, 'Links (' + n.links.length + ')'));
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
    panel.appendChild(ul);
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
          badge, k.warn === 'mismatch' ? 'ld-mismatch' : k.warn === 'degraded' ? 'ld-warn' : (b ? 'ld-bundle' : 'ld-link'));
    if (k.warnings.length) {
      panel.appendChild(el('h3', null, 'Warnings (' + k.warnings.length + ')'));
      var wl = el('ul');
      k.warnings.forEach(function (w) { wl.appendChild(el('li', 'w-' + w.severity, w.text)); });
      panel.appendChild(wl);
    }
    facts([['Speed', speedText(k)], ['Bundle state', b ? (b.up ? 'up' : 'down') : ''],
           ['Seen by', (k.sources || []).map(function (x) { return x.toUpperCase(); }).join(' + ')],
           ['Link checks', k.checked ? '' : 'no interface speed/duplex data for this link']]);
    panel.appendChild(el('h3', null, b ? 'Member links' : 'Interfaces'));
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
    panel.appendChild(ul);
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
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { hide(); } });

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
      li.appendChild(el('div', 'pb-why', w.text));
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
  var filter = null, dimNodes = {}, dimEdges = {};
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
    return type === 'warn' ? k.warn === val : false;
  }
  function applyFilter(f) {
    filter = f;
    var nodeIds = Object.keys(DATA.nodes), edgeIds = Object.keys(DATA.links), keepN = {}, keepE = {};
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
    network.body.data.nodes.update(nodeIds.map(function (id) {
      dimNodes[id] = !!f && !keepN[id];
      return { id: id, opacity: dimNodes[id] ? 0.18 : 1 };
    }));
    network.body.data.edges.update(edgeIds.map(function (eid) {
      var k = DATA.links[eid];
      dimEdges[eid] = !!f && !keepE[eid];
      return { id: eid, color: { color: k.color, opacity: dimEdges[eid] ? 0.12 : 1 },
               font: { size: 12, strokeWidth: 4, strokeColor: STYLE.bg || '#ffffff',
                       color: dimEdges[eid] ? 'rgba(52,52,52,0.15)' : '#343434',
                       vadjust: (k.warn && k.label) ? -16 : 0 },
               shadow: (k.warn && !dimEdges[eid]) ? { enabled: true, color: WARN[k.warn], size: 14, x: 0, y: 0 } : false };
    }));
    Array.prototype.forEach.call(document.querySelectorAll('#lldp-legend .lg-item'), function (b) {
      b.setAttribute('aria-pressed', String(!!f && b.getAttribute('data-filter') === f));
    });
    network.redraw();
  }
  Array.prototype.forEach.call(document.querySelectorAll('#lldp-legend .lg-item'), function (b) {
    b.addEventListener('click', function () {
      var key = b.getAttribute('data-filter');
      applyFilter(filter === key ? null : key);
    });
  });

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
  network.on('beforeDrawing', function (ctx) {
    if (!TIERS.length) { return; }
    var v = viewBox();
    ctx.save();
    ctx.fillStyle = 'rgba(31,41,51,0.045)';
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
    var pos = network.getPositions();
    ctx.save();
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    Object.keys(DATA.nodes).forEach(function (id) {
      var n = DATA.nodes[id], p = pos[id], bn = network.body.nodes[id];
      if (!p || !bn || bn.options.hidden) { return; }
      ctx.globalAlpha = dimNodes[id] ? 0.22 : 1;
      var size = bn.options.size || 22;
      var lines = [{ t: n.label, font: '600 14px ' + FAMILY, color: STYLE.fg || '#323f4b' }];
      if (scale >= 0.45 && n.sub) { lines.push({ t: n.sub, font: '11px ' + FAMILY, color: '#6b7785' }); }
      if (scale >= 0.45 && n.badge) {
        lines.push({ t: (n.tone === 'failed' ? '\u25cf ' : '') + n.badge, font: '600 11px ' + FAMILY,
                     color: n.tone === 'failed' ? '#C23030' : '#6B7785' });
      }
      var widest = 0;
      lines.forEach(function (l) { ctx.font = l.font; widest = Math.max(widest, ctx.measureText(l.t).width); });
      var top = p.y + size + 6, height = 15 * lines.length + 2;
      ctx.fillStyle = 'rgba(255,255,255,0.82)';          // pill: links pass behind the text
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
    ctx.fillStyle = '#7b8794';
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
  function drawPortLabels(ctx) {
    if (network.getScale() < 0.3) { return; }  // too small to read: skip rather than clutter
    ctx.save();
    ctx.font = FONT;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.lineJoin = 'round';
    ctx.lineWidth = 4;
    ctx.strokeStyle = STYLE.bg || '#ffffff';
    ctx.fillStyle = STYLE.fg || '#323f4b';
    var taken = {};  // node id -> headings already used by labels around that node
    LINKS.forEach(function (lk) {
      var pos = network.getPositions([lk.from, lk.to]);
      var a = pos[lk.from], b = pos[lk.to], edge = network.body.edges[lk.id];
      if (!a || !b || !edge || edge.options.hidden) { return; }
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
      ctx.strokeStyle = '#ffffff';
      ctx.stroke();
      ctx.fillStyle = WARN[k.warn];
      ctx.fill();
      ctx.fillStyle = '#ffffff';
      ctx.font = '700 11px ' + FAMILY;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('!', pt.x, pt.y + 3);
      ctx.restore();
    });
  }

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
  function build(nodes, links) {
    var model = { nodes: nodes, edges: [], roots: [], isRoot: {} };
    Object.keys(links).forEach(function (id) {
      var k = links[id];
      if (nodes[k.from] && nodes[k.to]) {
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
  var FAMILY = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
  var RED = '#D64545', AMBER = '#E08A00';
  var model = Sim.build(DATA.nodes, DATA.links);
  var sim = { active: false, failedNodes: new Set(), failedLinks: new Set(), showSpof: false,
              result: null, state: {} };
  window.LLDP_SIM = sim;
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
      g.members.forEach(function (m) { nu.push({ id: m, hidden: collapsed }); });
      g.edgeIds.forEach(function (e) { eu.push({ id: e, hidden: collapsed }); });
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

  function updateGroups() {
    var up = [];
    groupIds.forEach(function (gid) {
      var g = groups[gid], lost = 0, weak = 0, label = (g.expanded ? '\u2212 ' : '') + g.text;
      var border = '#9aa5b1', fill = g.expanded ? '#ffffff' : '#eef2f6';
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
      up.push({ id: gid, label: label, color: { background: fill, border: border } });
    });
    if (up.length) { nodesDS.update(up); }
  }

  // --------------------------------------------------------------------- the simulation
  var toggleBtn = document.getElementById('lldp-sim-toggle');
  var banner = document.getElementById('lldp-sim-banner');
  var countsEl = document.getElementById('lldp-sim-counts'), linesEl = document.getElementById('lldp-sim-lines');
  var spofBtn = document.getElementById('lldp-sim-spof'), pop = document.getElementById('lldp-sim-pop');

  function refresh() {
    sim.result = sim.active ? Sim.evaluate(model, sim.failedNodes, sim.failedLinks) : null;
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
    else { line('Click a device or a link to simulate its failure.'); }
  }
  function flip(set, key) {
    if (set.has(key)) { set.delete(key); } else { set.add(key); }
    refresh();
  }
  function enter() {
    sim.active = true;
    var detail = document.getElementById('lldp-detail');
    if (detail) { detail.hidden = true; }
    refresh();
  }
  function clear() { sim.failedNodes.clear(); sim.failedLinks.clear(); }
  function leave() { sim.active = false; sim.showSpof = false; clear(); closePop(); refresh(); }
  toggleBtn.addEventListener('click', function () { if (sim.active) { leave(); } else { enter(); } });
  spofBtn.addEventListener('click', function () { sim.showSpof = !sim.showSpof; refresh(); });
  document.getElementById('lldp-sim-reset').addEventListener('click', function () { clear(); closePop(); refresh(); });
  document.getElementById('lldp-sim-exit').addEventListener('click', leave);
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { closePop(); } });
  sim.refresh = refresh;

  // ----------------------------------------------------------- click-to-fail popovers
  function closePop() { pop.hidden = true; }
  function openPop(at, title, label, action) {
    while (pop.firstChild) { pop.removeChild(pop.firstChild); }
    pop.appendChild(el('div', 'sp-title', title));
    var b = el('button', 'sp-action', label);
    b.type = 'button';
    b.addEventListener('click', function () { closePop(); action(); });
    pop.appendChild(b);
    pop.hidden = false;
    var f = network.canvas.frame.getBoundingClientRect();
    pop.style.left = Math.max(8, Math.min(f.left + at.x + 10, window.innerWidth - pop.offsetWidth - 8)) + 'px';
    pop.style.top = Math.max(8, Math.min(f.top + at.y + 10, window.innerHeight - pop.offsetHeight - 8)) + 'px';
    b.focus();
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
      openPop(p.pointer.DOM, name(id),
              sim.failedNodes.has(id) ? 'Restore device' : 'Simulate Switch Failure / Blast Radius',
              function () { flip(sim.failedNodes, id); });
    } else if (!id && eid && DATA.links[eid]) {
      var k = DATA.links[eid], n = (k.pairs || []).length;
      openPop(p.pointer.DOM, k.a + ' \u2194 ' + k.b,
              sim.failedLinks.has(eid) ? 'Restore cable' : (n > 1 ? 'Simulate Cable Cut (all ' + n + ' links)' : 'Simulate Cable Cut'),
              function () { flip(sim.failedLinks, eid); });
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
        ctx.fillStyle = 'rgba(214,69,69,0.55)';
        ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, 2 * Math.PI); ctx.fill();
        ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 4; cross(ctx, p.x, p.y, r * 0.5);
        if (words) { tag(ctx, '[FAILED]', p.x, top, RED, '#ffffff', RED); }
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
    ctx.restore();
  });

  applyGrouping();
})();
"""
