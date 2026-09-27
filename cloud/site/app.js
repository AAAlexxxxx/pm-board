/* PM Board: cloud dashboard for the Polymarket paper books (cloud/DESIGN.md). Vanilla JS, no build step.
   Reads data/*.json published by GitHub Actions; sends commands back through repository_dispatch. */
'use strict';

const VERSION = '2026-09-27.2';
const CAT_RU = { 'Mentions/Tweets': 'Твиты', Crypto: 'Крипта', Esports: 'Киберспорт', Sports: 'Спорт', Geopolitics: 'Геополитика',
  Elections: 'Выборы', 'US Politics': 'Политика США', 'Econ/Finance': 'Экономика', Culture: 'Культура',
  'Weather/Science': 'Погода/наука', Other: 'Другое' };
const KIND_RU = { price_jump: 'скачок цены', day_move: 'ход за 24ч', volume_spike: 'всплеск объёма', wide_spread: 'широкий спред',
  fav_collapse: 'слом фаворита', negrisk_sell: 'neg-risk: сумма bid > 1', negrisk_dev: 'neg-risk: сумма mid > 1' };
const DECISION_RU = { open: 'сделка', dry: 'сделал бы', skip: 'пропуск', fail: 'ошибка', reject: 'отказ', settle: 'погашение' };
const PRESETS = [['all', 'Все'], ['fav', 'Фавориты'], ['edge', 'Edge+'], ['soon', 'Скоро'], ['move', 'Движение'],
  ['liq', 'Ликвидные'], ['new', 'Новые']];
const SORTS = [['v24', 'объём 24ч'], ['ann', 'годовых'], ['edge', 'edge'], ['days', 'дни до конца'], ['d1', 'ход за 24ч'],
  ['lq', 'ликвидность'], ['v7', 'объём 7д']];

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const LS = {
  get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* private mode */ } },
};
const nf2 = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const nf0 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const isNum = (v) => v != null && !isNaN(v);
const fmt = {
  usd(v, d = 2) { if (!isNum(v)) return '—'; return (v < 0 ? '−$' : '$') + (d ? nf2 : nf0).format(Math.abs(v)); },
  susd(v) { if (!isNum(v)) return '—'; return (v < 0 ? '−' : '+') + '$' + nf2.format(Math.abs(v)); },
  money(v) {
    if (!isNum(v)) return '—';
    const a = Math.abs(v);
    const s = a >= 1e6 ? (a / 1e6).toFixed(1) + 'M' : a >= 1e4 ? (a / 1e3).toFixed(0) + 'k' : a >= 1e3 ? (a / 1e3).toFixed(1) + 'k' : a.toFixed(0);
    return (v < 0 ? '−' : '') + '$' + s;
  },
  px(p) { return isNum(p) ? Number(p).toFixed(3) : '—'; },
  pct(v, d = 1) { if (!isNum(v)) return '—'; return v > 9.99 ? '>999%' : (v * 100).toFixed(d) + '%'; },
  spct(v, d = 1) { if (!isNum(v)) return '—'; return (v < 0 ? '−' : '+') + (Math.abs(v) * 100).toFixed(d) + '%'; },
  cents(v) { if (!isNum(v)) return '—'; return (v < 0 ? '−' : '+') + (Math.abs(v) * 100).toFixed(1) + '¢'; },
  days(d) { if (!isNum(d)) return '—'; if (d < 0) return 'истёк'; if (d < 1) return Math.max(1, Math.round(d * 24)) + 'ч'; return Math.round(d) + 'д'; },
  date(ts) { const t = new Date(ts); return isNaN(t) ? '—' : t.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }); },
  dt(ts) {
    const t = new Date(ts);
    return isNaN(t) ? '—' : t.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }) + ' ' + t.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  },
  ago(ts) {
    const s = (Date.now() - new Date(ts)) / 1000;
    if (isNaN(s)) return '—';
    if (s < 90) return 'только что';
    if (s < 3600) return Math.round(s / 60) + ' мин назад';
    if (s < 86400) return Math.round(s / 3600) + ' ч назад';
    return Math.round(s / 86400) + ' дн назад';
  },
  cls(v) { return v > 0 ? 'pos' : v < 0 ? 'neg' : ''; },
};

const S = {
  tab: LS.get('tab', 'mk'), data: null, error: null, loading: false, scroll: {}, pending: null, pollTimer: null,
  mk: Object.assign({ q: '', preset: 'fav', cats: [], sort: 'v24', games: false, pmin: '', pmax: '', dmin: '', dmax: '', vmin: '', filters: false },
    LS.get('mk', {}), { show: 50 }),
  settings: LS.get('settings', {}),
};

function repoDefaults() {
  const h = location.hostname, parts = location.pathname.split('/').filter(Boolean);
  if (h.endsWith('.github.io')) return { owner: h.split('.')[0], repo: parts[0] || h };
  return { owner: '', repo: '' };
}
function settings() {
  const own = Object.fromEntries(Object.entries(S.settings).filter(([, v]) => v));
  return Object.assign({ token: '' }, repoDefaults(), own);
}
function saveMk() { LS.set('mk', S.mk); }
let debTimer;
function debounce(fn, ms) { clearTimeout(debTimer); debTimer = setTimeout(fn, ms); }
let toastTimer;
function toast(msg, ms = 3000) {
  const t = $('#toast');
  t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, ms);
}

