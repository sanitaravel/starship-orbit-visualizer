/* Client-side player. Plotly draws only the static scene; this file animates it at the display
 * refresh rate from the data in gd.layout.meta.anim (see starship_viz/figures.py).
 *
 * Time: a continuous simulation time t (seconds since launch) advances by real dt * speed inside a
 * requestAnimationFrame loop. The ship is interpolated between the samples.
 *   2D: a canvas overlay (pointer-events: none) exactly over the geo subplot draws the night
 *       shading, terminator, ground track, launch site and ship; the overlay is re-projected after
 *       zoom/pan, resize and devicePixelRatio changes.
 *   3D: only the ship trace is moved, and the surface day/night colours are re-pushed when the
 *       subsolar point has moved more than SHADE_THRESHOLD_DEG.
 * The controls live in the static Dash layout (app.py); the graph is replaced on every update, so
 * each new Plotly div is bound when it mounts and an older player is dropped. */
(function () {
  'use strict';
  var PLAY = '▶︎', PAUSE = '⏸︎';
  var SPEEDS = [30, 60, 120, 300, 600, 1200], DEFAULT_SPEED = 300;
  var SHADE_THRESHOLD_DEG = 0.25;   // 3D: min subsolar movement between surface colour updates
  var SHADE_COOLDOWN_MS = 1000;     // 3D: min real time between surface updates (a surface update costs frames)
  var SHADE_MAX_LAG_DEG = 3;        // 3D: while playing, update regardless of the cooldown beyond this lag
  var PICK_REFRESH_MS = 1000;       // 3D: while playing, refresh the gl pick buffer (hover) this often
  var MAX_DT = 0.25;                // s; real time gap above which a frame is treated as a stall
  var D2R = Math.PI / 180;

  var speed = DEFAULT_SPEED;        // survives graph rebuilds
  var player = null;                // the player of the currently mounted graph

  /* ---------- helpers ---------- */
  function $(id) { return document.getElementById(id); }
  function clamp(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }
  function pad2(n) { return (n < 10 ? '0' : '') + n; }

  /* 'UTC YYYY-MM-DD HH:MM:SS' for t0_ms + t seconds (floored to whole seconds). */
  function utcLabel(t0ms, t) {
    var d = new Date(t0ms + Math.floor(t) * 1000);
    return 'UTC ' + d.getUTCFullYear() + '-' + pad2(d.getUTCMonth() + 1) + '-' + pad2(d.getUTCDate()) + ' ' +
      pad2(d.getUTCHours()) + ':' + pad2(d.getUTCMinutes()) + ':' + pad2(d.getUTCSeconds());
  }

  /* Index k with ts[k] <= t < ts[k+1] (clamped to [0, n-2]); `hint` speeds up monotone playback. */
  function seek(ts, t, hint) {
    var n = ts.length, k = hint | 0;
    if (k < 0 || k > n - 2) k = 0;
    if (ts[k] <= t && (k === n - 2 || t < ts[k + 1])) return k;
    if (k + 1 < n - 1 && ts[k + 1] <= t && (k + 1 === n - 2 || t < ts[k + 2])) return k + 1;
    var lo = 0, hi = n - 2;
    while (lo < hi) {
      var mid = (lo + hi + 1) >> 1;
      if (ts[mid] <= t) lo = mid; else hi = mid - 1;
    }
    return lo;
  }

  /* Interpolation between samples k and k1 = min(k+1, n-1): fraction f in [0, 1]. A zero-length
   * interval (duplicate time stamp) or a single sample gives f = 0 instead of NaN. */
  function frac(ts, t, k) {
    var k1 = Math.min(k + 1, ts.length - 1), dt = ts[k1] - ts[k];
    return dt > 0 ? clamp((t - ts[k]) / dt, 0, 1) : 0;
  }

  /* Unit vector of lon/lat (deg) and back; slerp of two unit vectors. */
  function lonLatVec(lon, lat) {
    var lo = lon * D2R, la = lat * D2R, c = Math.cos(la);
    return [c * Math.cos(lo), c * Math.sin(lo), Math.sin(la)];
  }
  function vecLonLat(v) {
    return [Math.atan2(v[1], v[0]) / D2R, Math.atan2(v[2], Math.sqrt(v[0] * v[0] + v[1] * v[1])) / D2R];
  }
  function slerp(a, b, f) {
    var d = clamp(a[0] * b[0] + a[1] * b[1] + a[2] * b[2], -1, 1), w = Math.acos(d), sw = Math.sin(w), x, y;
    if (sw < 1e-9) { x = 1 - f; y = f; } else { x = Math.sin((1 - f) * w) / sw; y = Math.sin(f * w) / sw; }
    return [x * a[0] + y * b[0], x * a[1] + y * b[1], x * a[2] + y * b[2]];
  }

  /* Subsolar point at sim time t: [lat_deg, lon_deg] (lon unwrapped, continuous), linearly
   * interpolated between the samples sent from Python. */
  function sunAt(a, t) {
    var n = a.sun_lat.length, s = clamp(t / a.sun_dt, 0, n - 1), i = Math.min(Math.floor(s), n - 2);
    if (n < 2) return [a.sun_lat[0], a.sun_lon[0]];
    var f = s - i;
    return [a.sun_lat[i] + f * (a.sun_lat[i + 1] - a.sun_lat[i]),
            a.sun_lon[i] + f * (a.sun_lon[i + 1] - a.sun_lon[i])];
  }

  /* ---------- 2D: canvas overlay ---------- */
  function Map2D(gd, a) {
    var self = this;
    this.gd = gd; this.a = a; this.k = 0;
    this.vis = {terminator: true, track: true, site: true, ship: true};
    this.sc = gd.querySelector('.svg-container');
    this.canvas = document.createElement('canvas');
    this.canvas.className = 'pb-overlay';
    this.canvas.setAttribute('aria-hidden', 'true');
    // Between the map layers and the legend/modebar, so the legend stays on top.
    var legendSvg = null, kids = this.sc.children;
    for (var i = 0; i < kids.length; i++) {
      if (kids[i].tagName.toLowerCase() === 'svg' && kids[i].querySelector('.legend, .infolayer')) {
        legendSvg = kids[i]; break;
      }
    }
    this.sc.insertBefore(this.canvas, legendSvg);
    this.ctx = this.canvas.getContext('2d');
    this.track = null;          // Path2D of the whole ground track (rebuilt on sync)
    this.sync();
    this.onSync = function () { self.sync(); if (player) player.request(); };
    gd.on('plotly_relayout', this.onSync);
    gd.on('plotly_relayouting', this.onSync);
    gd.on('plotly_afterplot', this.onSync);
    this.onRestyle = function () { self.readVisibility(); if (player) player.request(); };
    gd.on('plotly_restyle', this.onRestyle);
    this.readVisibility();
    if (window.ResizeObserver) {
      this.ro = new ResizeObserver(this.onSync);
      this.ro.observe(this.sc);
    }
  }

  Map2D.prototype.readVisibility = function () {
    var names = {Terminator: 'terminator', 'Ground track': 'track', 'Launch site': 'site', Ship: 'ship'};
    var data = this.gd.data || [];
    for (var i = 0; i < data.length; i++) {
      var key = names[data[i].name];
      if (key) this.vis[key] = data[i].visible !== false && data[i].visible !== 'legendonly';
    }
  };

  /* Recompute the canvas size and the lon/lat -> css px mapping from the geo subplot.
   * Equirectangular is linear in latitude and in the longitude u measured from the cut meridian
   * lon0 = -rotation (panning rotates the globe, which moves the antimeridian):
   *   u = wrap180(lon - lon0),  x = ax * u + bx,  y = ay * lat + by. */
  Map2D.prototype.sync = function () {
    var sp = this.gd._fullLayout && this.gd._fullLayout.geo && this.gd._fullLayout.geo._subplot;
    if (!sp || !sp.projection) return;
    var dpr = window.devicePixelRatio || 1, w = this.sc.clientWidth, h = this.sc.clientHeight;
    var cw = Math.max(1, Math.round(w * dpr)), ch = Math.max(1, Math.round(h * dpr));
    if (this.canvas.width !== cw || this.canvas.height !== ch) {
      this.canvas.width = cw; this.canvas.height = ch;
      this.canvas.style.width = w + 'px'; this.canvas.style.height = h + 'px';
    }
    this.dpr = dpr;
    var p = sp.projection, rot = typeof p.rotate === 'function' ? p.rotate() : [0, 0, 0];
    this.lon0 = -(rot && rot[0] || 0);
    var o = p([this.lon0, 0]), e = p([this.lon0 + 1, 0]), n = p([this.lon0, 90]);
    // Offset of the geo group in the container: the geo group's screen position relative to the canvas.
    var ox = 0, oy = 0, fw = sp.framework && sp.framework.node && sp.framework.node();
    if (fw && fw.getScreenCTM) {
      var m = fw.getScreenCTM(), r = this.canvas.getBoundingClientRect();
      if (m) { ox = m.e - r.left; oy = m.f - r.top; }
    }
    this.ax = e[0] - o[0]; this.bx = o[0] + ox;
    this.ay = (n[1] - o[1]) / 90; this.by = o[1] + oy;
    var q = p([this.lon0 + 90, 45]);
    this.ref = [o[0], o[1], n[1], q[0], q[1]];
    var cr = sp.clipRect && sp.clipRect.node && sp.clipRect.node();
    this.clip = cr ? [cr.x.baseVal.value + ox, cr.y.baseVal.value + oy,
                      cr.width.baseVal.value, cr.height.baseVal.value] : [0, 0, w, h];
    this.projRef = p;
    // Longitudes of the track relative to the cut meridian, and the static full track in css px.
    var lon = this.a.lon, u = this.u && this.u.length === lon.length ? this.u : (this.u = new Float64Array(lon.length));
    for (var i = 0; i < lon.length; i++) u[i] = this.rel(lon[i]);
    this.track = new Path2D();
    this.tracePath(this.track, lon.length - 1, null);
  };

  Map2D.prototype.rel = function (lon) {
    var u = (lon - this.lon0) % 360;
    return u >= 180 ? u - 360 : (u < -180 ? u + 360 : u);
  };
  Map2D.prototype.px = function (lon) { return this.ax * this.rel(lon) + this.bx; };
  Map2D.prototype.py = function (lat) { return this.ay * lat + this.by; };

  /* Add the ground track from sample 0 to sample `last` (plus the interpolated point `tail`,
   * [lon, lat], if given) to path `p`. The path breaks where it crosses the cut meridian (the map
   * edge). Segments with a large longitude change (passes near a pole) are split along the great
   * circle first, so that a crossing is only reported where the track really crosses the edge and
   * a pole pass runs up to the pole row instead of cutting across the map. */
  Map2D.prototype.tracePath = function (p, last, tail) {
    var u = this.u, lat = this.a.lat, ax = this.ax, ay = this.ay, bx = this.bx, by = this.by, self = this;
    var u0 = u[0], py0 = lat[0], lastX = ax * u0 + bx, lastY = ay * py0 + by;
    p.moveTo(lastX, lastY);
    function piece(uu, la, force) {
      var x = ax * uu + bx, y = ay * la + by;
      if (py0 * la > 0 && Math.abs(py0) >= 89 && Math.abs(la) >= 89 && Math.abs(uu - u0) > 30 && Math.abs(uu - u0) < 330) {
        // hop along the pole row (a sample at/near the pole): end the leg at the pole row, restart at the new longitude
        var pyr = ay * (la > 0 ? 90 : -90) + by;
        p.lineTo(ax * u0 + bx, pyr); p.moveTo(x, pyr);
        lastX = x; lastY = pyr; u0 = uu; py0 = la;
        return;
      }
      if (Math.abs(uu - u0) > 180) {   // crosses the cut: end at the map edge, restart at the other edge
        var up = uu + (u0 > 0 ? 360 : -360), edge = u0 > 0 ? 180 : -180;
        var lc = py0 + (edge - u0) / (up - u0) * (la - py0);
        p.lineTo(ax * edge + bx, ay * lc + by);
        p.moveTo(ax * -edge + bx, ay * lc + by);
        lastX = ax * -edge + bx; lastY = ay * lc + by;
      }
      x = ax * uu + bx; y = ay * la + by;
      if (force || Math.abs(x - lastX) + Math.abs(y - lastY) >= 0.75) {  // skip sub-pixel steps
        p.lineTo(x, y); lastX = x; lastY = y;
      }
      u0 = uu; py0 = la;
    }
    var n = tail ? last + 2 : last + 1;
    for (var i = 1; i < n; i++) {
      var uu = i <= last ? u[i] : self.rel(tail[0]), la = i <= last ? lat[i] : tail[1];
      var dl = uu - u0;
      if (dl > 180) dl -= 360; else if (dl < -180) dl += 360;
      if (Math.abs(dl) > 60) {
        var va = lonLatVec(u0, py0), vb = lonLatVec(uu, la), m = Math.ceil(Math.abs(dl) / 20), brk = null;
        if (Math.abs(dl) > 90) {   // pole pass? sample the arc finely and look for a run within 1 deg of a pole
          var dot = Math.max(-1, Math.min(1, va[0] * vb[0] + va[1] * vb[1] + va[2] * vb[2])),
              mf = Math.min(720, Math.ceil(Math.acos(dot) / D2R / 0.25)), fine = [], ia = -1, ib = -1;
          for (var j = 1; j < mf; j++) {
            var q = vecLonLat(slerp(va, vb, j / mf));
            fine.push(q);
            if (Math.abs(q[1]) >= 89) { if (ia < 0) ia = j - 1; ib = j - 1; }
          }
          if (ia >= 0) {
            var dn = fine[ib][0] - fine[ia][0];
            dn -= 360 * Math.round(dn / 360);
            if (Math.abs(dn) > 90) brk = [fine, ia, ib];
          }
        }
        if (brk) {   // end the leg at the pole row, resume at the new longitude (no connector along the edge)
          var fi = brk[0], pa = brk[1], pb = brk[2], pl = fi[pa][1] > 0 ? 90 : -90;
          for (var j = 0; j <= pa; j++) piece(fi[j][0], fi[j][1], false);
          piece(fi[pa][0], pl, true);
          u0 = fi[pb][0]; py0 = pl; lastX = ax * u0 + bx; lastY = ay * pl + by;
          p.moveTo(lastX, lastY);
          for (var j = pb + 1; j < fi.length; j++) piece(fi[j][0], fi[j][1], false);
        } else {
          for (var j = 1; j < m; j++) { var q = vecLonLat(slerp(va, vb, j / m)); piece(q[0], q[1], false); }
        }
      }
      piece(uu, la, i === n - 1);
    }
  };

  Map2D.prototype.draw = function (t) {
    var a = this.a, ctx = this.ctx, p = this.projRef;
    if (p) {   // zoom/pan or resize that did not (yet) raise an event: re-sync lazily
      var o = p([this.lon0, 0]), n = p([this.lon0, 90]), q = p([this.lon0 + 90, 45]), r = this.ref;
      var rot = typeof p.rotate === 'function' ? p.rotate() : [0, 0, 0];
      if (-(rot && rot[0] || 0) !== this.lon0 || o[0] !== r[0] || o[1] !== r[1] || n[1] !== r[2] ||
          q[0] !== r[3] || q[1] !== r[4] || (window.devicePixelRatio || 1) !== this.dpr) this.sync();
    }
    var k = this.k = seek(a.t_s, t, this.k), k1 = Math.min(k + 1, a.t_s.length - 1), f = frac(a.t_s, t, k);
    // Ship: great-circle interpolation of the unit vectors (no sliding at the antimeridian or the poles).
    var sll = vecLonLat(slerp(lonLatVec(a.lon[k], a.lat[k]), lonLatVec(a.lon[k1], a.lat[k1]), f)),
        slon = sll[0], slat = sll[1];
    if (slon >= 180) slon -= 360;
    this.ship = [slon, slat];

    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.canvas.width / this.dpr, this.canvas.height / this.dpr);
    ctx.save();
    ctx.beginPath();
    ctx.rect(this.clip[0], this.clip[1], this.clip[2], this.clip[3]);
    ctx.clip();

    // Night side + terminator from the subsolar point (same formulas as sun.py).
    var s = sunAt(a, t), d = s[0] * D2R, sunLon = s[1];
    var dd = Math.abs(d) < 1e-3 ? (d >= 0 ? 1e-3 : -1e-3) : d, tn = Math.tan(dd);
    var pole = s[0] >= 0 ? -90 : 90, N = 181, tx = this._tx || (this._tx = new Float64Array(N * 2));
    for (var i = 0; i < N; i++) {
      var uu = -180 + 360 * i / (N - 1);   // longitude relative to the cut meridian
      tx[2 * i] = this.ax * uu + this.bx;
      tx[2 * i + 1] = this.ay * (Math.atan(-Math.cos((this.lon0 + uu - sunLon) * D2R) / tn) / D2R) + this.by;
    }
    ctx.beginPath();
    ctx.moveTo(tx[0], tx[1]);
    for (i = 1; i < N; i++) ctx.lineTo(tx[2 * i], tx[2 * i + 1]);
    ctx.lineTo(tx[2 * N - 2], this.ay * pole + this.by);
    ctx.lineTo(tx[0], this.ay * pole + this.by);
    ctx.closePath();
    ctx.fillStyle = a.colors.night;
    ctx.fill();
    if (this.vis.terminator) {
      ctx.beginPath();
      ctx.moveTo(tx[0], tx[1]);
      for (i = 1; i < N; i++) ctx.lineTo(tx[2 * i], tx[2 * i + 1]);
      ctx.setLineDash([6, 6]); ctx.lineWidth = 1.2; ctx.strokeStyle = a.colors.terminator;
      ctx.stroke(); ctx.setLineDash([]);
    }

    if (this.vis.track) {   // whole track dimmed, flown part bright
      ctx.lineJoin = 'round'; ctx.lineWidth = 2;
      ctx.globalAlpha = 0.35; ctx.strokeStyle = a.colors.track;
      ctx.stroke(this.track);
      ctx.globalAlpha = 1;
      var flown = new Path2D();
      this.tracePath(flown, k, [slon, slat]);
      ctx.stroke(flown);
    }
    if (this.vis.site) {    // same diamond geometry as the Plotly marker beneath (half-diagonal 1.3 * size/2)
      var sx = this.px(a.site.lon), sy = this.py(a.site.lat), rr = 5.85;
      ctx.beginPath();
      ctx.moveTo(sx + rr, sy); ctx.lineTo(sx, sy + rr); ctx.lineTo(sx - rr, sy); ctx.lineTo(sx, sy - rr);
      ctx.closePath();
      ctx.fillStyle = a.colors.site; ctx.fill();
    }
    if (this.vis.ship) {
      ctx.beginPath();
      ctx.arc(this.px(slon), this.py(slat), 6.5, 0, 6.283185307179586);
      ctx.fillStyle = a.colors.ship; ctx.fill();
      ctx.lineWidth = 2; ctx.strokeStyle = a.colors.ship_edge; ctx.stroke();
    }
    ctx.restore();
  };

  Map2D.prototype.onDprChange = function () { this.sync(); if (player) player.request(); };

  Map2D.prototype.destroy = function () {
    var gd = this.gd;
    if (gd.removeListener) {
      gd.removeListener('plotly_relayout', this.onSync);
      gd.removeListener('plotly_relayouting', this.onSync);
      gd.removeListener('plotly_afterplot', this.onSync);
      gd.removeListener('plotly_restyle', this.onRestyle);
    }
    if (this.ro) this.ro.disconnect();
    if (this.canvas.parentNode) this.canvas.parentNode.removeChild(this.canvas);
  };

  /* ---------- 3D: update only what changed ---------- */
  /* Plotly.restyle on a gl3d trace re-converts and re-uploads EVERY trace of the scene (about
   * 100 ms here), so the player updates the gl object of the single changed trace directly
   * (scene.traces[uid].update: 0.4 ms for the ship) and falls back to Plotly.restyle if those
   * internals are missing.
   * Day/night: re-colouring the surface costs 60+ ms even through the direct path, so the Earth is
   * lit instead: the night side is the ambient colour and the sun is a light fixed in the Earth
   * frame. gl-surface3d takes lightPosition in clip space, so draw() is wrapped to convert the
   * sun position with the current camera matrices; moving the sun then costs nothing. If the
   * internals are missing, the surface colours are re-pushed (SHADE_* throttling) instead. */
  function matMul(a, b) {   // column-major 4x4: a * b
    var o = new Array(16);
    for (var c = 0; c < 4; c++) for (var r = 0; r < 4; r++) {
      o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
    }
    return o;
  }

  function Globe3D(gd, a) {
    this.gd = gd; this.a = a; this.k = 0;
    this.scene = gd._fullLayout.scene._scene;
    this.timer = 0; this.lastShadeMs = 0; this.lastPickMs = 0;
    this.sunDir = null;          // unit vector to the sun in the Earth frame
    this.lastSun = sunAt(a, 0);
    // Plotly renders gl3d at plotGlPixelRatio (default 2) regardless of the display; on a 1x display
    // that is 4x the pixels and costs ~7 ms per frame. Never render above the device pixel ratio.
    this.maxRatio = this.scene.pixelRatio || 1;   // Plotly's plotGlPixelRatio
    this.onDprChange();
    this.lit = this.installLight();
    if (!this.lit) this.initFallback();
  }

  /* Pixel ratio = min(plotGlPixelRatio, devicePixelRatio). gl-plot3d resizes its canvas from
   * glplot.pixelRatio on its next tick, so no window resize event is needed. */
  Globe3D.prototype.onDprChange = function () {
    var gp = this.scene.glplot, pr = Math.min(this.maxRatio, window.devicePixelRatio || 1);
    if (gp && this.scene.pixelRatio !== pr) { this.scene.pixelRatio = pr; gp.pixelRatio = pr; }
    if (player) player.request();
  };

  Globe3D.prototype.installLight = function () {
    var self = this, ft = this.gd._fullData[this.a.surface_trace],
        obj = ft && this.scene.traces && this.scene.traces[ft.uid], sf = obj && obj.surface;
    if (!sf || typeof sf.draw !== 'function' || !sf.lightPosition) return false;
    var orig = sf.draw;
    sf.draw = function (params, pickState) {
      var u = self.sunDir, m = params && params.model, v = params && params.view, p = params && params.projection;
      if (u && m && v && p) {
        // The model matrix already holds the data scale; the sun at 1e6 km is effectively at infinity.
        var D = 1e6, t = matMul(p, matMul(v, m)), x = u[0] * D, y = u[1] * D, z = u[2] * D;
        var w = t[3] * x + t[7] * y + t[11] * z + t[15];
        this.lightPosition = [(t[0] * x + t[4] * y + t[8] * z + t[12]) / w,
                              (t[1] * x + t[5] * y + t[9] * z + t[13]) / w,
                              (t[2] * x + t[6] * y + t[10] * z + t[14]) / w];
      }
      return orig.call(this, params, pickState);
    };
    return true;
  };

  /* No lighting hook: switch to the re-coloured, quantised surface (tables mirror figures.py). */
  Globe3D.prototype.initFallback = function () {
    var sh = this.a.shade, nl = sh.n_lat, no = sh.n_lon, n = nl * no;
    this.nl = nl; this.no = no;
    this.gx = new Float32Array(n); this.gy = new Float32Array(n); this.gz = new Float32Array(n);
    for (var i = 0; i < nl; i++) {
      var la = (-90 + 180 * i / (nl - 1)) * D2R;
      for (var j = 0; j < no; j++) {
        var lo = (-180 + 360 * j / (no - 1)) * D2R, m = i * no + j;
        this.gx[m] = Math.cos(la) * Math.cos(lo);
        this.gy[m] = Math.cos(la) * Math.sin(lo);
        this.gz[m] = Math.sin(la);
      }
    }
    window.Plotly.restyle(this.gd, {'lighting.ambient': 1, 'lighting.diffuse': 0, 'lighting.roughness': 1,
      colorscale: [[[0, sh.earth], [1, sh.day]]], cmin: 0, cmax: sh.levels}, [this.a.surface_trace]);
    this.lastSun = [1e3, 1e3];
  };

  /* Day/night level grid (rows = latitudes), the quantised look of the fallback. */
  Globe3D.prototype.shade = function (sun) {
    var sh = this.a.shade, d = sun[0] * D2R, l = sun[1] * D2R;
    var sx = Math.cos(d) * Math.cos(l), sy = Math.cos(d) * Math.sin(l), sz = Math.sin(d);
    var rows = new Array(this.nl);
    for (var i = 0; i < this.nl; i++) {
      var row = new Array(this.no);
      for (var j = 0; j < this.no; j++) {
        var m = i * this.no + j, s = this.gx[m] * sx + this.gy[m] * sy + this.gz[m] * sz;
        row[j] = Math.round((clamp(s / sh.twilight, -1, 1) + 1) * 0.5 * sh.levels);
      }
      rows[i] = row;
    }
    return rows;
  };

  /* Set properties of trace `i` and redraw only that trace. While playing the pick buffer is not
   * refreshed (see below); when paused/stepping/scrubbing it is, so hovering stays correct. */
  Globe3D.prototype.setTrace = function (i, props) {
    var gd = this.gd, ft = gd._fullData && gd._fullData[i], obj = ft && this.scene.traces &&
      this.scene.traces[ft.uid], gp = this.scene.glplot, key;
    for (key in props) if (gd.data[i]) gd.data[i][key] = props[key];   // keep the figure in sync
    if (!this.noDirect && gp && typeof gp.redraw === 'function') {
      try {
        if (ft && ft.visible !== true) {   // hidden in the legend: no gl object to update, but the light still moves
          gp.redraw();
          return;
        }
        if (obj && typeof obj.update === 'function') {
          for (key in props) ft[key] = props[key];
          obj.update(ft);
          // Every update marks its gl object dirty, which makes gl-plot3d re-render the whole scene a
          // second time into the picking buffer (13 ms/frame). While playing, clear the flags so that
          // redraw() only draws, except once per PICK_REFRESH_MS: the pick buffer then follows the ship
          // (otherwise a ship that started on the Launch site would keep covering it). Frames drawn
          // while paused (stop, step, scrub) always refresh it, so hover is right as soon as it stops.
          var now = performance.now();
          if (player && player.playing && now - this.lastPickMs < PICK_REFRESH_MS) {
            for (var j = 0; j < gp.objects.length; j++) gp.objects[j].dirty = false;
          } else {
            this.lastPickMs = now;
          }
          gp.redraw();
          return;
        }
      } catch (e) { this.noDirect = true; }
    }
    var upd = {};
    for (key in props) upd[key] = [props[key]];
    window.Plotly.restyle(gd, upd, [i]);
  };

  Globe3D.prototype.draw = function (t) {
    var a = this.a;
    var k = this.k = seek(a.t_s, t, this.k), k1 = Math.min(k + 1, a.t_s.length - 1), f = frac(a.t_s, t, k);
    var x = a.x[k] + f * (a.x[k1] - a.x[k]), y = a.y[k] + f * (a.y[k1] - a.y[k]),
        z = a.z[k] + f * (a.z[k1] - a.z[k]);
    this.ship = [x, y, z];
    var sun = sunAt(a, t), d = sun[0] * D2R, l = sun[1] * D2R;
    this.sunDir = [Math.cos(d) * Math.cos(l), Math.cos(d) * Math.sin(l), Math.sin(d)];
    this.setTrace(a.ship_trace, {x: [x], y: [y], z: [z]});   // also redraws the lit surface
    if (this.lit) return;
    // Fallback: re-colour only after the sun moved enough, and not too often while playing.
    var ls = this.lastSun, lag = Math.max(Math.abs(sun[0] - ls[0]), Math.abs(sun[1] - ls[1]));
    if (lag > SHADE_THRESHOLD_DEG) {
      var now = performance.now(), wait = this.lastShadeMs + SHADE_COOLDOWN_MS - now,
          playing = player && player.playing;
      if (!playing && wait < 0 || playing && (wait <= 0 || lag >= SHADE_MAX_LAG_DEG)) {
        this.lastSun = sun; this.lastShadeMs = now;
        this.setTrace(a.surface_trace, {surfacecolor: this.shade(sun)});
      } else if (!this.timer) {   // paused/scrubbing: catch up once the cooldown is over
        var self = this;
        this.timer = setTimeout(function () { self.timer = 0; if (player) player.request(); }, Math.max(wait, 16));
      }
    }
  };

  Globe3D.prototype.destroy = function () { if (this.timer) clearTimeout(this.timer); };

  /* ---------- player ---------- */
  function Player(gd, a) {
    var self = this;
    this.gd = gd; this.a = a;
    this.T = a.t_s[a.t_s.length - 1];
    this.t = 0; this.playing = false; this.last = 0; this.raf = 0; this.alive = true;
    this.shownSec = -1;
    this.view = a.view === '2d' ? new Map2D(gd, a) : new Globe3D(gd, a);
    this.tick = function (ts) { self.raf = 0; self.frame(ts); };
    var range = $('pb-range');
    if (range) { range.max = String(this.T); range.value = '0'; }
    setPlaying(false);
    this.request();
  }

  Player.prototype.request = function () {
    if (this.alive && !this.raf) this.raf = requestAnimationFrame(this.tick);
  };

  Player.prototype.frame = function (ts) {
    if (!this.alive) return;
    if (!document.documentElement.contains(this.gd)) { this.destroy(); return; }
    if (this.playing) {
      var dt = this.last ? Math.min((ts - this.last) / 1000, MAX_DT) : 0;
      this.t += dt * speed;
      if (this.t >= this.T) { this.t = this.T; this.playing = false; setPlaying(false); }
    }
    this.last = this.playing ? ts : 0;
    var t0 = performance.now();
    this.view.draw(this.t);
    this.drawMs = performance.now() - t0;
    this.updateUi();
    if (this.playing) this.request();
  };

  Player.prototype.updateUi = function () {
    var sec = Math.floor(this.t);
    if (sec !== this.shownSec) {
      this.shownSec = sec;
      var lab = $('pb-utc');
      if (lab) lab.textContent = utcLabel(this.a.t0_ms, this.t);
    }
    var range = $('pb-range');
    if (range && Math.abs(parseFloat(range.value) - this.t) > 1e-6) range.value = String(this.t);
  };

  Player.prototype.setTime = function (t) {
    this.t = clamp(t, 0, this.T);
    this.last = 0;
    this.request();
  };

  Player.prototype.destroy = function () {
    this.alive = false;
    this.playing = false;
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    this.view.destroy();
  };

  function setPlaying(v) {
    if (player) player.playing = v;
    var b = $('pb-toggle');
    if (!b) return;
    b.textContent = v ? PAUSE : PLAY;
    var txt = v ? 'Pause' : 'Play';
    b.setAttribute('aria-label', txt);
    b.title = txt;
  }

  /* ---------- controls ---------- */
  function ensureControls() {
    var box = $('pb-speed-box');
    if (box && !box.firstChild) {
      var sel = document.createElement('select');
      sel.setAttribute('aria-label', 'Playback speed');
      SPEEDS.forEach(function (s) {
        var o = document.createElement('option');
        o.value = String(s); o.textContent = s + '×';
        if (s === speed) o.selected = true;
        sel.appendChild(o);
      });
      sel.addEventListener('change', function () { speed = parseFloat(sel.value) || DEFAULT_SPEED; });
      box.appendChild(sel);
    }
    var rbox = $('pb-range-box');
    if (rbox && !rbox.firstChild) {
      var r = document.createElement('input');
      r.type = 'range'; r.id = 'pb-range'; r.className = 'pb-range';
      r.min = '0'; r.max = '1'; r.step = 'any'; r.value = '0';
      r.setAttribute('aria-label', 'Time');
      r.addEventListener('input', function () {   // scrubbing sets the time and pauses
        if (!player) return;
        setPlaying(false);
        player.setTime(parseFloat(r.value) || 0);
      });
      rbox.appendChild(r);
    }
  }

  function step(delta) {
    if (!player) return;
    setPlaying(false);
    player.setTime(player.t + delta * (player.a.step_s || 1));
  }

  function toggle() {
    if (!player) return;
    if (player.playing) { setPlaying(false); player.request(); return; }   // one more (paused) frame refreshes picking
    if (player.t >= player.T) player.t = 0;     // pressed at the end: restart from the beginning
    player.last = 0;
    setPlaying(true);
    player.request();
  }

  document.addEventListener('click', function (e) {
    var btn = e.target.closest && e.target.closest('.pb-btn');
    if (!btn) return;
    if (btn.id === 'pb-back') step(-1);
    else if (btn.id === 'pb-fwd') step(1);
    else if (btn.id === 'pb-toggle') toggle();
  });

  /* ---------- devicePixelRatio changes (zoom, moving to another display) ---------- */
  var dprQuery = null;
  function onDprChange() {
    watchDpr();
    if (player && player.view && player.view.onDprChange) player.view.onDprChange();
  }
  function watchDpr() {   // a (resolution: Xdppx) query fires once when the ratio leaves X, so re-arm it
    if (!window.matchMedia) return;
    if (dprQuery && dprQuery.removeEventListener) dprQuery.removeEventListener('change', onDprChange);
    dprQuery = window.matchMedia('(resolution: ' + (window.devicePixelRatio || 1) + 'dppx)');
    if (dprQuery.addEventListener) dprQuery.addEventListener('change', onDprChange);
  }
  watchDpr();

  /* ---------- mounting ---------- */
  function ready(gd) {
    if (typeof gd.on !== 'function' || !gd._fullLayout || !window.Plotly) return false;
    var a = gd.layout && gd.layout.meta && gd.layout.meta.anim;
    if (!a) return false;
    if (a.view === '2d') {
      var sp = gd._fullLayout.geo && gd._fullLayout.geo._subplot;
      return !!(sp && sp.projection && sp.clipRect && gd.querySelector('.svg-container .legend, .svg-container .infolayer'));
    }
    return !!(gd._fullLayout.scene && gd._fullLayout.scene._scene && gd._fullLayout.scene._scene.glplot);
  }

  var BIND_RETRY_MS = 50, BIND_MAX_WAIT_MS = 10000;
  function bind(gd, tries) {
    tries = tries || 0;
    if (gd.__pbBound || !document.documentElement.contains(gd)) return;
    if (!ready(gd)) {
      if (tries * BIND_RETRY_MS >= BIND_MAX_WAIT_MS) {   // no WebGL / no animation data: give up quietly
        gd.__pbWaiting = false;
        if (window.console) console.warn('starship playback: graph not ready after ' + BIND_MAX_WAIT_MS / 1000 + ' s, player not started');
        return;
      }
      setTimeout(function () { bind(gd, tries + 1); }, BIND_RETRY_MS);
      return;
    }
    gd.__pbBound = true;
    ensureControls();
    if (player) player.destroy();
    player = null;
    try {
      player = new Player(gd, gd.layout.meta.anim);   // a freshly mounted graph always starts paused at t = 0
    } catch (e) {   // leave the figure as Plotly drew it; the controls stay inert but the page works
      player = null;
      gd.__pbBound = false;
      var ov = gd.querySelectorAll('.pb-overlay');
      for (var i = 0; i < ov.length; i++) if (ov[i].parentNode) ov[i].parentNode.removeChild(ov[i]);
      if (window.console) console.warn('starship playback: could not start the player', e);
    }
  }

  var scanQueued = false;
  function scan() {
    scanQueued = false;
    ensureControls();
    var gds = document.querySelectorAll('.js-plotly-plot');
    for (var i = 0; i < gds.length; i++) {
      var gd = gds[i];
      if (!gd.__pbBound && !gd.__pbWaiting) { gd.__pbWaiting = true; bind(gd); }
    }
  }
  function queueScan() { if (!scanQueued) { scanQueued = true; setTimeout(scan, 0); } }
  new MutationObserver(function (recs) {   // only node insertions matter (the UTC label changes text often)
    for (var i = 0; i < recs.length; i++) {
      var added = recs[i].addedNodes;
      for (var j = 0; j < added.length; j++) if (added[j].nodeType === 1) { queueScan(); return; }
    }
  }).observe(document.documentElement, {childList: true, subtree: true});
  document.addEventListener('DOMContentLoaded', scan);

  /* Test/debug hook (read-mostly). */
  window.starshipPlayer = {
    get state() { return player ? {t: player.t, T: player.T, playing: player.playing, speed: speed,
                                   ship: player.view.ship, sun: sunAt(player.a, player.t), drawMs: player.drawMs, step_s: player.a.step_s,
                                   utc: utcLabel(player.a.t0_ms, player.t)} : null; },
    get player() { return player; },
    seek: function (t) { if (player) { setPlaying(false); player.setTime(t); } },
    setSpeed: function (s) { speed = s; }
  };
})();
