(function () {
  'use strict';
  var $ = function (s) { return document.querySelector(s); };
  var NS = 'http://www.w3.org/2000/svg';
  var BAND = { calm: 'Calm', normal: 'Normal', elevated: 'Elevated', storm: 'Storm' };
  var LANG = { en: 'English', pt: 'Portuguese', es: 'Spanish', de: 'German', it: 'Italian', tr: 'Turkish' };
  var STATUS = [
    ['clean', 'Clean on first fetch'],
    ['recovered', 'Recovered on re-fetch'],
    ['quarantined', 'Quarantined, awaiting tomorrow'],
    ['confirmed_shift', 'Confirmed shift'],
    ['discarded', 'Discarded, suspected poisoning'],
    ['missing', 'Missing'],
    ['repaired_by_provider', 'Repaired by the data provider']
  ];
  var S, current = 'global', range = 90;

  fetch('data/summary.json', { cache: 'no-cache' })
    .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then(init)
    .catch(function () {
      $('#hero-band').textContent = 'no data yet';
      $('#chart').innerHTML = '<p class="muted">The first readings appear after the baseline period.</p>';
    });

  function seriesBy(code) {
    if (code === 'global') return S.global;
    for (var i = 0; i < S.markets.length; i++) if (S.markets[i].code === code) return S.markets[i];
    return null;
  }
  function day(d) { return Date.parse(d + 'T00:00:00Z'); }
  function fmt(d, opts) {
    return new Date(day(d)).toLocaleDateString('en-US', Object.assign({ month: 'short', day: 'numeric', timeZone: 'UTC' }, opts || {}));
  }
  function el(tag, attrs, parent) {
    var n = document.createElementNS(NS, tag);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }
  function chip(node, band) {
    node.dataset.band = band || '';
    node.textContent = band ? BAND[band] : 'no data';
  }
  function scored(points) { return points.filter(function (p) { return p.s != null && !p.g; }); }
  function copy(btn, field, label) {
    btn.addEventListener('click', function () {
      var t = $(field);
      t.select();
      (navigator.clipboard ? navigator.clipboard.writeText(t.value) : Promise.reject()).catch(function () { document.execCommand('copy'); });
      btn.textContent = 'Copied';
      setTimeout(function () { btn.textContent = label; }, 1600);
    });
  }

  function init(s) {
    S = s;
    var q = new URLSearchParams(location.search).get('m');
    if (q && seriesBy(q)) current = q;
    renderHero();
    renderChips();
    renderGrid();
    select(current, true);
    document.querySelectorAll('.seg button').forEach(function (b) {
      b.addEventListener('click', function () {
        range = +b.dataset.range;
        document.querySelectorAll('.seg button').forEach(function (x) { x.setAttribute('aria-selected', x === b); });
        renderChart();
      });
    });
    copy($('#copy-embed'), '#embed-code', 'Copy code');
    copy($('#copy-cite'), '#cite', 'Copy citation');
    var gen = new Date(S.generated_at);
    $('#generated').textContent = 'Updated ' + gen.toUTCString().replace('GMT', 'UTC');
    var t;
    window.addEventListener('resize', function () { clearTimeout(t); t = setTimeout(renderChart, 120); });
  }

  function renderHero() {
    var g = S.global, l = g.latest;
    var n = scored(g.series).length;
    $('#bulletin').textContent = 'Daily bulletin' + (n ? ' · No. ' + n : '') + (S.latest_date ? ' · ' + fmt(S.latest_date, { weekday: 'short', year: 'numeric' }) : '');
    $('#hero-num').textContent = l && l.s != null ? l.s.toFixed(1) : '–';
    chip($('#hero-band'), l && l.b);
    var meta = 'All markets · ' + (l ? l.n : 0) + ' of ' + S.markets.length + ' reporting';
    if (l && l.p) meta += ' · provisional';
    $('#hero-meta').textContent = meta;

    var pts = scored(g.series);
    var last7 = pts.slice(-7), last30 = pts.filter(function (p) { return day(p.d) > day(S.latest_date) - 30 * 864e5; });
    if (last7.length) $('#stat-avg').textContent = (last7.reduce(function (a, p) { return a + p.s; }, 0) / last7.length).toFixed(1);
    if (last30.length) {
      var hi = last30.reduce(function (a, p) { return p.s > a.s ? p : a; });
      $('#stat-high').innerHTML = '';
      $('#stat-high').appendChild(document.createTextNode(hi.s.toFixed(1) + ' '));
      var sm = document.createElement('small');
      sm.textContent = fmt(hi.d);
      $('#stat-high').appendChild(sm);
    }
    var hot = S.markets.filter(function (m) { return m.latest && m.latest.s != null; })
      .sort(function (a, b) { return b.latest.s - a.latest.s; })[0];
    if (hot) $('#stat-hot').textContent = hot.name;
  }

  function renderChips() {
    var box = $('#series-chips');
    [S.global].concat(S.markets).forEach(function (m) {
      var b = document.createElement('button');
      b.setAttribute('role', 'tab');
      b.dataset.code = m.code;
      b.textContent = m.code === 'global' ? 'All markets' : m.code.toUpperCase();
      b.title = m.name;
      b.addEventListener('click', function () { select(m.code); });
      box.appendChild(b);
    });
  }

  function renderGrid() {
    var g = $('#market-grid');
    S.markets.forEach(function (m) {
      var c = document.createElement('button');
      c.type = 'button';
      c.className = 'card';
      c.dataset.code = m.code;
      var l = m.latest || {};
      c.innerHTML = '<div class="card__top"><span></span><span class="mono"></span></div>' +
        '<div class="card__mid"><span class="card__score"></span><span class="chip"></span></div>';
      c.querySelector('.card__top span').textContent = m.name;
      c.querySelector('.card__top .mono').textContent = m.code.toUpperCase();
      c.querySelector('.card__score').textContent = l.s != null ? l.s.toFixed(1) + (l.p ? '*' : '') : '–';
      chip(c.querySelector('.chip'), l.b);
      c.appendChild(spark(m.series.slice(-30)));
      var foot = document.createElement('div');
      foot.className = 'card__foot';
      foot.textContent = (m.basket || 0) + ' keywords · ' + (LANG[m.language] || m.language);
      c.appendChild(foot);
      c.addEventListener('click', function () { select(m.code); $('#chart-title').scrollIntoView({ behavior: 'smooth', block: 'start' }); });
      g.appendChild(c);
    });
  }

  function spark(pts) {
    var svg = el('svg', { viewBox: '0 0 120 36', preserveAspectRatio: 'none', 'aria-hidden': 'true' });
    var d = '', pen = false;
    pts.forEach(function (p, i) {
      if (p.s == null || p.g) { pen = false; return; }
      var x = pts.length > 1 ? (i / (pts.length - 1)) * 120 : 60, y = 34 - (p.s / 10) * 32;
      d += (pen ? 'L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1);
      pen = true;
    });
    el('path', { d: d, 'vector-effect': 'non-scaling-stroke' }, svg);
    return svg;
  }

  function select(code, initial) {
    current = code;
    var m = seriesBy(code);
    document.querySelectorAll('#series-chips button').forEach(function (b) { b.setAttribute('aria-selected', b.dataset.code === code); });
    document.querySelectorAll('.card').forEach(function (b) { b.setAttribute('aria-pressed', b.dataset.code === code); });
    if (!initial) history.replaceState(null, '', code === 'global' ? location.pathname : '?m=' + code);
    renderChart();
    renderMovers();
    renderQuality();
    renderEmbed(m);
  }

  function renderChart() {
    var m = seriesBy(current), box = $('#chart');
    var name = current === 'global' ? 'All markets' : m.name;
    $('#chart-title').textContent = name + ', last ' + (range === 365 ? 'year' : range + ' days');
    box.innerHTML = '';
    if (!m || !scored(m.series).length) {
      var since = (m && m.series.length) ? ' Collection started ' + fmt(m.series[0].d, { year: 'numeric' }) + '.' : '';
      box.innerHTML = '<p class="muted">Collecting the baseline. Volatility compares two days, so the first reading appears after the second day of data.' + since + '</p>';
      $('#notes').hidden = true;
      $('#legend').textContent = '';
      return;
    }
    var end = day(S.latest_date), start = end - (range - 1) * 864e5;
    var pts = m.series.filter(function (p) { return day(p.d) >= start; });
    var W = Math.max(300, box.clientWidth), H = Math.round(Math.max(230, Math.min(330, W * 0.4)));
    var narrow = W < 560;
    var L = 26, R = narrow ? 12 : 76, T = 34, B = 26, iw = W - L - R, ih = H - T - B;
    var x = function (t) { return L + ((t - start) / Math.max(1, end - start)) * iw; };
    var y = function (s) { return T + ih - (Math.min(10, s) / 10) * ih; };
    var svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': name + ' volatility score over the selected period' }, box);

    var ref = m.reference;
    if (ref && ref.p99) {
      var cuts = [0, 5, 7, 9, 10];  // the score is anchored on the percentiles, so bands sit at fixed scores
      ['calm', 'normal', 'elevated', 'storm'].forEach(function (b, i) {
        var y0 = y(cuts[i]), y1 = y(Math.min(10, cuts[i + 1]));
        if (y0 - y1 < 0.5) return;
        el('rect', { x: L, y: y1, width: iw, height: y0 - y1, 'class': 'band-' + b }, svg);
        if (narrow || y0 - y1 < 12) return;
        var t = el('text', { x: L + iw + 8, y: (y0 + y1) / 2 + 3, 'class': 'band-label', fill: 'var(--' + b + ')' }, svg);
        t.textContent = BAND[b];
      });
    }
    var grid = el('g', { 'class': 'grid' }, svg), axis = el('g', { 'class': 'axis' }, svg);
    [0, 2, 4, 6, 8, 10].forEach(function (v) {
      el('line', { x1: L, x2: L + iw, y1: y(v), y2: y(v) }, grid);
      var t = el('text', { x: L - 8, y: y(v) + 4, 'text-anchor': 'end' }, axis);
      t.textContent = v;
    });
    var ticks = narrow ? 3 : 5;
    for (var i = 0; i < ticks; i++) {
      var tt = start + ((end - start) * i) / (ticks - 1);
      var lab = el('text', { x: x(tt), y: H - 6, 'text-anchor': i === 0 ? 'start' : i === ticks - 1 ? 'end' : 'middle' }, axis);
      lab.textContent = fmt(new Date(tt).toISOString().slice(0, 10), range > 200 ? { year: '2-digit' } : null);
    }

    // annotations: shaded span + numbered tag, explained in the footnotes below the chart
    var notes = $('#notes');
    notes.innerHTML = '';
    var nNote = 0;
    (S.annotations || []).forEach(function (a) {
      var a0 = Math.max(start, day(a.start)), a1 = Math.min(end + 864e5, day(a.end || a.start) + 864e5);
      if (a1 <= start || a0 > end) return;
      nNote++;
      var g = el('g', { 'class': 'ann' }, svg);
      el('rect', { x: x(a0), y: T, width: Math.max(3, x(a1) - x(a0)), height: ih }, g);
      var tag = el('g', { 'class': 'ann-tag' }, svg);
      var label = narrow ? String(nNote) : a.label;
      var tw = narrow ? 18 : Math.min(260, 7 * label.length + 30);
      var tx = Math.min(Math.max(L, x(a0)), L + iw - tw);
      el('rect', { x: tx, y: 6, width: tw, height: 20, rx: 6 }, tag);
      var tx2 = el('text', { x: tx + 8, y: 20 }, tag);
      if (narrow) { tx2.setAttribute('x', tx + 5); var sp = el('tspan', {}, tx2); sp.textContent = nNote; }
      else { tx2.appendChild(document.createTextNode(label + ' ')); var sup = el('tspan', { dy: -4 }, tx2); sup.textContent = nNote; }
      var li = document.createElement('li');
      li.textContent = fmt(a.start, { year: 'numeric' }) + (a.end && a.end !== a.start ? ' – ' + fmt(a.end, { year: 'numeric' }) : '') + '. ' + a.label + (a.note ? '. ' + a.note : '');
      notes.appendChild(li);
    });
    notes.hidden = nNote === 0;

    var d = '', area = '', pen = false, first = null, lastPt = null;
    pts.forEach(function (p) {
      if (p.s == null || p.g) {
        if (pen) area += 'L' + x(day(lastPt.d)).toFixed(1) + ' ' + (T + ih) + 'Z';
        pen = false;
        return;
      }
      var px = x(day(p.d)).toFixed(1), py = y(p.s).toFixed(1);
      if (!pen) { area += 'M' + px + ' ' + (T + ih) + 'L' + px + ' ' + py; first = p; }
      else area += 'L' + px + ' ' + py;
      d += (pen ? 'L' : 'M') + px + ' ' + py;
      pen = true;
      lastPt = p;
    });
    if (pen && lastPt) area += 'L' + x(day(lastPt.d)).toFixed(1) + ' ' + (T + ih) + 'Z';
    el('path', { d: area, 'class': 'area' }, svg);
    el('path', { d: d, 'class': 'line' }, svg);
    pts.forEach(function (p) {
      if (p.p && p.s != null && !p.g) el('circle', { cx: x(day(p.d)), cy: y(p.s), r: 3.5, 'class': 'dot-prov' }, svg);
    });
    if (lastPt) el('circle', { cx: x(day(lastPt.d)), cy: y(lastPt.s), r: 5, 'class': 'today' }, svg);

    var cursor = el('line', { 'class': 'cursor', y1: T, y2: T + ih, visibility: 'hidden' }, svg);
    var tip = document.createElement('div');
    tip.className = 'tip';
    tip.hidden = true;
    box.appendChild(tip);
    var hit = el('rect', { x: L, y: T, width: iw, height: ih, fill: 'transparent' }, svg);
    function move(ev) {
      var r = svg.getBoundingClientRect(), px = ((ev.clientX - r.left) / r.width) * W;
      var t = start + ((px - L) / iw) * (end - start), best = null, bd = Infinity;
      pts.forEach(function (p) { var dd = Math.abs(day(p.d) - t); if (dd < bd) { bd = dd; best = p; } });
      if (!best) return;
      var cx = x(day(best.d));
      cursor.setAttribute('x1', cx); cursor.setAttribute('x2', cx); cursor.setAttribute('visibility', 'visible');
      var txt = fmt(best.d, { year: 'numeric' }) + ' · ';
      if (best.s == null || best.g) txt += 'too little clean data';
      else txt += best.s.toFixed(1) + ' ' + BAND[best.b] + ' · ' + best.n + (current === 'global' ? ' markets' : ' keywords') + (best.p ? ' · provisional' : '');
      tip.textContent = txt;
      tip.hidden = false;
      tip.style.left = Math.min(Math.max((cx / W) * r.width, 100), r.width - 100) + 'px';
      tip.style.top = ((best.s != null && !best.g ? y(best.s) : T + ih / 2) / H) * r.height + 'px';
    }
    hit.addEventListener('pointermove', move);
    hit.addEventListener('pointerdown', move);
    hit.addEventListener('pointerleave', function () { tip.hidden = true; cursor.setAttribute('visibility', 'hidden'); });

    var lg = 'Score 0–10 against this series’ own history';
    if (ref) {
      if (ref.source === 'bootstrap') lg += ' (provisional reference until 30 clean days exist)';
      else if (ref.source === 'calibration') lg += ' (starting reference from a pre-launch SERP sample until 30 clean days exist)';
      else lg += ' (' + ref.source + ', frozen for the month)';
    }
    lg += '. Hollow dots are provisional days; gaps are days with too little clean data.';
    $('#legend').textContent = lg;
  }

  function renderMovers() {
    var tb = $('#movers'), items;
    tb.innerHTML = '';
    if (current === 'global') {
      items = [];
      S.markets.forEach(function (m) { (m.movers || []).forEach(function (k) { items.push({ keyword: k.keyword, v: k.v, code: m.code }); }); });
      items.sort(function (a, b) { return b.v - a.v; });
      items = items.slice(0, 8);
    } else {
      items = (seriesBy(current).movers || []).map(function (k) { return { keyword: k.keyword, v: k.v, code: current }; });
    }
    if (!items.length) { tb.innerHTML = '<tr><td colspan="3" class="muted">No scored keywords yet.</td></tr>'; return; }
    items.forEach(function (k) {
      var tr = document.createElement('tr');
      [k.keyword, k.code.toUpperCase(), k.v.toFixed(1)].forEach(function (v, i) {
        var td = document.createElement('td');
        td.textContent = v;
        if (i === 2) td.className = 'num';
        tr.appendChild(td);
      });
      tb.appendChild(tr);
    });
  }

  function renderQuality() {
    var today = {}, q30 = {};
    if (current === 'global') {
      S.markets.forEach(function (m) { for (var k in m.today) today[k] = (today[k] || 0) + m.today[k]; });
      q30 = S.quality_30d || {};
      $('#quality-for').textContent = '· all markets';
    } else {
      var m = seriesBy(current);
      today = m.today || {};
      q30 = m.quality_30d || {};
      $('#quality-for').textContent = '· ' + current.toUpperCase();
    }
    var max = Math.max(1, today.clean || 0);
    var ul = $('#quality');
    ul.innerHTML = '<li class="q-head"><span class="q-label">Fetch outcome</span><span class="q-bar" style="background:none"></span><span class="q-n">Day</span><span class="q-30">30 d</span></li>';
    STATUS.forEach(function (s) {
      var n = today[s[0]] || 0;
      var li = document.createElement('li');
      li.innerHTML = '<span class="q-label"></span><span class="q-bar"><span></span></span><span class="q-n"></span><span class="q-30"></span>';
      li.querySelector('.q-label').textContent = s[1];
      li.querySelector('.q-bar span').style.width = (n ? Math.max(2, Math.round(n / max * 100)) : 0) + '%';
      li.querySelector('.q-n').textContent = n;
      li.querySelector('.q-30').textContent = q30[s[0]] || 0;
      ul.appendChild(li);
    });
  }

  function renderEmbed(m) {
    var page = (S.site_url || '').replace(/\/?$/, '/');
    var name = current === 'global' ? 'all markets' : m.name;
    var src = page + 'badge/' + current + '.svg';
    $('#embed-code').value = '<a href="' + (S.embed_link_url || page) + '"><img src="' + src + '" alt="iGaming SERP volatility (' + name + ') by SEO Tipsy" height="20"></a>';
    $('#cite').value = 'Source: iGaming SERP Volatility Index (' + name + '), SEO Tipsy, ' + fmt(S.latest_date, { year: 'numeric' }) + '. ' + page + ' (CC BY 4.0)';
    var p = $('#embed-preview');
    p.innerHTML = '';
    var img = document.createElement('img');
    img.src = 'badge/' + current + '.svg?' + S.latest_date;
    img.alt = 'Badge preview for ' + name;
    img.height = 20;
    p.appendChild(img);
  }
})();