/* ---------- data ---------- */
async function getJSON(name) {
  const r = await fetch(`data/${name}.json`, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${name}.json: HTTP ${r.status}`);
  return r.json();
}
async function loadAll() {
  if (S.loading) return;
  S.loading = true; renderStamp();
  try {
    const [meta, markets, books, engine, alerts] = await Promise.all(['meta', 'markets', 'books', 'engine', 'alerts'].map(getJSON));
    S.data = { meta, markets, books, engine, alerts }; S.error = null;
    indexData();
  } catch (e) { S.error = e.message; }
  S.loading = false;
  render();
  if (S.deep && S.data) {   // deep link: ?c=<condition id> opens the market card once
    const c = S.deep; S.deep = null;
    if (S.byC.has(c)) marketSheet(c);
  }
}
function indexData() {
  const now = Date.now(), m = S.data.markets;
  S.held = {};
  for (const k of ['a', 'b']) for (const p of S.data.books[k].open) (S.held[p.c] = S.held[p.c] || []).push(k === 'a' ? 'A' : 'C');
  for (const r of m.rows) {
    const mid = r.b != null && r.a != null ? (r.b + r.a) / 2 : (r.l != null ? r.l : 0.5);
    r.yes = mid >= 0.5;
    r.fp = r.yes ? r.a : (r.b != null ? +(1 - r.b).toFixed(4) : null);
    r.days = r.end ? (new Date(r.end) - now) / 864e5 : null;
    r.sp = r.a != null && r.b != null ? r.a - r.b : null;
    r.cat = m.cats[r.k];
    r.txt = `${r.q} ${r.e} ${r.gi || ''}`.toLowerCase();
    r.held = S.held[r.c];
  }
  S.byC = new Map(m.rows.map((r) => [r.c, r]));
}

/* ---------- render ---------- */
function render() {
  renderStamp();
  const v = $('#view');
  if (!S.data) {
    v.innerHTML = S.error ? `<div class="empty">Не удалось загрузить данные<br><span class="small">${esc(S.error)}</span></div>` : '<div class="empty">Загрузка…</div>';
    return;
  }
  const R = { mk: renderMarkets, sg: renderSignals, a: () => renderBook('a'), b: () => renderBook('b'), more: renderMore };
  v.innerHTML = (R[S.tab] || renderMarkets)();
  afterRender();
  $$('.tabs button').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === S.tab)));
}
function renderStamp() {
  const el = $('#stamp');
  $('#reload').classList.toggle('spin', S.loading);
  if (!S.data) { el.textContent = S.error ? 'ошибка загрузки' : 'загрузка…'; el.className = 'stamp' + (S.error ? ' err' : ''); return; }
  const m = S.data.meta;
  el.className = 'stamp' + (S.error ? ' err' : '');
  el.textContent = S.pending ? 'ждём облако…' : fmt.ago(m.generated);
}
function afterRender() {
  const el = $('#spark');
  if (el) {
    el.innerHTML = sparkline(S.data.books.history, +el.dataset.idx, +el.dataset.base, el.clientWidth || 340);
    attachSpark(el);
  }
}

/* ---------- markets ---------- */
function filterMarkets() {
  const F = S.mk, rows = S.data.markets.rows;
  const q = F.q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const num = (x) => (x === '' || x == null ? null : Number(x));
  const pmin = num(F.pmin), pmax = num(F.pmax), dmin = num(F.dmin), dmax = num(F.dmax), vmin = num(F.vmin);
  const out = rows.filter((r) => {
    if (!r.acc) return false;
    if (!F.games && r.gm) return false;
    if (F.cats.length && !F.cats.includes(r.cat)) return false;
    if (q.length && !q.every((w) => r.txt.includes(w))) return false;
    if (pmin != null && !(r.fp >= pmin)) return false;
    if (pmax != null && !(r.fp <= pmax)) return false;
    if (dmin != null && !(r.days >= dmin)) return false;
    if (dmax != null && !(r.days <= dmax)) return false;
    if (vmin != null && !(r.v24 >= vmin)) return false;
    switch (F.preset) {
      case 'fav': return r.fp >= 0.85 && r.fp <= 0.99 && r.days >= 1 && r.days <= 150 && r.v7 / 7 >= 20000 && r.b > 0 && r.a < 1;
      case 'edge': return r.edge > 0;
      case 'soon': return r.days != null && r.days > 0 && r.days <= 7 && r.v24 >= 25000;
      case 'move': return Math.abs(r.d1 || 0) >= 0.1 && r.v24 >= 25000;
      case 'liq': return r.v24 >= 100000;
      case 'new': return !!r.cr && (Date.now() - new Date(r.cr)) / 864e5 <= 3;
      default: return true;
    }
  });
  const s = F.sort, desc = s !== 'days';
  const key = (r) => { const v = r[s]; return !isNum(v) ? (desc ? -Infinity : Infinity) : (s === 'd1' ? Math.abs(v) : v); };
  out.sort((x, y) => (desc ? key(y) - key(x) : key(x) - key(y)));
  return out;
}
function renderMarkets() {
  const F = S.mk, list = filterMarkets(), cats = S.data.markets.cats;
  return `
  <div class="search">
    <input type="search" id="q" placeholder="рынок или событие" value="${esc(F.q)}" autocomplete="off" autocapitalize="off">
    <select id="sort" aria-label="Сортировка">${SORTS.map(([k, l]) => `<option value="${k}" ${k === F.sort ? 'selected' : ''}>${l}</option>`).join('')}</select>
  </div>
  <div class="chips">${PRESETS.map(([k, l]) => `<button type="button" class="chip" data-preset="${k}" aria-pressed="${k === F.preset}">${l}</button>`).join('')}</div>
  <div class="chips">${cats.map((c) => `<button type="button" class="chip" data-cat="${esc(c)}" aria-pressed="${F.cats.includes(c)}">${CAT_RU[c] || esc(c)}</button>`).join('')}</div>
  <div class="row"><span class="meta" id="mkcount">${countText(list.length)}</span><button type="button" class="linkbtn" data-action="toggle-filters">${F.filters ? 'Скрыть фильтры' : 'Фильтры'}</button></div>
  ${F.filters ? renderFilters() : ''}
  <div id="mklist" style="display:grid;gap:12px">${listHTML(list)}</div>`;
}
function renderFilters() {
  const F = S.mk;
  return `<div class="card filters">
    <label>Цена фаворита от<input type="number" inputmode="decimal" step="0.01" min="0.5" max="1" data-f="pmin" value="${esc(F.pmin)}" placeholder="0.50"></label>
    <label>до<input type="number" inputmode="decimal" step="0.01" min="0.5" max="1" data-f="pmax" value="${esc(F.pmax)}" placeholder="1.00"></label>
    <label>Дней до конца от<input type="number" inputmode="numeric" data-f="dmin" value="${esc(F.dmin)}" placeholder="0"></label>
    <label>до<input type="number" inputmode="numeric" data-f="dmax" value="${esc(F.dmax)}" placeholder="365"></label>
    <label class="wide">Объём за 24ч не меньше, $<input type="number" inputmode="numeric" data-f="vmin" value="${esc(F.vmin)}" placeholder="0"></label>
    <label class="toggle wide"><input type="checkbox" data-f="games" ${F.games ? 'checked' : ''}> показывать отдельные матчи</label>
    <button type="button" class="linkbtn wide" data-action="reset-filters">Сбросить фильтры и поиск</button>
  </div>`;
}
function listHTML(list) {
  const shown = list.slice(0, S.mk.show);
  return (shown.length ? shown.map(marketCard).join('') : '<div class="empty">Ничего не найдено</div>')
    + (list.length > shown.length ? `<button type="button" class="more" data-action="more">Показать ещё (${nf0.format(list.length - shown.length)})</button>` : '');
}
function renderList() {
  const el = $('#mklist');
  if (!el) return;
  const list = filterMarkets();
  el.innerHTML = listHTML(list);
  const c = $('#mkcount');
  if (c) c.textContent = countText(list.length);
}
function countText(n) { return `${nf0.format(n)} из ${nf0.format(S.data.markets.rows.length)} · снимок ${fmt.dt(S.data.meta.snapshot)}`; }
function marketCard(r) {
  const edge = r.ann != null
    ? `fair ${fmt.px(r.fv)} · <span class="${fmt.cls(r.edge)}">${fmt.cents(r.edge)}</span> · ${fmt.pct(r.ann, 0)} год.`
    : '<span class="dim">fair: нет (вне вселенной)</span>';
  return `<div class="card tap mk" data-c="${esc(r.c)}">
    <div class="l1"><div class="left"><span class="pill cat">${CAT_RU[r.cat] || esc(r.cat)}</span>${r.held ? `<span class="pill held">${r.held.join('')}</span>` : ''}${r.nr ? '<span class="pill cat">nr</span>' : ''}<span class="meta ellipsis">${fmt.days(r.days)}${r.gi ? ' · ' + esc(r.gi) : ''}</span></div><span class="meta num">${fmt.money(r.v24)}</span></div>
    <div class="q">${esc(r.q)}</div>
    ${r.e && r.e !== r.q ? `<div class="meta ellipsis">${esc(r.e)}</div>` : ''}
    <div class="l3"><span><span class="dim">Yes</span> <span class="px">${fmt.px(r.b)}</span><span class="dim"> / </span><span class="px">${fmt.px(r.a)}</span> <span class="pill ${r.yes ? 'yes' : 'no'}">${r.yes ? 'Yes' : 'No'} ${fmt.px(r.fp)}</span></span><span class="num ${fmt.cls(r.d1)}">${r.d1 != null ? fmt.cents(r.d1) : ''}</span></div>
    <div class="l3"><span class="meta">${edge}</span><span class="meta">спред ${r.sp != null ? (r.sp * 100).toFixed(1) + '¢' : '—'}</span></div>
  </div>`;
}
function sideTxt(p) { return p.side === 'buy' ? `Yes @ ${fmt.px(p.px)}` : `No @ ${fmt.px(p.tpx != null ? p.tpx : 1 - p.px)}`; }
function pmLink(slug) { return `<a class="btn ghost" style="text-align:center;display:block" href="https://polymarket.com/event/${encodeURIComponent(slug || '')}" target="_blank" rel="noopener">Открыть на Polymarket ↗</a>`; }

function marketSheet(c) {
  const r = S.byC.get(c);
  if (!r) return toast('Рынка нет в текущем снимке');
  const pos = ['a', 'b'].flatMap((k) => S.data.books[k].open.filter((p) => p.c === c).map((p) => Object.assign({ book: k }, p)));
  openSheet(`
    <h3>${esc(r.q)}</h3>
    <div class="meta">${esc(r.e)}${r.gi ? ' · ' + esc(r.gi) : ''} · ${CAT_RU[r.cat] || esc(r.cat)}${r.nr ? ' · neg-risk' : ''}${r.gm ? ' · матч' : ''}</div>
    <dl class="kv">
      <dt>Yes bid / ask</dt><dd>${fmt.px(r.b)} / ${fmt.px(r.a)}</dd>
      <dt>Последняя сделка</dt><dd>${fmt.px(r.l)}</dd>
      <dt>Фаворит</dt><dd>${r.yes ? 'Yes' : 'No'} по ${fmt.px(r.fp)}</dd>
      <dt>Fair движка</dt><dd>${r.fv != null ? fmt.px(r.fv) : '—'}</dd>
      <dt>Edge после 1¢ и комиссии</dt><dd class="${fmt.cls(r.edge)}">${r.edge != null ? fmt.cents(r.edge) : '—'}</dd>
      <dt>Годовых</dt><dd>${r.ann != null ? fmt.pct(r.ann, 0) : '—'}</dd>
      <dt>Ход 24ч / 7д</dt><dd>${r.d1 != null ? fmt.cents(r.d1) : '—'} / ${r.d7 != null ? fmt.cents(r.d7) : '—'}</dd>
      <dt>Объём 24ч / 7д</dt><dd>${fmt.money(r.v24)} / ${fmt.money(r.v7)}</dd>
      <dt>Ликвидность</dt><dd>${fmt.money(r.lq)}</dd>
      <dt>Комиссия тейкера</dt><dd>${r.fee ? fmt.pct(r.fee, 1) : 'нет'}</dd>
      <dt>Конец</dt><dd>${r.end ? fmt.dt(r.end) + ' (' + fmt.days(r.days) + ')' : '—'}</dd>
      <dt>Создан</dt><dd>${esc(r.cr || '—')}</dd>
    </dl>
    ${pos.map((p) => `<div class="notice">В книге ${p.book === 'a' ? 'A' : 'Claude'}: ${sideTxt(p)} × ${nf0.format(p.qty)}, вложено ${fmt.usd(p.cost)}, P&L <b class="${fmt.cls(p.pnl)}">${fmt.susd(p.pnl)}</b></div>`).join('')}
    ${pmLink(r.s)}
    ${tradeForm(r)}`);
}
function tradeForm(r) {
  const ok = !!settings().token;
  return `<div class="card form" data-trade="${esc(r.c)}">
    <div class="phead"><h2>Бумажная сделка · книга A</h2><span class="note">${ok ? 'через GitHub' : 'нужен токен (вкладка Ещё)'}</span></div>
    <div class="seg"><button type="button" class="buy" data-side="buy" aria-pressed="${r.yes}">Купить Yes ${fmt.px(r.a)}</button><button type="button" class="sell" data-side="sell" aria-pressed="${!r.yes}">Продать Yes ${fmt.px(r.b)}</button></div>
    <div class="grid2"><label class="field">Сумма, $<input type="number" inputmode="decimal" name="usd" value="100" min="1" step="10"></label><label class="field">Лимит Yes (необяз.)<input type="number" inputmode="decimal" name="limit" step="0.001" min="0.001" max="0.999" placeholder="авто ±2¢"></label></div>
    <div class="btnrow"><button type="button" class="btn ghost" data-action="trade-dry">Проверить</button><button type="button" class="btn" data-action="trade-go">Отправить</button></div>
    <div class="meta">Исполнится в облаке по живому стакану через 1–3 минуты; результат появится на вкладках Книга A и Ещё.</div>
  </div>`;
}

/* ---------- books ---------- */
function renderBook(k) {
  const B = S.data.books[k], K = B.kpi, idx = k === 'a' ? 1 : 2;
  const title = k === 'a' ? 'Книга A · ручная' : 'Книга Claude · движок';
  const delta = K.nav - K.start;
  return `
  <div class="card hero"><div class="k">${title} · NAV</div><div class="v">${fmt.usd(K.nav)}</div>
    <div class="d ${fmt.cls(delta)}">${fmt.susd(delta)} · ${fmt.spct(K.ret, 2)} от старта ${fmt.usd(K.start, 0)}</div>
    <div class="spark" id="spark" data-idx="${idx}" data-base="${K.start}"></div></div>
  <div class="kpis">
    ${kpi('Кэш', fmt.usd(K.cash, 0), fmt.pct(K.nav ? K.cash / K.nav : 0, 0) + ' NAV')}
    ${kpi('Вложено', fmt.usd(K.invested, 0), `${K.n_open} позиций`)}
    ${kpi('Нереализ. P&L', fmt.susd(K.unrealized), fmt.spct(K.invested ? K.unrealized / K.invested : 0), fmt.cls(K.unrealized))}
    ${kpi('Реализ. P&L', fmt.susd(K.realized), `${K.wins} / ${K.losses} · ${K.n_closed} закрыто`, fmt.cls(K.realized))}
    ${kpi('При выигрыше всех', fmt.susd(K.max_gain_open), fmt.spct(K.invested ? K.max_gain_open / K.invested : 0))}
    ${k === 'b' ? kpi('Explore', fmt.usd(K.explore, 0), fmt.pct(K.nav ? K.explore / K.nav : 0, 0) + ' NAV, лимит 20%') : kpi('Комиссии', fmt.usd(K.fees), '')}
  </div>
  ${B.by_cat.length ? `<div class="card"><div class="phead"><h2>По категориям</h2><span class="note">красная метка: лимит 30% NAV</span></div>${bars(B.by_cat, K.nav)}</div>` : ''}
  <h2>Открытые позиции · ${B.open.length}</h2>
  ${B.open.length ? B.open.map((p) => positionCard(p, k)).join('') : '<div class="empty">Позиций нет</div>'}
  <details><summary>Закрытые · ${B.closed.length}</summary><div class="inner">${B.closed.length ? `<div class="list">${B.closed.map(closedRow).join('')}</div>` : '<div class="meta">Пока нет</div>'}</div></details>
  <details><summary>Журнал сделок · ${B.trades.length}</summary><div class="inner">${B.trades.length ? `<div class="list">${B.trades.map(tradeRow).join('')}</div>` : '<div class="meta">Пусто</div>'}</div></details>
  ${k === 'b' ? engineLog() : ''}`;
}
function kpi(k, v, s, cls = '') { return `<div class="kpi"><div class="k">${k}</div><div class="v ${cls}">${v}</div><div class="s">${s}</div></div>`; }
function bars(items, nav) {
  const cap = 0.3 * nav, max = Math.max(cap, ...items.map((x) => x.v));
  return `<div class="bars">${items.map((x) => `<div class="bar"><span class="lab">${CAT_RU[x.k] || esc(x.k)}</span><span class="track"><span class="fill" style="width:${(100 * x.v / max).toFixed(1)}%"></span><span class="cap" style="left:${(100 * cap / max).toFixed(1)}%"></span></span><span class="val">${fmt.money(x.v)}</span></div>`).join('')}</div>`;
}
function positionCard(p, k) {
  return `<div class="card tap posc" data-pos="${esc(p.pid)}" data-book="${k}">
    <div class="l1"><div class="left"><span class="pill ${p.side === 'buy' ? 'yes' : 'no'}">${sideTxt(p)}</span> <span class="pill cat">${CAT_RU[p.cat] || esc(p.cat)}</span>${p.tier ? ` <span class="pill ${p.tier}">${p.tier}</span>` : ''}</div><span class="meta">${fmt.days(p.days)}</span></div>
    <div class="q">${esc(p.q)}</div>
    <div class="nums">
      <div><span class="k">Вложено</span><span class="v">${fmt.money(p.cost)}</span></div>
      <div><span class="k">P&L</span><span class="v ${fmt.cls(p.pnl)}">${fmt.susd(p.pnl)}</span></div>
      <div><span class="k">К погаш.</span><span class="v">${fmt.spct(p.ytm)}</span></div>
      <div><span class="k">Годовых</span><span class="v">${fmt.pct(p.ann, 0)}</span></div>
    </div></div>`;
}
function closedRow(p) {
  return `<div><div class="row"><span class="ellipsis">${esc(p.q)}</span><b class="num ${fmt.cls(p.pnl)}">${fmt.susd(p.pnl)}</b></div>
    <div class="row meta"><span>${fmt.date(p.closed)} · ${esc(p.reason || '')}</span><span>${fmt.spct(p.ret)}${p.hold != null ? ' · ' + fmt.days(p.hold) : ''}</span></div></div>`;
}
function tradeRow(t) {
  const what = t.action === 'close'
    ? `закрытие ${nf0.format(t.qty)} по ${fmt.px(t.exit_px)}`
    : `${t.action === 'add' ? 'добавка' : 'открытие'} ${t.side === 'buy' ? 'Yes' : 'No'} × ${nf0.format(t.qty)}${t.cost != null ? ' за ' + fmt.usd(t.cost) : ''}`;
  return `<div><div class="row"><span class="ellipsis">${esc(t.q || t.pid)}</span><span class="num ${t.pnl != null ? fmt.cls(t.pnl) : ''}">${t.pnl != null ? fmt.susd(t.pnl) : ''}</span></div>
    <div class="row meta"><span>${fmt.dt(t.ts)} · ${what}</span></div></div>`;
}
function rulesBlock() {
  return `<details><summary>Правила движка</summary><div class="inner"><dl class="rules">${Object.entries(S.data.engine.rules || {}).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl></div></details>`;
}
function engineLog() {
  const L = S.data.engine.log || [];
  const rows = L.slice(0, 60).map((x) => `<div><div class="row"><span class="ellipsis"><span class="pill ${esc(x.action)}">${DECISION_RU[x.action] || esc(x.action)}</span> ${esc(x.q || x.summary || '')}</span>${x.usd != null ? `<span class="num">${fmt.usd(x.usd, 0)}</span>` : ''}</div>
    <div class="row meta"><span>${fmt.dt(x.ts)}${x.tier ? ' · ' + esc(x.tier) : ''}${x.reason ? ' · ' + esc(x.reason) : ''}${x.pnl != null ? ' · P&L ' + fmt.susd(x.pnl) : ''}</span></div></div>`).join('');
  return `<details><summary>Действия движка · ${L.length}</summary><div class="inner">${rows ? `<div class="list">${rows}</div>` : '<div class="meta">Пусто</div>'}</div></details>${rulesBlock()}`;
}
function positionSheet(book, pid) {
  const p = S.data.books[book].open.find((x) => x.pid === pid);
  if (!p) return;
  const r = S.byC.get(p.c), ok = !!settings().token;
  openSheet(`<h3>${esc(p.q)}</h3>
    <div class="meta">${esc(p.e || '')} · ${CAT_RU[p.cat] || esc(p.cat)}${p.tier ? ` · <span class="pill ${p.tier}">${p.tier}</span>` : ''} · книга ${book === 'a' ? 'A' : 'Claude'}</div>
    <dl class="kv">
      <dt>Позиция</dt><dd>${sideTxt(p)} × ${nf0.format(p.qty)}</dd>
      <dt>Вложено (комиссия ${fmt.usd(p.fee)})</dt><dd>${fmt.usd(p.cost)}</dd>
      <dt>Отметка, bid ${esc(p.outcome || '')}</dt><dd>${fmt.px(p.mark)} · ${fmt.usd(p.value)}</dd>
      <dt>P&L</dt><dd class="${fmt.cls(p.pnl)}">${fmt.susd(p.pnl)} (${fmt.spct(p.ret)})</dd>
      <dt>При выигрыше</dt><dd>${fmt.susd(p.max_gain)} (${fmt.spct(p.ytm)})</dd>
      <dt>Годовых к погашению</dt><dd>${fmt.pct(p.ann, 0)}</dd>
      <dt>Fair при входе</dt><dd>${p.fair0 != null ? fmt.px(p.fair0) : '—'}</dd>
      <dt>Открыта</dt><dd>${fmt.dt(p.opened)}${p.lots > 1 ? ` · ${p.lots} лота` : ''}</dd>
      <dt>Конец</dt><dd>${p.end ? fmt.dt(p.end) + ' (' + fmt.days(p.days) + ')' : '—'}</dd>
      <dt>Отмечена</dt><dd>${p.marked ? fmt.ago(p.marked) : '—'}</dd>
      ${r ? `<dt>Сейчас Yes bid / ask</dt><dd>${fmt.px(r.b)} / ${fmt.px(r.a)}</dd>` : ''}
    </dl>
    ${pmLink(p.s)}
    <div class="card form" data-close="${esc(pid)}" data-book="${book}">
      <div class="phead"><h2>Закрыть по рынку</h2><span class="note">${ok ? 'через GitHub' : 'нужен токен (вкладка Ещё)'}</span></div>
      <div class="btnrow"><button type="button" class="btn ghost" data-action="close-dry">Проверить</button><button type="button" class="btn danger" data-action="close-go">Закрыть позицию</button></div>
    </div>`);
}

/* ---------- sparkline (single series, emphasis on the last point) ---------- */
function sparkline(hist, idx, base, W) {
  const pts = (hist || []).map((h) => [new Date(h[0]).getTime(), h[idx]]).filter((p) => !isNaN(p[0]) && isNum(p[1]));
  if (pts.length < 2) return '<div class="meta" style="padding-top:36px;text-align:center">История NAV появится после нескольких обновлений</div>';
  const H = 96, padL = 4, padR = 58, padT = 10, padB = 14;
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), lo = Math.min(...ys, base), hi = Math.max(...ys, base);
  const span = hi - lo || 1, yl = lo - span * 0.1, yh = hi + span * 0.1;
  const X = (t) => padL + (W - padL - padR) * (x1 > x0 ? (t - x0) / (x1 - x0) : 1);
  const Y = (v) => padT + (H - padT - padB) * (1 - (v - yl) / (yh - yl));
  const d = pts.map((p, i) => (i ? 'L' : 'M') + X(p[0]).toFixed(1) + ' ' + Y(p[1]).toFixed(1)).join(' ');
  const last = pts[pts.length - 1];
  const area = `${d} L${X(last[0]).toFixed(1)} ${Y(yl).toFixed(1)} L${X(pts[0][0]).toFixed(1)} ${Y(yl).toFixed(1)} Z`;
  const geo = JSON.stringify({ W, H, padL, padR, padT, padB, x0, x1, yl, yh });
  const data = JSON.stringify(pts.map((p) => [p[0], +p[1].toFixed(2)]));
  return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" data-pts='${data}' data-geo='${geo}'>
    <line x1="${padL}" x2="${W - padR}" y1="${Y(base).toFixed(1)}" y2="${Y(base).toFixed(1)}" stroke="var(--grid)" stroke-width="1"/>
    <path d="${area}" fill="var(--accent)" fill-opacity="0.10"/>
    <path d="${d}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${X(last[0]).toFixed(1)}" cy="${Y(last[1]).toFixed(1)}" r="4" fill="var(--accent)" stroke="var(--surface)" stroke-width="2"/>
    <text x="${W - padR + 8}" y="${Y(last[1]).toFixed(1)}" dy="4" font-size="11" fill="var(--ink)" font-family="ui-monospace, Menlo, monospace">${fmt.usd(last[1], 0)}</text>
    <text x="${padL}" y="${H - 2}" font-size="10" fill="var(--muted)">${fmt.date(pts[0][0])}</text>
    <text x="${W - padR}" y="${H - 2}" font-size="10" fill="var(--muted)" text-anchor="end">${fmt.date(last[0])}</text>
    <g class="hover" style="display:none"><line y1="${padT}" y2="${H - padB}" stroke="var(--axis)" stroke-width="1"/><circle r="4" fill="var(--accent)" stroke="var(--surface)" stroke-width="2"/></g>
  </svg><div class="tip" hidden></div>`;
}
function attachSpark(el) {
  const svg = el.querySelector('svg');
  if (!svg) return;
  const pts = JSON.parse(svg.dataset.pts), g = JSON.parse(svg.dataset.geo), tip = el.querySelector('.tip'), hov = svg.querySelector('.hover');
  const X = (t) => g.padL + (g.W - g.padL - g.padR) * (g.x1 > g.x0 ? (t - g.x0) / (g.x1 - g.x0) : 1);
  const Y = (v) => g.padT + (g.H - g.padT - g.padB) * (1 - (v - g.yl) / (g.yh - g.yl));
  const show = (ev) => {
    const rect = svg.getBoundingClientRect(), x = (ev.clientX - rect.left) * (g.W / rect.width);
    let best = 0, bd = Infinity;
    pts.forEach((p, i) => { const dd = Math.abs(X(p[0]) - x); if (dd < bd) { bd = dd; best = i; } });
    const p = pts[best], px = X(p[0]), py = Y(p[1]);
    hov.style.display = '';
    const ln = hov.querySelector('line'), c = hov.querySelector('circle');
    ln.setAttribute('x1', px); ln.setAttribute('x2', px); c.setAttribute('cx', px); c.setAttribute('cy', py);
    tip.hidden = false; tip.textContent = `${fmt.dt(p[0])} · ${fmt.usd(p[1])}`;
    tip.style.left = Math.min(Math.max(px * rect.width / g.W, 70), rect.width - 70) + 'px';
  };
  svg.addEventListener('pointermove', show);
  svg.addEventListener('pointerdown', show);
  svg.addEventListener('pointerleave', () => { hov.style.display = 'none'; tip.hidden = true; });
}

/* ---------- signals ---------- */
function renderSignals() {
  const sc = S.data.engine.scan, A = S.data.alerts.rows || [];
  let eng = '<div class="empty">Движок ещё не запускался в облаке</div>';
  if (sc) {
    const acted = sc.rows.filter((r) => r.decision !== 'reject'), rej = sc.rows.filter((r) => r.decision === 'reject');
    eng = `<div class="card"><div class="phead"><h2>Скан движка${sc.dry ? ' (без сделок)' : ''}</h2><span class="note">${fmt.dt(sc.ts)} · NAV ${fmt.usd(sc.nav, 0)}</span></div>
      <div class="meta">${sc.rows.length} оценено · ${acted.length} прошли фильтры · ${sc.traded.length} ${sc.dry ? 'сделал бы' : 'сделок'}${sc.settled.length ? ' · погашено ' + sc.settled.length : ''}</div></div>
      ${acted.map(scanCard).join('')}
      <details><summary>Отказы · ${rej.length}</summary><div class="inner">${rej.map(scanCard).join('') || '<div class="meta">Нет</div>'}</div></details>`;
  }
  return `${eng}<h2>Аномалии за 7 дней · ${A.length}</h2>${A.length ? `<div class="list">${A.slice(0, 150).map(alertRow).join('')}</div>` : '<div class="empty">Тихо</div>'}`;
}
function scanCard(r) {
  return `<div class="card tap" data-c="${esc(r.c)}">
    <div class="row"><span><span class="pill ${esc(r.decision)}">${DECISION_RU[r.decision] || esc(r.decision)}</span> <span class="pill ${esc(r.tier)}">${esc(r.tier)}</span> <span class="pill cat">${CAT_RU[r.cat] || esc(r.cat)}</span></span><span class="meta">${fmt.days(r.days)} · ${fmt.money(r.daily_vol)}/д</span></div>
    <div class="q">${esc(r.q)}</div>
    <div class="small">${esc(r.fav)} ${fmt.px(r.fav_ask)} → fair ${fmt.px(r.fair)} · <span class="${fmt.cls(r.edge)}">${fmt.cents(r.edge)}</span> · ${fmt.pct(r.ann, 0)} год.${r.lo3 != null ? ` · 72ч ${fmt.px(r.lo3)}–${fmt.px(r.hi3)}` : ''}</div>
    <div class="meta">${esc(r.reason)}${r.usd ? ` · ${fmt.usd(r.usd, 0)} по ${fmt.px(r.fill)}` : ''}</div></div>`;
}
function alertRow(a) {
  return `<div ${a.c ? `class="tap" data-c="${esc(a.c)}"` : ''}><div class="row"><span class="ellipsis"><span class="sev s${a.sev}"></span>${KIND_RU[a.kind] || esc(a.kind)}${a.held ? ' <span class="pill held">в книге</span>' : ''}</span><span class="meta">${fmt.dt(a.ts)}</span></div>
    <div class="small">${esc(a.msg)}</div><div class="meta">${CAT_RU[a.cat] || esc(a.cat || '')}</div></div>`;
}

/* ---------- more ---------- */
function renderMore() {
  const m = S.data.meta, st = settings(), la = m.last_action, cal = m.calibration || {};
  return `
  <div class="card"><div class="phead"><h2>Данные</h2><span class="note">v${VERSION}</span></div>
    <dl class="kv"><dt>Снимок рынков</dt><dd>${fmt.dt(m.snapshot)}</dd><dt>Опубликовано</dt><dd>${fmt.dt(m.generated)} · ${esc(m.mode)}</dd>
    <dt>Рынков (7д ≥ $${nf0.format(m.floor)})</dt><dd>${nf0.format(m.n_markets)}</dd><dt>Fair value посчитан</dt><dd>${nf0.format(m.n_fair || 0)} рынков</dd>
    <dt>Калибровка</dt><dd>${esc(cal.built || '—')} · до ${esc(cal.last_entry_day || '—')}</dd></dl></div>
  ${la ? `<div class="notice ${la.ok ? 'ok' : 'bad'}"><b>Последняя команда</b> · ${fmt.dt(la.ts)} · ${esc(la.op)} · книга ${esc(la.book)}${la.dry ? ' · проверка' : ''}<br>${esc(la.summary || la.error || '')}${(la.candidates || []).slice(0, 6).map((c) => `<br>· ${esc(c.q)} ${c.v24 != null ? fmt.money(c.v24) : ''}`).join('')}</div>` : ''}
  <div class="card form" id="settings">
    <h2>Команды в облако</h2>
    <div class="meta">Кнопки ниже и сделки отправляют <span class="mono">repository_dispatch</span> в GitHub Actions. Нужен fine-grained токен с правом «Contents: Read and write» на этот репозиторий. Токен хранится только в этом приложении.</div>
    <div class="grid2"><label class="field">Владелец<input name="owner" value="${esc(st.owner)}" autocapitalize="off" autocomplete="off"></label><label class="field">Репозиторий<input name="repo" value="${esc(st.repo)}" autocapitalize="off" autocomplete="off"></label></div>
    <label class="field">Токен GitHub<input name="token" type="password" value="${esc(st.token)}" autocomplete="off" placeholder="github_pat_…"></label>
    <div class="btnrow"><button type="button" class="btn ghost" data-action="save-settings">Сохранить</button><button type="button" class="btn ghost" data-action="clear-token">Удалить токен</button></div>
    <div class="btnrow"><button type="button" class="btn" data-action="dispatch" data-type="refresh">Обновить сейчас</button><button type="button" class="btn ghost" data-action="dispatch" data-type="scan">Скан движка</button><button type="button" class="btn ghost" data-action="dispatch" data-type="cycle">Цикл Claude</button></div>
    ${st.token ? '<div class="meta">Команда уходит в GitHub Actions; результат на сайте через 2–4 минуты.</div>' : '<div class="notice bad">Кнопки и сделки заработают после ввода токена выше и «Сохранить».</div>'}
  </div>
  <details><summary>На экран «Домой» iPhone</summary><div class="inner small">В Safari нажмите «Поделиться» → «На экран “Домой”». Приложение откроется без адресной строки, последние данные доступны офлайн. Токен вводится уже внутри установленного приложения: у него своё хранилище.</div></details>
  <details><summary>Как это работает</summary><div class="inner small">GitHub Actions каждые 30 минут снимает все открытые рынки Polymarket, переоценивает и гасит позиции обеих книг, считает fair value по калибровке и аномалии; раз в день (10:05 UTC) запускает цикл движка Claude и публикует страницу на GitHub Pages. Деньги бумажные: на Polymarket ничего не отправляется.</div></details>
  ${rulesBlock()}`;
}

/* ---------- cloud commands ---------- */
async function dispatch(type, payload, label) {
  const st = settings();
  if (!st.token || !st.owner || !st.repo) { toast('Нужен токен GitHub: вкладка Ещё → «Команды в облако» → вставить токен → Сохранить', 6000); return false; }
  try {
    const r = await fetch(`https://api.github.com/repos/${encodeURIComponent(st.owner)}/${encodeURIComponent(st.repo)}/dispatches`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${st.token}`, Accept: 'application/vnd.github+json', 'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2022-11-28' },
      body: JSON.stringify({ event_type: type, client_payload: payload || {} }),
    });
    if (r.status === 204) {
      S.pending = { sent: Date.now(), label };
      toast(`${label}: отправлено, результат через 1–3 мин`, 4000);
      poll(); renderStamp();
      return true;
    }
    const t = await r.text();
    toast(`GitHub ответил ${r.status}: ${t.slice(0, 160)}`, 7000);
    return false;
  } catch (e) { toast('Сеть: ' + e.message, 5000); return false; }
}
function poll() {
  clearInterval(S.pollTimer);
  S.pollTimer = setInterval(async () => {
    if (!S.pending) return clearInterval(S.pollTimer);
    if (Date.now() - S.pending.sent > 10 * 60e3) {
      S.pending = null; clearInterval(S.pollTimer); renderStamp();
      return toast('Облако не ответило за 10 минут: проверьте вкладку Actions в GitHub', 7000);
    }
    try {
      const meta = await getJSON('meta');
      if (new Date(meta.generated).getTime() > S.pending.sent - 60e3) {
        const la = meta.last_action, fresh = la && new Date(la.ts).getTime() > S.pending.sent - 60e3;
        S.pending = null; clearInterval(S.pollTimer);
        await loadAll();
        toast(fresh ? (la.ok ? '✓ ' : '✗ ') + (la.summary || la.error) : 'Данные обновлены', 9000);
      }
    } catch (e) { /* keep polling */ }
  }, 20000);
}

async function reloadWithFeedback() {
  const before = S.data && S.data.meta.generated;
  await loadAll();
  if (!S.data) return;
  const g = S.data.meta.generated;
  toast(g === before
    ? `Новых данных на сайте нет: снимок ${fmt.dt(S.data.meta.snapshot)} (${fmt.ago(g)}). Облако обновляет его каждые 30 мин; запустить сейчас: Ещё → «Обновить сейчас»`
    : `Загружен снимок ${fmt.dt(S.data.meta.snapshot)}`, 7000);
}

/* ---------- sheet & events ---------- */
function openSheet(html) { $('#sheet-body').innerHTML = html; $('#sheet').hidden = false; $('#sheet-back').hidden = false; document.body.style.overflow = 'hidden'; }
function closeSheet() { $('#sheet').hidden = true; $('#sheet-back').hidden = true; document.body.style.overflow = ''; }

async function onAction(a, t) {
  switch (a) {
    case 'toggle-filters': S.mk.filters = !S.mk.filters; saveMk(); render(); break;
    case 'reset-filters': Object.assign(S.mk, { pmin: '', pmax: '', dmin: '', dmax: '', vmin: '', games: false, cats: [], q: '', show: 50 }); saveMk(); render(); break;
    case 'more': S.mk.show += 50; renderList(); break;
    case 'save-settings': {
      const f = $('#settings');
      S.settings = { owner: $('[name=owner]', f).value.trim(), repo: $('[name=repo]', f).value.trim(), token: $('[name=token]', f).value.trim() };
      LS.set('settings', S.settings); toast('Сохранено'); render(); break;
    }
    case 'clear-token': S.settings = Object.assign({}, S.settings, { token: '' }); LS.set('settings', S.settings); toast('Токен удалён'); render(); break;
    case 'dispatch': dispatch(t.dataset.type, {}, { refresh: 'Обновление', scan: 'Скан движка', cycle: 'Цикл Claude' }[t.dataset.type] || t.dataset.type); break;
    case 'trade-dry': case 'trade-go': {
      const f = t.closest('[data-trade]'), sideBtn = $('.seg [aria-pressed="true"]', f);
      const side = sideBtn ? sideBtn.dataset.side : 'buy';
      const usd = Number($('[name=usd]', f).value), limit = $('[name=limit]', f).value, r = S.byC.get(f.dataset.trade);
      if (!(usd > 0)) return toast('Укажите сумму');
      if (a === 'trade-go' && !confirm(`${side === 'buy' ? 'Купить Yes' : 'Продать Yes'} на $${usd}\n${r ? r.q : ''}\n\nБумажная сделка в книге A. Отправить?`)) return;
      const ok = await dispatch('trade', { book: 'a', op: 'open', market: f.dataset.trade, side, usd, limit: limit || null, dry: a === 'trade-dry' },
        a === 'trade-dry' ? 'Проверка сделки' : 'Сделка');
      if (ok) closeSheet();
      break;
    }
    case 'close-dry': case 'close-go': {
      const f = t.closest('[data-close]');
      if (a === 'close-go' && !confirm('Закрыть позицию по рынку (бумажно)?')) return;
      const ok = await dispatch('trade', { book: f.dataset.book, op: 'close', pid: f.dataset.close, dry: a === 'close-dry' },
        a === 'close-dry' ? 'Проверка закрытия' : 'Закрытие');
      if (ok) closeSheet();
      break;
    }
    default: break;
  }
}

function bind() {
  $('#reload').addEventListener('click', reloadWithFeedback);
  $$('.tabs button').forEach((b) => b.addEventListener('click', () => {
    S.scroll[S.tab] = window.scrollY;
    S.tab = b.dataset.tab; LS.set('tab', S.tab);
    render();
    window.scrollTo(0, S.scroll[S.tab] || 0);
  }));
  $('#sheet-back').addEventListener('click', closeSheet);
  const view = $('#view');
  view.addEventListener('click', (ev) => {
    const t = ev.target.closest('[data-action],[data-preset],[data-cat],[data-pos],[data-c]');
    if (!t || ev.target.closest('a')) return;
    if (t.dataset.preset) { S.mk.preset = t.dataset.preset; S.mk.show = 50; saveMk(); render(); return; }
    if (t.dataset.cat) {
      const i = S.mk.cats.indexOf(t.dataset.cat);
      if (i >= 0) S.mk.cats.splice(i, 1); else S.mk.cats.push(t.dataset.cat);
      S.mk.show = 50; saveMk(); render(); return;
    }
    if (t.dataset.action) { onAction(t.dataset.action, t); return; }
    if (t.dataset.pos) { positionSheet(t.dataset.book, t.dataset.pos); return; }
    if (t.dataset.c) marketSheet(t.dataset.c);
  });
  view.addEventListener('input', (ev) => {
    const el = ev.target;
    if (el.id === 'q') { S.mk.q = el.value; S.mk.show = 50; saveMk(); debounce(renderList, 150); }
    else if (el.dataset.f) { S.mk[el.dataset.f] = el.type === 'checkbox' ? el.checked : el.value; S.mk.show = 50; saveMk(); debounce(renderList, 150); }
  });
  view.addEventListener('change', (ev) => { if (ev.target.id === 'sort') { S.mk.sort = ev.target.value; saveMk(); renderList(); } });
  $('#sheet').addEventListener('click', (ev) => {
    const t = ev.target.closest('[data-action],[data-side]');
    if (!t) return;
    if (t.dataset.side) { $$('button', t.parentElement).forEach((b) => b.setAttribute('aria-pressed', String(b === t))); return; }
    onAction(t.dataset.action, t);
  });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && S.data && Date.now() - new Date(S.data.meta.generated) > 15 * 60e3) loadAll();
  });
}

function init() {
  const qs = new URLSearchParams(location.search), want = qs.get('tab');   // deep links: ?tab=a, ?c=0x…
  if (want && ['mk', 'sg', 'a', 'b', 'more'].includes(want)) S.tab = want;
  S.deep = qs.get('c');
  bind();
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch(() => {});
    let reloaded = false;   // a new service worker took over: load the new shell once
    navigator.serviceWorker.addEventListener('controllerchange', () => { if (!reloaded) { reloaded = true; location.reload(); } });
  }
  render();
  loadAll();
}
init();
