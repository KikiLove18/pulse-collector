// Пост в бесплатный канал (@pulse_rynka_news): карусель «5 главных новостей по теме» + (pro=1) пост про PRO с кнопкой.
// Запускает воркер (worker/freech.js) через workflow channel.yml в pulse-collector. ИИ не вызывается: новости уже готовы (/api/freech).
// Слайды — снимки настоящих карточек новостей из приложения (функция newsCard), без меню, в рамке 1080×1350.
// Переменные: THEME ru|us|crypto|commodities|x, TARGET test|channel, PRO 0|1, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (тест), INGEST_SECRET.
const { chromium } = require("playwright");

const APP = process.env.APP_URL || "https://app.pulse-rynka.ru";
const BOT = "ime_news_01_bot";
const THEME = process.env.THEME || "ru", TARGET = process.env.TARGET || "test", PRO = process.env.PRO === "1" || THEME === "pro"; // pro — только пост PRO
const CHAT = TARGET === "channel" ? process.env.FREECH_CHAT || "@pulse_rynka_news" : process.env.TELEGRAM_CHAT_ID;
const TITLE = { ru: "🇷🇺 Россия", us: "🇺🇸 США", crypto: "₿ Крипта", commodities: "🛢 Нефть и золото", x: "🗣 Что говорят люди" };
const HEAD = { ru: "🇷🇺 <b>Россия — главное за день</b>", us: "🇺🇸 <b>США — главное за день</b>", crypto: "₿ <b>Крипта — главное за день</b>",
  commodities: "🛢 <b>Нефть и золото — главное за день</b>", x: "🗣 <b>Что говорят люди, которые двигают рынки</b>" };
// пост PRO (19:00): карусель — 1) что двигает Сбербанк сегодня (живой разбор из приложения), 2) схема «как это работает», 3) события на 2 недели,
// 4–5) примеры из закрытого канала, 6) золотой итог «что входит + цена». Рамка PRO — золотая, новости — синие. Владелец 25.10: «сначала дать ценность, потом — что ещё в PRO».
// Текст — без обещаний дохода; цены — как в боте (PRICES в worker/index.js)
// текст — по правилам сильных каналов (владелец 25.10: «лаконично, красиво»): вопрос-крючок → живая польза → одна строка с ценой → одна кнопка
const proText = (name, up, dn) => [
  DAY,
  `👑 <b>Что сейчас двигает ${name}?</b>`,
  [up.length ? `📈 <b>Поддерживают</b> — ${up.join(", ")}` : "", dn.length ? `📉 <b>Давят</b> — ${dn.join(", ")}` : ""].filter(Boolean).join("\n"),
  `Так PRO разбирает любой из 42 активов →`,
  `💎 <b>PRO — от 240 ₽ в месяц</b> · Базовый бесплатно`].filter(Boolean).join("\n\n"); // владелец 25.10: без 🟢🔴 — «дёшево смотрится»
