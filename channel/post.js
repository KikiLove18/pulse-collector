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
  `👑 <b>Что сейчас двигает ${name}?</b>`,
  [up.length ? `🟢 Поддерживают: ${up.join(", ")}` : "", dn.length ? `🔴 Давят: ${dn.join(", ")}` : ""].filter(Boolean).join("\n"),
  `Так PRO разбирает любой из 42 активов. Листай карточки →`,
  `<b>PRO — от 240 ₽ в месяц.</b> Базовый — бесплатно.`].filter(Boolean).join("\n\n");
const PRO_ASSET = "SBER";

// содержимое слайда крупно (владелец 25.10: «читать с телефона, не увеличивая»): кадр 400×500 CSS → 1080×1350; не влезает — чуть уменьшаем, но не меньше 75%
const FIT = `const fw=o.querySelector('.fw'); if(fw){ fw.style.minHeight='0'; fw.style.overflow='hidden'; let z=1; while(fw.scrollHeight>fw.clientHeight+1&&z>0.76){ z-=0.02; [...fw.children].forEach(c=>c.style.zoom=z); } }`;
const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

async function tg(method, form) {
  const r = await fetch(`https://api.telegram.org/bot${process.env.TELEGRAM_BOT_TOKEN}/${method}`, { method: "POST", body: form });
  const j = await r.json();
  if (!j.ok) throw new Error(`${method}: ${j.description}`);
  return j;
}

