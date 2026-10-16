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
    if (p.nodes && p.nodes.length) { showNode(p.nodes[0]); }
    else if (p.edges && p.edges.length) { showLink(p.edges[0]); }
    else { hide(); }
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
      if (!p || !bn) { return; }
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
      if (!a || !b || !edge) { return; }
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
      if (!edge || !a || !b) { return; }
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