const PRO_ASSET = "SBER";
// день недели и число в каждой подписи (владелец 27.10: «Вторник, 27 октября»)
const dayLabel = (d = new Date()) => { const s = d.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long", timeZone: "Europe/Moscow" }); return s[0].toUpperCase() + s.slice(1); };
const DAY = `🗓 <i>${dayLabel()}</i>`;

// содержимое слайда крупно (владелец 25.10: «читать с телефона, не увеличивая»): кадр 400×500 CSS → 1080×1350; не влезает — чуть уменьшаем, но не меньше 75%
const FIT = `const fw=o.querySelector('.fw'); if(fw){ fw.style.minHeight='0'; fw.style.overflow='hidden'; let z=1; while(fw.scrollHeight>fw.clientHeight+1&&z>0.76){ z-=0.02; [...fw.children].forEach(c=>c.style.zoom=z); } }`;
const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

async function tg(method, form, tries = 3) {
  const r = await fetch(`https://api.telegram.org/bot${process.env.TELEGRAM_BOT_TOKEN}/${method}`, { method: "POST", body: form });
  const j = await r.json();
  if (j.error_code === 429 && tries > 1) { const s = ((j.parameters && j.parameters.retry_after) || 30) + 1; console.log(`${method}: Telegram просит подождать ${s} с`); await new Promise(z => setTimeout(z, s * 1000)); return tg(method, form, tries - 1); } // лимит канала ~20 сообщений в минуту
  if (!j.ok) throw new Error(`${method}: ${j.description}`);
  return j;
}

async function proPost(p, data) {
  const { shots, text: PRO_TEXT } = await proSlides(p, data);
  const html = `<tg-slideshow>${shots.map((_, i) => `<img src="tg://photo?id=s${i}"/>`).join("")}</tg-slideshow>` +
    PRO_TEXT.split("\n\n").map(t => `<p>${t.replace(/\n/g, "<br/>")}</p>`).join("") +
    `<tg-button-row align="left"><tg-button type="url" url="https://t.me/${BOT}?startapp=ch_pro">👑 Оформить PRO</tg-button></tg-button-row>`;
  const f = new FormData();
  f.append("chat_id", CHAT);
  f.append("rich_message", JSON.stringify({ html, media: shots.map((_, i) => ({ id: `s${i}`, media: { type: "photo", media: `attach://f${i}` } })) }));
  shots.forEach((buf, i) => f.append(`f${i}`, new Blob([buf], { type: "image/jpeg" }), `f${i}.jpg`));
  try { const r = await tg("sendRichMessage", f); console.log("пост PRO (слайдшоу) отправлен:", CHAT, "#" + r.result.message_id); }
  catch (e) { // запасной: альбом + подпись (кнопки у альбома нет — ссылка в тексте)
    console.log("слайдшоу PRO не ушло:", e.message, "— отправляю альбомом");
    await sendPhotos(shots, `${PRO_TEXT}\n\n👑 <a href="https://t.me/${BOT}?startapp=ch_pro">Оформить PRO</a>`);
  }
}
// слайды поста PRO: блоки берём из настоящих окон приложения (разбор актива, схема фактора) — с живыми котировками
async function proSlides(p, data) {
  await p.evaluate(async d => { Object.assign(DEMO_STATE, d); await load(); window.hasPro = () => true; window.labLocked = () => false; }, // демо → настоящие данные, всё открыто
    { news: data.news, events: data.events, results: data.results, quotes: data.quotes, outlook: data.outlook, macro: data.macro });
  await p.waitForTimeout(1200);
  const parts = await p.evaluate(k => {
    openAssetLab(k);
    const ax = document.getElementById("axbody"), q = sel => [...ax.querySelectorAll(sel)];
    const name = ax.querySelector(".oxh b")?.textContent || "";
    const head = ax.querySelector(".oxh").outerHTML.replace(/<button class="sheet-x"[\s\S]*?<\/button>/, "");
    const sum = ax.querySelector(".fxsum")?.outerHTML || "";
    const fx = ax.querySelector(".oxf2"); [...fx.children].slice(4).forEach(x => x.remove());
    // события на 2 недели: сильные первыми, одно на день (у ФРС в один день — решение, заявление и пресс-конференция), 4 штуки — по дате
    const now = Date.now(), seen = new Set();
    const evs = EVS.filter(e => !e.res && new Date(e.dt) > now && new Date(e.dt) < now + 14 * 864e5)
      .sort((a, b) => (b.impact === "High") - (a.impact === "High") || /Решение|ставк/i.test(b.title) - /Решение|ставк/i.test(a.title) || new Date(a.dt) - new Date(b.dt))
      .filter(e => { const d = new Date(new Date(e.dt).getTime() + 3 * 3600e3).toISOString().slice(0, 10); return seen.has(d) ? false : seen.add(d); }).slice(0, 4).sort((a, b) => new Date(a.dt) - new Date(b.dt));
    const R = fxRows(AX_CUR.cls), low = t => /^[А-ЯЁ][а-яё]/.test(t) && !/^[А-ЯЁ]{2}/.test(t) ? t[0].toLowerCase() + t.slice(1) : t;
    const fxUp = R.filter(x => x.L && x.L.e === 1).map(x => low(x.f.t)).slice(0, 3), fxDn = R.filter(x => x.L && x.L.e === -1).map(x => low(x.f.t)).slice(0, 3);
    const out = { name, head, sum, fxUp, fxDn, fx: fx.outerHTML, ev: evs.length ? `<div class="evminis">${evs.map(e => evMini(e, 0, "")).join("")}</div>` : "" };
    fxOpen(0);
    out.chainHd = ax.querySelector(".fxhd")?.outerHTML || "";
    out.chainLbl = [...ax.querySelectorAll(".oxlbl")].find(x => /Как это работает/.test(x.textContent))?.outerHTML || "";
    out.chain = ax.querySelector(".fxchain")?.outerHTML || "";
    closeSheet();
    return out;
  }, PRO_ASSET);
  const panel = h => `<div class="prp">${h}</div>`, ft = t => `<div class="ft"><span>${t}</span><span>@ime_news_01_bot</span></div>`;
  const big = (t, s) => `<div class="big">${t}${s ? `<small>${s}</small>` : ""}</div>`;
  const shots = [];
  shots.push(await shot(p, "return a", HEAD_HTML("👑 PRO") + `<div class="fw">${big(`Что двигает ${esc(parts.name)} сегодня`, "так в PRO разобран любой из 42 активов")}${panel(parts.head + parts.sum + parts.fx)}</div>` + ft("1 / 6 · разбор актива"), PRO_CSS));
  shots.push(await shot(p, "return a", HEAD_HTML("👑 PRO") + `<div class="fw">${big("Как это работает", "от решения ЦБ до цены акции — по шагам")}${panel(parts.chainHd + parts.chainLbl + parts.chain)}</div>` + ft("2 / 6 · схема влияния"), PRO_CSS));
  const club = (img, t, s, li) => HEAD_HTML("🔒 Закрытый канал PRO") + `<div class="fw">${big(t, s)}<img class="cimg" src="img/promo/${img}?v=1" alt=""><ul class="cl">${li.map(x => `<li>${x}</li>`).join("")}</ul></div>`;
  // 4) «Неделя впереди» — в стиле поста закрытого канала (Редактор, pro_queue/2026-10-wk4 v4_w1/w2), одной картинкой: дни друг под другом,
  // точки и линии; события — из календаря приложения на 7 дней вперёд (каждый день свежие)
  await p.evaluate(async () => { const l = document.createElement("link"); l.rel = "stylesheet";
    l.href = "https://fonts.googleapis.com/css2?family=Unbounded:wght@800&family=Inter:wght@500;600;700;800&display=swap"; document.head.appendChild(l);
    await new Promise(r => { l.onload = l.onerror = r; setTimeout(r, 5000); });
    await Promise.race([Promise.all([document.fonts.load("800 20px Unbounded"), document.fonts.load("700 14px Inter")]), new Promise(r => setTimeout(r, 6000))]); });
  // владелец 25.10: в бесплатном канале — не вся неделя, а выжимка: 3 главных события (слайд 3) и как они повлияют на рынок (слайд 4).
  // Сценарии — evScen приложения (те же, что в окне события), события — календарь на 7 дней вперёд
  const wk = await p.evaluate(() => {
    const D = t => new Date(new Date(t).getTime() + 3 * 3600e3), day = t => D(t).toISOString().slice(0, 10), now = Date.now();
    const DN = ["ВС", "ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ"], e2 = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
    const num = x => String(x).replace(/(\d)\.(\d)/g, "$1,$2"), seen = new Set();
    const top = EVS.filter(e => new Date(e.dt) > now && new Date(e.dt) < now + 7 * 864e5 && e.impact !== "Low")
      .sort((a, b) => (b.impact === "High") - (a.impact === "High") || (evScen(b).length > 0) - (evScen(a).length > 0) || /ОПЕК|Банк[а]? России/.test(b.title) - /ОПЕК|Банк[а]? России/.test(a.title) || /Решение/.test(b.title) - /Решение/.test(a.title) || (a.dt < b.dt ? -1 : 1))
      .filter(e => { const d = day(e.dt); return seen.has(d) ? false : seen.add(d); }).slice(0, 3).sort((a, b) => a.dt < b.dt ? -1 : 1);
    const when = e => `${DN[D(e.dt).getUTCDay()]} ${D(e.dt).toISOString().slice(8, 10)}.${D(e.dt).toISOString().slice(5, 7)} · ${D(e.dt).toISOString().slice(11, 16)}`;
    const first = t => (t || "").split(/(?<=\.)\s/)[0];
    const range = new Date(day(now) + "T12:00:00Z").toLocaleDateString("ru-RU", { day: "numeric", month: "long", timeZone: "UTC" }) + " — " +
      new Date(day(now + 6 * 864e5) + "T12:00:00Z").toLocaleDateString("ru-RU", { day: "numeric", month: "long", timeZone: "UTC" });
    const head = (b, n) => `<div class="xwb"><img src="img/logo192.png" alt=""><span class="xbn">ПУЛЬС РЫНКА</span><span class="xbday">PRO</span><span class="xbname">${b}</span></div>`;
    // фото к событию — из img/bg приложения (Wikimedia, свободные лицензии), как у Редактора: здание ФРС, Капитолий…
    const pic = e => { const t = e.title;
      if (/ФРС|Уорш/.test(t)) return "fed"; if (/Банк[а]? России|Росстат|ОФЗ|налог|Минфин/i.test(t) || e.region === "ru") return "kremlin";
      if (/Экспирац|BTC|биткоин|крипт/i.test(t) || e.region === "crypto") return "crypto1"; if (/нефт|ОПЕК|EIA|Brent/i.test(t)) return "oil";
      if (/золот/i.test(t)) return "gold"; if (e.kind === "earnings") return /Apple|Alphabet|Microsoft|Amazon|Meta|NVIDIA|Tesla/i.test(t) ? "tech" : "wallst";
      return "capitol"; };
    const ev = top.map((e, i) => `<div class="xe"><div class="xn">${i + 1}</div><div><div class="xt">${when(e)}</div><p>${e2(e.title)}</p>
      <span>${e2(e.forecast ? `ждут ${num(e.forecast)}${e.previous ? ` · было ${num(e.previous)}` : ""}` : first(evDesc(e)))}</span></div><img class="xph" src="img/bg/${pic(e)}.jpg" alt=""></div>`).join("");
    const sc = top.map(e => { const S = evScen(e); return `<div class="xf"><b>${when(e).split(" · ")[0]}</b><div><h3>${e2(e.title)}</h3>${S.length ? S.map(([k, t, d, mv]) =>
      `<div class="xs"><i class="${k}">${k === "up" ? "▲" : "▼"}</i><div><p><b>${e2(t)}</b> — ${e2(d)}</p>${Object.keys(mv).length ? `<div class="xm">${Object.entries(mv).map(([a, v]) => `<span>${AN[a] || a} <em class="${v > 0 ? "u" : "d"}">${v > 0 ? "↑" : "↓"}</em></span>`).join("")}</div>` : ""}</div></div>`).join("")
      : `<p class="xd">${e2(first(evDesc(e)))}</p>`}</div></div>`; }).join("");
    return { n: top.length, week: head("НЕДЕЛЯ ВПЕРЕДИ") + `<div class="fw"><div class="xwh"><div class="xwk">${range}</div><h1>Неделя впереди</h1><div class="xsub">3 события, которые сдвинут рынок</div></div><div class="xes">${ev}</div></div>`,
      scen: `<div class="fw"><div class="xfh">Как это повлияет на рынок</div><div class="xfs">${sc}</div><div class="xnote">Полный разбор — в закрытом канале по понедельникам · не инвестиционная рекомендация</div></div>` };
  });
  if (wk.n) {
    shots.push(await shot(p, "return a", wk.week + ft("3 / 6 · закрытый канал"), WEEK_CSS));
    shots.push(await shot(p, "return a", wk.scen + ft("4 / 6 · закрытый канал"), WEEK_CSS + SCEN_CSS));
  }
  shots.push(await shot(p, "return a", club("club_ex_guide.jpg", "PDF-гайды", "учимся читать рынок — пошагово, со схемами",
    ["ставка ЦБ, инфляция, нефть и рубль", "новый гайд — каждую неделю"]) + ft(`${shots.length + 1} / 6 · закрытый канал`), PRO_CSS));
  // итог — золотой (владелец 25.10: «чтобы сразу было понятно, что это PRO»)
  const LIST = [["📊", "Разбор любого из 42 активов"], ["🔗", "Схема «как это работает»"], ["📅", "События на 2 недели + напоминания"], ["👤", "Все 69 человек, которые двигают рынки"],
    ["🔎", "Поиск по всей Ленте"], ["🔒", "Закрытый канал: неделя впереди, PDF-гайды"]];
  shots.push(await shot(p, "return a", `<div class="hd"><img src="img/logo192.png" alt="">Пульс рынка<span class="th gb">👑 PRO</span></div>
    <div class="fw">${big("Не просто новости —<br>что они сделают с ценой", "")}<div class="gl">${LIST.map(([i, t]) => `<div><span>${i}</span>${t}</div>`).join("")}</div>
    <div class="gp"><b>от 240 ₽</b> в месяц<small>Базовый — бесплатно</small></div></div>` + ft("6 / 6 · жми «Оформить PRO» ↓"), PRO_CSS + GOLD_CSS));
  return { shots, text: proText(esc(parts.name), parts.fxUp, parts.fxDn) };
}
// PRO — золотая рамка (бесплатные новости — синие)
const PRO_CSS = `#fc{background:linear-gradient(165deg,#241904,#5e430f 60%,#8f6a22)}#fc .th{color:#ffd978;opacity:1}
  #fc .big{font-size:24px}#fc .big small{display:block;font-size:13.5px;font-weight:700;opacity:.8;margin-top:6px}
  #fc .prp{background:var(--surface,#fff);border-radius:22px;padding:14px 12px;box-shadow:0 10px 40px rgba(0,0,0,.35);color:var(--ink,#111)}#fc .prp .oxlbl:first-child{margin-top:0}#fc .prp .oxf2 b{color:var(--ink,#111)}
  #fc .prp .oxh{margin-bottom:10px}#fc .prp .fxn{padding:9px 12px}#fc .prp .fxa{height:14px}#fc .prp .evminis{grid-template-columns:minmax(0,1fr);margin:0}#fc .prp .evmini{width:100%!important;max-width:none!important;box-sizing:border-box}#fc .prp .evmini .eb{flex:1;min-width:0}
  #fc .cimg{width:100%;border-radius:14px;box-shadow:0 10px 40px rgba(0,0,0,.4);margin:8px 0 4px}#fc .cl{color:#fff;font-size:16px;font-weight:600;margin:6px 0 0;padding-left:20px;line-height:1.5}
  #fc .full{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}`;
// «Неделя впереди» — как у Редактора (v4.html, .s-week): бежевый фон, красные время и даты, Unbounded + Inter
const WEEK_CSS = `#fc{background:#F4EFE6;color:#1B1B1B;padding:14px 12px 10px;gap:8px}#fc .ft{color:#1B1B1B;opacity:.55}
  #fc .xwb{display:flex;align-items:center;gap:7px;font:700 10.5px Inter;letter-spacing:.08em;color:#6B655B}#fc .xwb img{width:20px;height:20px;border-radius:5px}
  #fc .xbday{margin-left:auto;background:#D9472B;color:#fff;border-radius:99px;padding:3px 9px;font-weight:800}#fc .xbname{color:#1B1B1B;font-weight:800}
  #fc .fw{gap:14px}#fc .xwk{font:600 13px Inter;color:#D9472B;letter-spacing:.04em}
  #fc h1{font:800 34px/1 Unbounded,Inter;letter-spacing:-.03em;margin:6px 0 0}#fc .xsub{font:600 14px Inter;color:#6B655B;margin-top:8px}
  #fc .xes{display:flex;flex-direction:column;gap:9px}#fc .xe{display:grid;grid-template-columns:40px 1fr 76px;gap:10px;align-items:center;background:#fff;border-radius:16px;padding:14px 14px 14px 12px}
  #fc .xn{font:800 34px/1 Unbounded,Inter;color:#D9472B;text-align:center}#fc .xt{font:800 12px Unbounded,Inter;color:#D9472B;letter-spacing:.02em}
  #fc .xe p{margin:5px 0 3px;font:800 19px/1.2 Inter}#fc .xe span{font:500 13px/1.35 Inter;color:#6B655B}#fc .xph{width:76px;height:76px;border-radius:12px;object-fit:cover}`;
// «Как это повлияет на рынок» — как блок «На это обратите внимание» у Редактора: чёрный фон, красные метки
const SCEN_CSS = `#fc{background:#1B1B1B;color:#fff}#fc .ft{color:#fff;opacity:.5}
  #fc .xfh{font:800 22px/1.15 Unbounded,Inter;color:#FF6B4A;letter-spacing:-.02em}#fc .xfs{display:flex;flex-direction:column}
  #fc .xf{display:grid;grid-template-columns:50px 1fr;gap:10px;padding:12px 0;border-top:1px solid #33302B}#fc .xf>b{font:800 12px Unbounded,Inter;color:#FF6B4A;padding-top:3px}
  #fc .xf h3{margin:0 0 6px;font:800 16px/1.2 Inter}#fc .xs{display:grid;grid-template-columns:16px 1fr;gap:4px;margin-top:5px}
  #fc .xs i{font-style:normal;font-size:11px;padding-top:2px}#fc .xs i{color:#FF6B4A}
  #fc .xs p{margin:0;font:500 13px/1.3 Inter;color:#D8D2C8}#fc .xs p b{color:#fff}#fc .xd{margin:0;font:500 13px/1.35 Inter;color:#D8D2C8}
  #fc .xm{display:flex;flex-wrap:wrap;gap:5px;margin-top:4px}#fc .xm span{font:700 11.5px Inter;background:#2A2723;border-radius:99px;padding:3px 8px;color:#EDE7DD}
  #fc .xm em{font-style:normal}#fc .xm em.u{color:#5FD39A}#fc .xm em.d{color:#FF6B4A}#fc .xnote{font:500 11px Inter;color:#8A8377}`;
const GOLD_CSS = `#fc{background:linear-gradient(160deg,#f6dc8e,#e2b24c 50%,#b9832a);color:#2a1c03}#fc .hd,#fc .big,#fc .ft{color:#2a1c03}#fc .ft{opacity:.7}
  #fc .gb{background:#2a1c03;color:#ffd978!important;padding:4px 10px;border-radius:99px;font-size:13px}
  #fc .big{font-size:27px;margin-bottom:10px}#fc .gl{display:grid;gap:7px}#fc .gl div{display:flex;align-items:center;gap:10px;background:rgba(255,255,255,.55);border-radius:14px;padding:9px 12px;font-size:16px;font-weight:700}
  #fc .gl span{font-size:20px}#fc .gp{margin-top:12px;background:#2a1c03;color:#ffd978;border-radius:16px;padding:12px 14px;font-size:16px;font-weight:700;display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
  #fc .gp b{font-size:26px}#fc .gp small{margin-left:auto;color:#f3e3b5;font-size:13px}`;

// рамка слайда (как у подборок) вокруг готового блока приложения; html строится в странице приложения
const FRAME_CSS = `#fc{position:fixed;inset:0;z-index:2147483647;background:linear-gradient(160deg,#1b3360,#3a6ea8);display:flex;flex-direction:column;padding:12px 8px 10px;gap:8px}
  #fc .hd{display:flex;align-items:center;gap:10px;font-weight:800;font-size:14px;color:#fff;padding:0 4px}#fc .hd img{width:26px;height:26px;border-radius:7px}
  #fc .hd .th{margin-left:auto;font-size:14px;font-weight:700;opacity:.8}#fc .card,#fc .past-hero{margin:0!important;box-shadow:0 10px 40px rgba(0,0,0,.35)}#fc .acts,#fc .newpill,#fc .ch,#fc .cd2{display:none}
  #fc .fw{flex:1;display:flex;flex-direction:column;justify-content:center;gap:8px}#fc .ft{display:flex;justify-content:space-between;font-size:13px;opacity:.75;color:#fff}
  #fc .big{color:#fff;font-size:26px;font-weight:900;line-height:1.15;margin:4px 2px 6px}#fc .evmini{width:100%;pointer-events:none}`;
async function shot(p, build, arg, extraCss = "") {
  await p.evaluate(({ css, build, arg, fit }) => {
    document.getElementById("fc")?.remove();
    const o = document.createElement("div");
    o.id = "fc";
    o.innerHTML = `<style>${css}</style>` + new Function("a", build)(arg);
    document.body.appendChild(o);
    new Function("o", fit)(o);
  }, { css: FRAME_CSS + extraCss, build, arg, fit: FIT });
  await p.waitForTimeout(800);
  const buf = await p.locator("#fc").screenshot({ type: "jpeg", quality: 88 });
  require("fs").mkdirSync("out", { recursive: true }); require("fs").writeFileSync(`out/${THEME}_${Date.now()}.jpg`, buf); // для проверки (артефакт запуска)
  return buf;
}
async function sendPhotos(shots, caption) {
  const fd = new FormData();
  fd.append("chat_id", CHAT);
  if (shots.length === 1) {
    fd.append("photo", new Blob([shots[0]], { type: "image/jpeg" }), "s0.jpg");
    fd.append("caption", caption);
    fd.append("parse_mode", "HTML");
    return tg("sendPhoto", fd);
  }
  fd.append("media", JSON.stringify(shots.map((_, i) => ({ type: "photo", media: `attach://s${i}`, ...(i ? {} : { caption, parse_mode: "HTML" }) }))));
  shots.forEach((buf, i) => fd.append(`s${i}`, new Blob([buf], { type: "image/jpeg" }), `s${i}.jpg`));
  return tg("sendMediaGroup", fd);
}
// «карусель» (владелец 25.10: «как в инсте, листать вправо-влево»): один пост с картинкой и кнопками ◀ 1/5 ▶ — картинку меняет воркер.
// Картинки сначала загружаем в Telegram (тестовый канал, без звука, сразу удаляем) — берём их file_id, затем пост и колода → воркеру.
const APP_LINK = `https://t.me/${BOT}?startapp=ch_${THEME}`; // метка ch_<рубрика>: откуда пришёл человек (статистика «Откуда пришли»)
const deckKb = (i, n) => [[{ text: "◀", callback_data: `fc:${(i + n - 1) % n}` }, { text: `${i + 1} / ${n}`, callback_data: `fc:${i}` }, { text: "▶", callback_data: `fc:${(i + 1) % n}` }],
  [{ text: "📱 Все новости — в приложении", url: APP_LINK }]];
async function deckPost(shots, caption) {
  const up = new FormData();
  up.append("chat_id", process.env.TELEGRAM_CHAT_ID);
  up.append("disable_notification", "true");
  up.append("media", JSON.stringify(shots.map((_, i) => ({ type: "photo", media: `attach://s${i}` }))));
  shots.forEach((buf, i) => up.append(`s${i}`, new Blob([buf], { type: "image/jpeg" }), `s${i}.jpg`));
  const msgs = (await tg("sendMediaGroup", up)).result;
  const fids = msgs.map(m => m.photo[m.photo.length - 1].file_id);
  const del = new FormData();
  del.append("chat_id", process.env.TELEGRAM_CHAT_ID);
  del.append("message_ids", JSON.stringify(msgs.map(m => m.message_id)));
  await tg("deleteMessages", del).catch(e => console.log("загрузка не удалилась:", e.message));
  const f = new FormData();
  f.append("chat_id", CHAT);
  f.append("photo", fids[0]);
  f.append("caption", caption);
  f.append("parse_mode", "HTML");
  f.append("reply_markup", JSON.stringify({ inline_keyboard: deckKb(0, fids.length) }));
  const post = (await tg("sendPhoto", f)).result;
  const r = await fetch(`${APP}/api/freech/deck`, { method: "POST", headers: { authorization: `Bearer ${process.env.INGEST_SECRET}`, "content-type": "application/json" },
    body: JSON.stringify({ chat: post.chat.id, mid: post.message_id, fids, caption, url: APP_LINK }) });
  console.log("пост-карусель отправлен:", CHAT, "колода у воркера:", r.status);
}
// настоящая карусель (Bot API 10.3, sendRichMessage + <tg-slideshow>): листается вправо-влево прямо в ленте — как пример владельца (25.10)
async function richPost(shots, caption) {
  const html = `<tg-slideshow>${shots.map((_, i) => `<img src="tg://photo?id=s${i}"/>`).join("")}</tg-slideshow><p>${caption.replace(/\n/g, "<br/>")}</p>` +
    `<tg-button-row align="left"><tg-button type="url" url="${APP_LINK}">📱 Все новости — в приложении</tg-button></tg-button-row>`;
  const f = new FormData();
  f.append("chat_id", CHAT);
  f.append("rich_message", JSON.stringify({ html, media: shots.map((_, i) => ({ id: `s${i}`, media: { type: "photo", media: `attach://f${i}` } })) }));
  shots.forEach((buf, i) => f.append(`f${i}`, new Blob([buf], { type: "image/jpeg" }), `f${i}.jpg`));
  const r = await tg("sendRichMessage", f);
  console.log("карусель (слайдшоу) отправлена:", CHAT, "#" + r.result.message_id);
}
const LINK = `📱 <a href="https://t.me/${BOT}?startapp=ch_${THEME}">Календарь и все новости — в приложении «Пульс рынка»</a>`;
const HEAD_HTML = t => `<div class="hd"><img src="img/logo192.png" alt="">Пульс рынка<span class="th">${t}</span></div>`;

// 📅 «Сегодня на рынке» (07:00 МСК): события календаря на сегодня — блоки evMini приложения; нет событий — поста нет
async function todayPost(p, events) {
  const dayOf = e => new Date(new Date(e.dt).getTime() + 3 * 3600e3).toISOString().slice(0, 10);
  let day = new Date(Date.now() + 3 * 3600e3).toISOString().slice(0, 10);
  // образец в тестовый канал в день без событий (выходные) — ближайший день с событиями
  if (TARGET === "test" && !events.some(e => dayOf(e) === day)) day = events.map(dayOf).filter(d => d > day).sort()[0] || day;
  const list = events.filter(e => dayOf(e) === day)
    .sort((a, b) => (b.impact === "High") - (a.impact === "High") || (a.dt > b.dt ? 1 : -1)).slice(0, 6).sort((a, b) => a.dt > b.dt ? 1 : -1);
  console.log(`today: ${list.length} событий\n` + list.map(e => "• " + e.title).join("\n"));
  if (!list.length) return console.log("сегодня событий нет — пост не отправлен");
  const label = dayLabel(new Date(day + "T12:00:00+03:00"));
  // 1-й пост дня (владелец 27.10): текст «что ждёт сегодня», за ним — картинка-календарь
  const hm = e => new Date(e.dt).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Moscow" });
  const FL = { ru: "🇷🇺", us: "🇺🇸", oil: "🛢", gold: "🥇", crypto: "₿" };
  const lines = list.map(e => `${e.impact === "High" ? "🔴" : "▫️"} <b>${hm(e)}</b> ${FL[e.region] || ""} ${esc(e.title)}${e.forecast ? ` · прогноз ${esc(e.forecast)}` : ""}`.replace(/  +/g, " "));
  const tf = new FormData();
  tf.append("chat_id", CHAT); tf.append("parse_mode", "HTML");
  tf.append("text", `🗓 <b>${label}</b>\n☀️ <b>Что ждёт рынок сегодня</b>\n\n${lines.join("\n")}\n\nВремя — московское. 🔴 — сильно двигает рынок. Днём — главное по России, США, крипте и сырью, вечером — итоги дня.`);
  tf.append("link_preview_options", JSON.stringify({ is_disabled: true }));
  await tg("sendMessage", tf);
  const img = await shot(p, `EVS = a.list; return \`${HEAD_HTML("📅 Календарь")}<div class="fw"><div class="big">Сегодня на рынке<br><small style="font-size:16px;opacity:.8;font-weight:700">\${a.label}</small></div>\${a.list.map(e => evMini(e, 0, "")).join("")}</div><div class="ft"><span>события дня</span><span>@ime_news_01_bot</span></div>\`;`, { list, label });
  const hi = list.filter(e => e.impact === "High").length;
  await sendPhotos([img], `🗓 <i>${label}</i>\n📅 <b>Сегодня на рынке</b> — ${list.length} ${list.length === 1 ? "событие" : list.length < 5 ? "события" : "событий"}${hi ? `, сильно двигают рынок: ${hi}` : ""}\n\n${LINK}`);
  console.log("сегодня на рынке отправлено");
}

// ✅ «Факт против прогноза»: итоги, которые воркер поставил в очередь (res_queue) — блок pastCard приложения
async function resultsPost(p, results) {
  console.log(`results: ${results.length}\n` + results.map(r => "• " + r.title + " — " + r.headline).join("\n"));
  if (!results.length) return;
  const shots = [];
  for (const r of results) shots.push(await shot(p, `return \`${HEAD_HTML("✅ Факт против прогноза")}<div class="fw">\${pastCard(a, 0)}</div><div class="ft"><span>итог события</span><span>@ime_news_01_bot</span></div>\`;`, r));
  const V = { better: "🟢 лучше ожиданий", worse: "🔴 хуже ожиданий", inline: "⚪ в рамках ожиданий", neutral: "⚪ в рамках ожиданий" };
  const cap = results.length === 1 ? `✅ <b>${esc(results[0].title)}</b>\n${esc(results[0].headline)} · ${V[results[0].verdict] || ""}`
    : `✅ <b>Факт против прогноза</b>\n` + results.map(r => `• ${esc(r.title)}: ${esc(r.headline)}`).join("\n");
  await sendPhotos(shots, `${DAY}\n${cap}\n\n${LINK}`);
  console.log("факт против прогноза отправлено");
}

// inspect — разбор поста тестового канала (ARG: «582» или «580-586»; из другого канала — «@pulse_rynka_news:4»): пересылаем копию в тестовый канал, печатаем устройство, копию удаляем
async function inspect() {
  const arg = String(process.env.ARG || ""), from = arg.includes(":") ? arg.split(":")[0] : process.env.TELEGRAM_CHAT_ID;
  const [a, b2] = arg.split(":").pop().split("-").map(Number);
  if (from !== process.env.TELEGRAM_CHAT_ID) { const g = new FormData(); g.append("chat_id", from); const c = (await tg("getChat", g)).result; console.log("канал:", c.id, c.type, "protected:", !!c.has_protected_content); }
  for (let id = a; id <= (b2 || a); id++) {
    const f = new FormData();
    f.append("chat_id", process.env.TELEGRAM_CHAT_ID); f.append("from_chat_id", from); f.append("message_id", String(id)); f.append("disable_notification", "true");
    try {
      const m = (await tg("forwardMessage", f)).result;
      const keys = Object.keys(m).filter(k => !["chat", "from", "sender_chat", "date", "message_id", "forward_origin", "forward_from_chat", "forward_from_message_id", "forward_date", "forward_signature", "is_automatic_forward"].includes(k));
      console.log(`#${id}:`, JSON.stringify(Object.fromEntries(keys.map(k => [k, k === "photo" ? m.photo.map(p => `${p.width}x${p.height}`) : m[k]])), null, 1).slice(0, 3000));
      const d = new FormData(); d.append("chat_id", process.env.TELEGRAM_CHAT_ID); d.append("message_id", String(m.message_id));
      await tg("deleteMessage", d);
    } catch (e) { console.log(`#${id}: ${e.message}`); }
  }
}

// 5 главных новостей темы → слайды (снимки карточек приложения)
async function deckShots(p, theme, news, used) {
  // подбор в странице — там же списки людей приложения (PERSON_RE)
  const pick = await p.evaluate(({ news, used, theme }) => {
    // без войны и происшествий (канал — про рынки), без повторов, сильные первыми; «люди» — новости с нашими персонами, по одной на человека
    // \b в JS не работает с русскими буквами — границы слова через [^а-яё]
    const BAD = /беспилот|БПЛА|ПВО|дрон|теракт|жертв|погиб|ранен|землетряс|протест|митинг|ракет|обстрел|удар|Минобороны|атак|военн|войн|армия|ВСУ|фронт|взрыв|эвакуир|хуситы|заложник|убит|арест|(^|[^а-яё])суд([^а-яён]|$)|приговор|покушени|подрыв|на минах|эскалац|КСИР|внешн\S* воздейств|ДНР|ЛНР|Херсон|Запорож|Крым|Севастопол|Белгород|Курск|электроснабжен|обесточ|Донбас|спецоперац|СВО(?![а-яё])|мобилизац|санитарн\S* зон|диверс/i;
    const pers = n => Object.keys(PERSON_RE).find(k => PERSON_RE[k].test(n.title || ""));
    const old = new Set(used);
    let L = news.filter(n => n.title && !old.has(n.title) && !BAD.test(n.title + " " + (n.text || "")));
    L = theme === "x" ? L.filter(n => pers(n)) : L.filter(n => n.block === theme);
    L.sort((a, b) => (b.imp || 0) - (a.imp || 0) || (b.card ? 1 : 0) - (a.card ? 1 : 0) || (b.ts > a.ts ? 1 : -1));
    const words = t => new Set(t.toLowerCase().replace(/[^a-zа-яё0-9 ]/gi, " ").split(/\s+/).filter(w => w.length > 3));
    const out = [], seen = new Set();
    for (const n of L) {
      const w = words(n.title);
      if (out.some(o => [...words(o.title)].filter(x => w.has(x)).length >= 2)) continue;
      if (theme === "x") { const k = pers(n); if (seen.has(k)) continue; seen.add(k); }
      out.push(n);
      if (out.length === 5) break;
    }
    return out;
  }, { news, used, theme });
  // карточки крипты до 25.10 ~04:00 МСК — с одинаковым рисованным фоном: показываем вместо них фото приложения (img/bg/crypto*)
  for (const n of pick) if (n.block === "crypto" && n.ts < "2026-10-25T01:00") delete n.card;
  console.log(`${theme}: ${pick.length} новостей\n` + pick.map(n => "• " + n.title).join("\n"));
  if (pick.length < 3) return { pick, shots: [] };

  const shots = [];
  for (let i = 0; i < pick.length; i++) {
    await p.evaluate(({ n, i, total, title, fit }) => {
      NEWS = [n];
      document.getElementById("fc")?.remove();
      const o = document.createElement("div");
      o.id = "fc";
      o.innerHTML = `<style>#fc{position:fixed;inset:0;z-index:2147483647;background:linear-gradient(160deg,#1b3360,#3a6ea8);display:flex;flex-direction:column;padding:12px 8px 10px;gap:8px}
        #fc .hd{display:flex;align-items:center;gap:10px;font-weight:800;font-size:14px;color:#fff;padding:0 4px}#fc .hd img{width:26px;height:26px;border-radius:7px}
        #fc .hd .th{margin-left:auto;font-size:14px;font-weight:700;opacity:.8}#fc .card{margin:0!important;box-shadow:0 10px 40px rgba(0,0,0,.35)}#fc .acts,#fc .newpill,#fc .has-cover>.t,#fc .ntag,#fc .also{display:none}
        #fc .fw{flex:1;display:flex;flex-direction:column;justify-content:center}#fc .ft{display:flex;justify-content:space-between;font-size:13px;opacity:.75;color:#fff}</style>
        <div class="hd"><img src="img/logo192.png" alt="">Пульс рынка<span class="th">${title}</span></div>
        <div class="fw">${newsCard(n, 0)}</div>
        <div class="ft"><span>${i + 1} / ${total}</span><span>@ime_news_01_bot</span></div>`;
      document.body.appendChild(o);
      new Function("o", fit)(o);
    }, { n: pick[i], i, total: pick.length, title: TITLE[theme], fit: FIT });
    await p.waitForTimeout(800); // картинка карточки
    shots.push(await p.locator("#fc").screenshot({ type: "jpeg", quality: 88 }));
  }
  const fs = require("fs"); fs.mkdirSync("out", { recursive: true }); shots.forEach((buf, i) => fs.writeFileSync(`out/${theme}_${i + 1}.jpg`, buf)); // для проверки (артефакт запуска)

  return { pick, shots };
}

// 🎠 карусель дня в боте (владелец 27.10): 4 колоды (Россия, США, крипта, сырьё) → картинки в Telegram (тестовый канал, без звука, сразу удаляем) →
// file_id воркеру (/api/freech/botdeck); людям карусель шлёт воркер в 13:00 по их времени. TARGET=test — воркер ещё присылает образец в тестовый канал.
async function botDecks(p, b, news) {
  const decks = {};
  for (const th of ["ru", "us", "crypto", "commodities"]) {
    const { shots } = await deckShots(p, th, news, []);
    if (shots.length < 3) { console.log(th, "— мало новостей, колоды нет"); continue; }
    const up = new FormData();
    up.append("chat_id", process.env.TELEGRAM_CHAT_ID); up.append("disable_notification", "true");
    up.append("media", JSON.stringify(shots.map((_, i) => ({ type: "photo", media: `attach://s${i}` }))));
    shots.forEach((buf, i) => up.append(`s${i}`, new Blob([buf], { type: "image/jpeg" }), `s${i}.jpg`));
    const msgs = (await tg("sendMediaGroup", up)).result;
    decks[th] = msgs.map(m => m.photo[m.photo.length - 1].file_id);
    const del = new FormData();
    del.append("chat_id", process.env.TELEGRAM_CHAT_ID); del.append("message_ids", JSON.stringify(msgs.map(m => m.message_id)));
    await tg("deleteMessages", del).catch(e => console.log("загрузка не удалилась:", e.message));
  }
  await b.close();
  if (TARGET === "test" && decks.ru) { // образец — как увидит человек (по file_id, без повторной загрузки); в канале вместо web_app — ссылка
    const html = `<tg-slideshow>${decks.ru.map((_, i) => `<img src="tg://photo?id=s${i}"/>`).join("")}</tg-slideshow><p>${DAY}<br/>${HEAD.ru}</p>` +
      `<tg-button-row align="left"><tg-button type="url" url="https://t.me/${BOT}?startapp=deck">📱 Все новости — в приложении</tg-button></tg-button-row>`;
    const f = new FormData(); f.append("chat_id", CHAT);
    f.append("rich_message", JSON.stringify({ html, media: decks.ru.map((fid, i) => ({ id: `s${i}`, media: { type: "photo", media: fid } })) }));
    console.log("образец карусели дня:", CHAT, "#" + (await tg("sendRichMessage", f)).result.message_id);
  }
  const r = await fetch(`${APP}/api/freech/botdeck`, { method: "POST", headers: { authorization: `Bearer ${process.env.INGEST_SECRET}`, "content-type": "application/json" },
    body: JSON.stringify({ decks }) });
  console.log("колоды у воркера:", Object.keys(decks).join(","), r.status, (await r.text()).slice(0, 200));
  if (!r.ok && TARGET !== "test") process.exit(1);
}

// ---------- v2: рубрики по сетке Маркетолога (владелец 27.10, r-1793088029796) — картинка по шаблону channel/freech.html + подпись ----------
// тексты lion/num/word/week/answer/poll — Редактор (meta.freech_posts[дата МСК]); today/week_ahead — календарь; itogi — новости дня. Нет текста — пост пропускаем, сигнал в тестовый канал.
const RUBRICS = ["today", "week_ahead", "lion", "num", "word", "week", "answer", "poll", "itogi"];
const MSK_DAY = () => new Date(Date.now() + 3 * 3600e3).toISOString().slice(0, 10);
const BAD_SRC = /^(США|Россия|Крипта|Сырьё|Нефть и золото|ru|us|crypto|commodities|x)$/i; // это рынок, а не источник
const srcLine = src => src && !BAD_SRC.test(String(src).trim()) ? `Источник: ${esc(src)}` : "";  // подпись «не ИИР» — только в закрепе и описании (Юрист 28.10)
const POLL_SPARE = [{ question: "Куда пойдёт рубль на этой неделе?", options: ["Укрепится", "Ослабнет", "Останется на месте", "Не слежу"] },
  { question: "Что разобрать в следующее воскресенье?", options: ["Вклады и ставки", "Акции РФ", "Валюта", "Крипта"] }];
// v3 (образец 28.10, t-1793172000001): TPL=v3 — шаблон freech3.html (крупнее, фото-фон), сменяющиеся подписи. Боевой — v2, пока владелец не сказал «да».
const V3 = process.env.TPL === "v3";
// фон v3 — фото из bot/bg (Wikimedia PD/CC0) по словам в тексте; нет совпадения — по рубрике
const BG = [[/нефт|brent|wti|опек|баррел/i, ["oil", "oil2", "tanker"]], [/золот|серебр/i, ["gold", "gold2", "gold3"]], [/биткоин|крипт|bitcoin|eth|токен|блокчейн/i, ["crypto4", "crypto5", "crypto12"]],
  [/фрс|пауэлл|уорш|федрезерв/i, ["fed", "fed2", "fed3"]], [/(^|[^а-яё])ии([^а-яё]|$)|nvidia|\bai\b|чип|технолог|anthropic|openai|apple|microsoft/i, ["tech", "tech2"]],
  [/китай|экспорт|импорт|пошлин|торгов|порт/i, ["port", "tanker"]], [/доллар|курс|валют/i, ["dollar"]], [/ipo|акци|nasdaq|s&p|уолл|бирж/i, ["wallst", "wallst2", "wallst3"]],
  [/росси|цб|рубл|мосбирж|минфин|офз|набиуллин/i, ["kremlin", "kremlin2", "kremlin3", "kremlin4", "kremlin5"]]];
const BG_RUB = { today: "wallst", week_ahead: "capitol", lion: "wallst2", num: "port", word: "wallst3", itogi: "kremlin3", week: "whitehouse", answer: "tech" };
const doy = () => Math.floor((Date.now() + 3 * 3600e3) / 864e5);
function bgFor(rub, x) {
  const txt = JSON.stringify(x), hit = BG.find(([re]) => re.test(txt));
  const name = hit ? hit[1][doy() % hit[1].length] : BG_RUB[rub] || "wallst";
  return "../bot/bg/" + name + ".jpg";
}
function rubricData(rub, data) {
  const all = data.posts || {}, last = Object.keys(all).sort().pop(); // образец в тестовый канал: нет текстов на сегодня — берём последние
  const posts = all[MSK_DAY()] || (TARGET === "test" && last ? all[last] : {}), date = dayLabel().toLowerCase();
  const hm = e => new Date(e.dt).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Moscow" });
  const dayOf = e => new Date(new Date(e.dt).getTime() + 3 * 3600e3).toISOString().slice(0, 10);
  const REG = { ru: "ru", us: "us", oil: "commodities", gold: "gold", crypto: "crypto" };
  const evs = (data.events || []).filter(e => e && e.dt && e.title);
  if (rub === "today") {
    const same = (a, b) => hm(a) === hm(b) && (a.title.toLowerCase().includes(b.title.toLowerCase()) || b.title.toLowerCase().includes(a.title.toLowerCase())); // «Базовые заказы…» и «Заказы…» в одно время — одно событие
    const list = evs.filter(e => dayOf(e) === MSK_DAY()).filter((e, i, A) => !A.some((o, j) => j !== i && same(o, e) && (o.title.length < e.title.length || (o.title.length === e.title.length && j < i)))).sort((a, b) => (b.impact === "High") - (a.impact === "High") || (a.dt > b.dt ? 1 : -1)).slice(0, 4).sort((a, b) => a.dt > b.dt ? 1 : -1);
    return list.length ? { rubric: rub, date, title: posts.today?.title || "Что важно сегодня", items: list.map(e => ({ time: hm(e), region: REG[e.region], t: e.title })) } : null;
  }
  if (rub === "week_ahead") {
    const end = new Date(Date.now() + 7 * 864e5).toISOString().slice(0, 10), WD = ["Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"];
    const list = evs.filter(e => e.impact === "High" && dayOf(e) >= MSK_DAY() && dayOf(e) < end).sort((a, b) => a.dt > b.dt ? 1 : -1).slice(0, 5);
    return list.length ? { rubric: rub, title: posts.week_ahead?.title || "Главное на неделе", items: list.map(e => ({ d: WD[new Date(dayOf(e) + "T12:00:00Z").getUTCDay()], t: `${REG[e.region] === "ru" ? "🇷🇺" : REG[e.region] === "us" ? "🇺🇸" : ""} ${e.title} — **${hm(e)}**`.trim() })) } : null;
  }
  if (rub === "itogi") {
    const BAD = /беспилот|БПЛА|ПВО|дрон|теракт|жертв|погиб|ранен|ракет|обстрел|удар|атак|военн|войн|ВСУ|фронт|взрыв|арест|приговор|СВО(?![а-яё])/i;
    const L = (data.news || []).filter(n => n.title && n.ts && dayOf({ dt: n.ts }) === MSK_DAY() && ["ru", "us", "crypto", "commodities"].includes(n.block) && !BAD.test(n.title + " " + (n.text || "")))
      .sort((a, b) => (b.imp || 0) - (a.imp || 0) || (b.ts > a.ts ? 1 : -1));
    const out = [];
    for (const m of ["ru", "us", "commodities", "crypto"]) { const n = L.find(x => x.block === m); if (n) out.push(n); }
    return out.length >= 3 ? { rubric: rub, date, items: out.map(n => ({ region: n.block, t: n.title })) } : null;
  }
  if (rub === "poll") return posts.poll?.question && (posts.poll.options || []).length >= 2 ? posts.poll : POLL_SPARE[Math.floor(Date.now() / 6048e5) % 2];
  return posts[rub] ? { ...posts[rub], rubric: rub } : null;
}
const CAPTION = {
  today: x => `☀️ <b>${esc(x.title)}</b>\nВремя — московское.`,
  week_ahead: x => `☀️ <b>${esc(x.title)}</b>\nВремя — московское.`,
  lion: x => `🦁 <b>${esc(x.title)}</b>\n\nЛев разложил по шагам — листайте картинку.${srcLine(x.src) ? `\n\n<i>${srcLine(x.src)}</i>` : ""}`,
  num: x => `🔢 <b>Цифра дня: ${esc(x.value)}</b>${x.title ? "\n" + esc(x.title) : ""}${srcLine(x.src) ? `\n\n<i>${srcLine(x.src)}</i>` : ""}`,
  word: x => `📖 <b>Слово дня — ${esc(x.term)}</b>\nСохраните, чтобы не забыть.${srcLine(x.src) ? `\n\n<i>${srcLine(x.src)}</i>` : ""}`,
  week: () => `🦁 <b>Неделя за 1 минуту</b>\nДля тех, кто пропустил — перешлите другу.`,
  answer: () => `🦁 <b>Лев отвечает</b>\nВопросы — в комментариях или в боте, лучший разберём в воскресенье.`,
  itogi: () => `🌙 <b>Итоги дня</b>\nГлавное по России, США, сырью и крипте — подробно в приложении.`,
};
// v3: 4 стиля подписи по очереди (день + рубрика) — чтобы не надоедало. Без советов и обещаний дохода.
const CAP3 = {
  short: (rub, x) => CAPTION[rub](x), // как сейчас: эмодзи + заголовок
  ask: (rub, x) => ({ num: `🤔 <b>Как думаете, что это за ${esc(x.value)}?</b>\n\n${esc(x.title || "")}${x.text ? " — " + esc(x.text) : ""}`,
    word: `🤔 <b>Слышали слово «${esc(x.term)}»?</b>\n\nОбъясняем за 30 секунд — на картинке.`,
    lion: `🤔 <b>${esc(x.title)} — и что?</b>\n\nЛев разложил по шагам: от новости до вашего кошелька.` })[rub] || CAPTION[rub](x),
  vote: (rub, x) => `${CAPTION[rub](x)}\n\nПонятно объяснили?\n👍 — да, всё ясно\n🤔 — разберите подробнее`,
  lion: (rub, x) => ({ num: `🦁 <b>Лев:</b> «${esc(x.value)} — эту цифру я бы запомнил.${x.title ? " " + esc(x.title) + "." : ""}»`,
    word: `🦁 <b>Лев:</b> «Сегодня в новостях часто звучит «${esc(x.term)}». Рассказываю, что это, без сложных слов».`,
    lion: `🦁 <b>Лев:</b> «${esc(x.title)}. Коротко — почему это важно и кого касается».`,
    itogi: `🦁 <b>Лев:</b> «Вот что было важно сегодня — по одной новости с каждого рынка».`,
    today: `🦁 <b>Лев:</b> «Вот за чем я слежу сегодня. Время — московское».` })[rub] || CAPTION[rub](x),
};
const CAP_ORDER = ["short", "ask", "vote", "lion"];
const capFor = (rub, x) => { const st = CAP_ORDER[(doy() + RUBRICS.indexOf(rub)) % 4]; const c = CAP3[st](rub, x);
  return c + (/^(lion|num|word)$/.test(rub) && st !== "short" && srcLine(x.src) ? `\n\n<i>${srcLine(x.src)}</i>` : ""); };
async function rubricPost(b, rub, data) {
  const x = rubricData(rub, data);
  if (x && V3) x.bg = bgFor(rub, x);
  if (!x) {
    console.log(`${rub}: нет данных на ${MSK_DAY()} — пост пропущен`);
    if (TARGET === "channel" && !["today", "week_ahead"].includes(rub)) { const f = new FormData(); f.append("chat_id", process.env.TELEGRAM_CHAT_ID);
      f.append("text", `⚠️ Бесплатный канал: нет текста рубрики «${rub}» на ${MSK_DAY()} (meta.freech_posts) — пост пропущен. Редактору.`); await tg("sendMessage", f).catch(() => 0); }
    return;
  }
  if (rub === "poll") { // родной опрос Telegram, без картинки
    const f = new FormData(); f.append("chat_id", CHAT); f.append("question", x.question); f.append("is_anonymous", "true");
    f.append("options", JSON.stringify(x.options.slice(0, 4).map(o => ({ text: String(o).slice(0, 100) }))));
    await tg("sendPoll", f); console.log("опрос отправлен:", x.question); return;
  }
  const p = await b.newPage({ viewport: { width: 1080, height: 1350 } });
  p.on("pageerror", e => console.log("page error:", e.message));
  await p.goto("file://" + require("path").join(__dirname, V3 ? "freech3.html" : "freech.html"));
  const fits = await p.evaluate(x => window.render(x), x);
  const buf = await p.locator("#p").screenshot({ type: "jpeg", quality: 88 });
  require("fs").mkdirSync("out", { recursive: true }); require("fs").writeFileSync(`out/${rub}_${Date.now()}.jpg`, buf);
  console.log(rub, fits ? "влезло" : "НЕ ВЛЕЗЛО (ужато до 70%)", JSON.stringify(x).slice(0, 300));
  const f = new FormData();
  f.append("chat_id", CHAT);
  f.append("photo", new Blob([buf], { type: "image/jpeg" }), `${rub}.jpg`);
  f.append("caption", `${DAY}\n${V3 ? capFor(rub, x) : CAPTION[rub](x)}`);
  f.append("parse_mode", "HTML");
  f.append("reply_markup", JSON.stringify({ inline_keyboard: [[{ text: "📱 Все новости — в приложении", url: `https://t.me/${BOT}?start=ch_${rub}` }]] }));
  const r = await tg("sendPhoto", f);
  console.log(`${rub} отправлено:`, CHAT, "#" + r.result.message_id);
}

(async () => {
  if (THEME === "inspect") return inspect();
  const api = await fetch(`${APP}/api/freech${TARGET === "test" ? "?sample=1" : ""}`, { headers: { authorization: `Bearer ${process.env.INGEST_SECRET}` } });
  if (!api.ok) throw new Error("api/freech " + api.status);
  const data = await api.json(), { news, used, events = [], results = [] } = data;
  if (RUBRICS.includes(THEME)) { const b = await chromium.launch(); await rubricPost(b, THEME, data); await b.close();
    if (TARGET === "channel") await fetch(`${APP}/api/freech/done`, { method: "POST", headers: { authorization: `Bearer ${process.env.INGEST_SECRET}`, "content-type": "application/json" }, body: JSON.stringify({ theme: THEME, titles: [] }) });
    return; }
  if (THEME === "pro" || THEME === "today_old" || THEME === "results") {
    const b = await chromium.launch();
    const p = await b.newPage({ viewport: { width: 400, height: 500 }, deviceScaleFactor: 2.7, locale: "ru-RU", timezoneId: "Europe/Moscow" });
    p.on("pageerror", e => console.log("page error:", e.message));
    await p.goto(APP + "/", { waitUntil: "networkidle" });
    await p.waitForTimeout(1500);
    if (THEME === "pro") { await proPost(p, data);
      if (TARGET === "channel") await fetch(`${APP}/api/freech/done`, { method: "POST", headers: { authorization: `Bearer ${process.env.INGEST_SECRET}`, "content-type": "application/json" },
        body: JSON.stringify({ theme: "pro", titles: [] }) }); } else if (THEME === "today_old") await todayPost(p, events); else await resultsPost(p, results);
    await b.close();
    return;
  }

  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 400, height: 500 }, deviceScaleFactor: 2.7, locale: "ru-RU", timezoneId: "Europe/Moscow" });
  p.on("pageerror", e => console.log("page error:", e.message));
  await p.goto(APP + "/", { waitUntil: "networkidle" });
  await p.waitForTimeout(1500);

  if (THEME === "bot") return botDecks(p, b, news);
  const { pick, shots } = await deckShots(p, THEME, news, used);
  if (pick.length < 3) { console.log("мало новостей — пост не отправлен"); await b.close(); return; }

  // подпись короткая — только заголовок (владелец 25.10); ссылка — кнопкой
  const caption = `${DAY}\n${HEAD[THEME]}`;
  await richPost(shots, caption).catch(e => { console.log("слайдшоу не ушло:", e.message, "— запасной вариант: кнопки ◀ ▶"); return deckPost(shots, caption); });
  if (PRO) await proPost(p, data);
  await b.close();
  if (TARGET === "channel") await fetch(`${APP}/api/freech/done`, { method: "POST", headers: { authorization: `Bearer ${process.env.INGEST_SECRET}`, "content-type": "application/json" },
    body: JSON.stringify({ theme: THEME, titles: pick.map(n => n.title) }) });
})().catch(e => { console.error(e); process.exit(1); });