async function proPost(p, data) {
  const { shots, text: PRO_TEXT } = await proSlides(p, data);
  const html = `<tg-slideshow>${shots.map((_, i) => `<img src="tg://photo?id=s${i}"/>`).join("")}</tg-slideshow>` +
    PRO_TEXT.split("\n\n").map(t => `<p>${t.replace(/\n/g, "<br/>")}</p>`).join("") +
    `<tg-button-row align="left"><tg-button type="url" url="https://t.me/${BOT}?startapp=pro">👑 Оформить PRO</tg-button></tg-button-row>`;
  const f = new FormData();
  f.append("chat_id", CHAT);
  f.append("rich_message", JSON.stringify({ html, media: shots.map((_, i) => ({ id: `s${i}`, media: { type: "photo", media: `attach://f${i}` } })) }));
  shots.forEach((buf, i) => f.append(`f${i}`, new Blob([buf], { type: "image/jpeg" }), `f${i}.jpg`));
  try { const r = await tg("sendRichMessage", f); console.log("пост PRO (слайдшоу) отправлен:", CHAT, "#" + r.result.message_id); }
  catch (e) { // запасной: альбом + подпись (кнопки у альбома нет — ссылка в тексте)
    console.log("слайдшоу PRO не ушло:", e.message, "— отправляю альбомом");
    await sendPhotos(shots, `${PRO_TEXT}\n\n👑 <a href="https://t.me/${BOT}?startapp=pro">Оформить PRO</a>`);
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
  if (parts.ev) shots.push(await shot(p, "return a", HEAD_HTML("👑 PRO") + `<div class="fw">${big("Что сдвинет рынок за 2 недели", "события с силой влияния — напомним за час")}${panel(parts.ev)}</div>` + ft("3 / 6 · события"), PRO_CSS));
  const club = (img, t, s, li) => HEAD_HTML("🔒 Закрытый канал PRO") + `<div class="fw">${big(t, s)}<img class="cimg" src="img/promo/${img}?v=1" alt=""><ul class="cl">${li.map(x => `<li>${x}</li>`).join("")}</ul></div>`;
  shots.push(await shot(p, "return a", club("club_ex_week.jpg", "Неделя впереди", "каждый понедельник — что ждёт рынок",
    ["ЦБ, ФРС, отчёты компаний — по дням", "по воскресеньям — разбор недели"]) + ft("4 / 6 · закрытый канал"), PRO_CSS));
  shots.push(await shot(p, "return a", club("club_ex_guide.jpg", "PDF-гайды", "учимся читать рынок — пошагово, со схемами",
    ["ставка ЦБ, инфляция, нефть и рубль", "новый гайд — каждую неделю"]) + ft("5 / 6 · закрытый канал"), PRO_CSS));
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
const APP_LINK = `https://t.me/${BOT}?startapp=ch`;
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
  const html = `<tg-slideshow>${shots.map((_, i) => `<img src="tg://photo?id=s${i}"/>`).join("")}</tg-slideshow><p>${caption}</p>` +
    `<tg-button-row align="left"><tg-button type="url" url="${APP_LINK}">📱 Все новости — в приложении</tg-button></tg-button-row>`;
  const f = new FormData();
  f.append("chat_id", CHAT);
  f.append("rich_message", JSON.stringify({ html, media: shots.map((_, i) => ({ id: `s${i}`, media: { type: "photo", media: `attach://f${i}` } })) }));
  shots.forEach((buf, i) => f.append(`f${i}`, new Blob([buf], { type: "image/jpeg" }), `f${i}.jpg`));
  const r = await tg("sendRichMessage", f);
  console.log("карусель (слайдшоу) отправлена:", CHAT, "#" + r.result.message_id);
}
const LINK = `📱 <a href="https://t.me/${BOT}?startapp=ch">Календарь и все новости — в приложении «Пульс рынка»</a>`;
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
  const label = new Date(day + "T12:00:00+03:00").toLocaleDateString("ru-RU", { day: "numeric", month: "long", weekday: "long", timeZone: "Europe/Moscow" });
  const img = await shot(p, `EVS = a.list; return \`${HEAD_HTML("📅 Календарь")}<div class="fw"><div class="big">Сегодня на рынке<br><small style="font-size:16px;opacity:.8;font-weight:700">\${a.label}</small></div>\${a.list.map(e => evMini(e, 0, "")).join("")}</div><div class="ft"><span>события дня</span><span>@ime_news_01_bot</span></div>\`;`, { list, label });
  const hi = list.filter(e => e.impact === "High").length;
  await sendPhotos([img], `📅 <b>Сегодня на рынке</b> — ${list.length} ${list.length === 1 ? "событие" : list.length < 5 ? "события" : "событий"}${hi ? `, сильно двигают рынок: ${hi}` : ""}\n\n${LINK}`);
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
  await sendPhotos(shots, `${cap}\n\n${LINK}`);
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

(async () => {
  if (THEME === "inspect") return inspect();
  const api = await fetch(`${APP}/api/freech${TARGET === "test" ? "?sample=1" : ""}`, { headers: { authorization: `Bearer ${process.env.INGEST_SECRET}` } });
  if (!api.ok) throw new Error("api/freech " + api.status);
  const data = await api.json(), { news, used, events = [], results = [] } = data;
  if (THEME === "pro" || THEME === "today" || THEME === "results") {
    const b = await chromium.launch();
    const p = await b.newPage({ viewport: { width: 400, height: 500 }, deviceScaleFactor: 2.7, locale: "ru-RU", timezoneId: "Europe/Moscow" });
    p.on("pageerror", e => console.log("page error:", e.message));
    await p.goto(APP + "/", { waitUntil: "networkidle" });
    await p.waitForTimeout(1500);
    if (THEME === "pro") await proPost(p, data); else if (THEME === "today") await todayPost(p, events); else await resultsPost(p, results);
    await b.close();
    return;
  }

  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 400, height: 500 }, deviceScaleFactor: 2.7, locale: "ru-RU", timezoneId: "Europe/Moscow" });
  p.on("pageerror", e => console.log("page error:", e.message));
  await p.goto(APP + "/", { waitUntil: "networkidle" });
  await p.waitForTimeout(1500);

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
  }, { news, used, theme: THEME });
  // карточки крипты до 25.10 ~04:00 МСК — с одинаковым рисованным фоном: показываем вместо них фото приложения (img/bg/crypto*)
  for (const n of pick) if (n.block === "crypto" && n.ts < "2026-10-25T01:00") delete n.card;
  console.log(`${THEME}: ${pick.length} новостей\n` + pick.map(n => "• " + n.title).join("\n"));
  if (pick.length < 3) { console.log("мало новостей — пост не отправлен"); await b.close(); return; }

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
    }, { n: pick[i], i, total: pick.length, title: TITLE[THEME], fit: FIT });
    await p.waitForTimeout(800); // картинка карточки
    shots.push(await p.locator("#fc").screenshot({ type: "jpeg", quality: 88 }));
  }
  const fs = require("fs"); fs.mkdirSync("out", { recursive: true }); shots.forEach((buf, i) => fs.writeFileSync(`out/${THEME}_${i + 1}.jpg`, buf)); // для проверки (артефакт запуска)

  // подпись короткая — только заголовок (владелец 25.10); ссылка — кнопкой
  const caption = HEAD[THEME];
  await richPost(shots, caption).catch(e => { console.log("слайдшоу не ушло:", e.message, "— запасной вариант: кнопки ◀ ▶"); return deckPost(shots, caption); });
  if (PRO) await proPost(p, data);
  await b.close();
  if (TARGET === "channel") await fetch(`${APP}/api/freech/done`, { method: "POST", headers: { authorization: `Bearer ${process.env.INGEST_SECRET}`, "content-type": "application/json" },
    body: JSON.stringify({ theme: THEME, titles: pick.map(n => n.title) }) });
})().catch(e => { console.error(e); process.exit(1); });
