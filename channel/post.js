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
// текст поста PRO (без обещаний дохода; цены — как в боте, PRICES в worker/index.js)
const PRO_TEXT = `👑 <b>Пульс рынка PRO</b>

Здесь — новости. В PRO — что они значат для твоих активов:

⚡ <b>Влияние</b> — какие новости давят на твои активы, а какие помогают
🔗 <b>Схема «как это работает»</b> — от решения ЦБ до цены акции, по шагам
📅 <b>События на 2 недели</b> — что может сдвинуть рынок, с силой влияния
🔎 <b>Разбор любого актива</b> — 42 актива, 69 человек, поиск по Ленте
🔒 <b>Закрытый канал</b> — разбор недели, PDF-обучение, анонсы первыми

PRO — от 240 ₽ в месяц. Базовый — бесплатно.`;

const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

async function tg(method, form) {
  const r = await fetch(`https://api.telegram.org/bot${process.env.TELEGRAM_BOT_TOKEN}/${method}`, { method: "POST", body: form });
  const j = await r.json();
  if (!j.ok) throw new Error(`${method}: ${j.description}`);
  return j;
}

async function proPost() {
  const f2 = new FormData();
  f2.append("chat_id", CHAT);
  f2.append("photo", `${APP}/img/promo/ch_pro.jpg?v=1`);
  f2.append("parse_mode", "HTML");
  f2.append("caption", PRO_TEXT);
  f2.append("reply_markup", JSON.stringify({ inline_keyboard: [[{ text: "👑 Оформить PRO", url: `https://t.me/${BOT}?startapp=pro` }]] }));
  await tg("sendPhoto", f2);
  console.log("пост PRO отправлен");
}

// рамка слайда (как у подборок) вокруг готового блока приложения; html строится в странице приложения
const FRAME_CSS = `#fc{position:fixed;inset:0;z-index:2147483647;background:linear-gradient(160deg,#1b3360,#3a6ea8);display:flex;flex-direction:column;padding:22px 18px 18px;gap:14px}
  #fc .hd{display:flex;align-items:center;gap:10px;font-weight:800;font-size:17px;color:#fff}#fc .hd img{width:34px;height:34px;border-radius:9px}
  #fc .hd .th{margin-left:auto;font-size:14px;font-weight:700;opacity:.8}#fc .card,#fc .past-hero{margin:0!important;box-shadow:0 10px 40px rgba(0,0,0,.35)}#fc .acts,#fc .newpill,#fc .ch,#fc .cd2{display:none}
  #fc .fw{flex:1;display:flex;flex-direction:column;justify-content:center;gap:8px}#fc .ft{display:flex;justify-content:space-between;font-size:13px;opacity:.75;color:#fff}
  #fc .big{color:#fff;font-size:26px;font-weight:900;line-height:1.15;margin:4px 2px 6px}#fc .evmini{width:100%;pointer-events:none}`;
async function shot(p, build, arg) {
  await p.evaluate(({ css, build, arg }) => {
    document.getElementById("fc")?.remove();
    const o = document.createElement("div");
    o.id = "fc";
    o.innerHTML = `<style>${css}</style>` + new Function("a", build)(arg);
    document.body.appendChild(o);
  }, { css: FRAME_CSS, build, arg });
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
  await tg("sendRichMessage", f);
  console.log("карусель (слайдшоу) отправлена:", CHAT);
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

// inspect — разбор поста тестового канала (ARG: «582» или «580-586»): пересылаем копию туда же, печатаем устройство, копию удаляем
async function inspect() {
  const [a, b2] = String(process.env.ARG || "").split("-").map(Number);
  for (let id = a; id <= (b2 || a); id++) {
    const f = new FormData();
    f.append("chat_id", process.env.TELEGRAM_CHAT_ID); f.append("from_chat_id", process.env.TELEGRAM_CHAT_ID); f.append("message_id", String(id)); f.append("disable_notification", "true");
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
  if (THEME === "pro") return proPost();
  const api = await fetch(`${APP}/api/freech${TARGET === "test" ? "?sample=1" : ""}`, { headers: { authorization: `Bearer ${process.env.INGEST_SECRET}` } });
  if (!api.ok) throw new Error("api/freech " + api.status);
  const { news, used, events = [], results = [] } = await api.json();
  if (THEME === "today" || THEME === "results") {
    const b = await chromium.launch();
    const p = await b.newPage({ viewport: { width: 540, height: 675 }, deviceScaleFactor: 2, locale: "ru-RU", timezoneId: "Europe/Moscow" });
    p.on("pageerror", e => console.log("page error:", e.message));
    await p.goto(APP + "/", { waitUntil: "networkidle" });
    await p.waitForTimeout(1500);
    if (THEME === "today") await todayPost(p, events); else await resultsPost(p, results);
    await b.close();
    return;
  }

  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 540, height: 675 }, deviceScaleFactor: 2, locale: "ru-RU", timezoneId: "Europe/Moscow" });
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
    await p.evaluate(({ n, i, total, title }) => {
      NEWS = [n];
      document.getElementById("fc")?.remove();
      const o = document.createElement("div");
      o.id = "fc";
      o.innerHTML = `<style>#fc{position:fixed;inset:0;z-index:2147483647;background:linear-gradient(160deg,#1b3360,#3a6ea8);display:flex;flex-direction:column;padding:22px 18px 18px;gap:14px}
        #fc .hd{display:flex;align-items:center;gap:10px;font-weight:800;font-size:17px;color:#fff}#fc .hd img{width:34px;height:34px;border-radius:9px}
        #fc .hd .th{margin-left:auto;font-size:14px;font-weight:700;opacity:.8}#fc .card{margin:0!important;box-shadow:0 10px 40px rgba(0,0,0,.35)}#fc .acts,#fc .newpill{display:none}
        #fc .fw{flex:1;display:flex;flex-direction:column;justify-content:center}#fc .ft{display:flex;justify-content:space-between;font-size:13px;opacity:.75;color:#fff}</style>
        <div class="hd"><img src="img/logo192.png" alt="">Пульс рынка<span class="th">${title}</span></div>
        <div class="fw">${newsCard(n, 0)}</div>
        <div class="ft"><span>${i + 1} / ${total}</span><span>@ime_news_01_bot</span></div>`;
      document.body.appendChild(o);
    }, { n: pick[i], i, total: pick.length, title: TITLE[THEME] });
    await p.waitForTimeout(800); // картинка карточки
    shots.push(await p.locator("#fc").screenshot({ type: "jpeg", quality: 88 }));
  }
  await b.close();
  const fs = require("fs"); fs.mkdirSync("out", { recursive: true }); shots.forEach((buf, i) => fs.writeFileSync(`out/${THEME}_${i + 1}.jpg`, buf)); // для проверки (артефакт запуска)

  // подпись короткая — только заголовок (владелец 25.10); ссылка — кнопкой
  const caption = HEAD[THEME];
  await richPost(shots, caption).catch(e => { console.log("слайдшоу не ушло:", e.message, "— запасной вариант: кнопки ◀ ▶"); return deckPost(shots, caption); });
  if (PRO) await proPost();
  if (TARGET === "channel") await fetch(`${APP}/api/freech/done`, { method: "POST", headers: { authorization: `Bearer ${process.env.INGEST_SECRET}`, "content-type": "application/json" },
    body: JSON.stringify({ theme: THEME, titles: pick.map(n => n.title) }) });
})().catch(e => { console.error(e); process.exit(1); });
