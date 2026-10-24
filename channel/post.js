// Пост в бесплатный канал (@pulse_rynka_news): карусель «5 главных новостей по теме» + (pro=1) пост про PRO с кнопкой.
// Запускает воркер (worker/freech.js) через workflow channel.yml в pulse-collector. ИИ не вызывается: новости уже готовы (/api/freech).
// Слайды — снимки настоящих карточек новостей из приложения (функция newsCard), без меню, в рамке 1080×1350.
// Переменные: THEME ru|us|crypto|commodities|x, TARGET test|channel, PRO 0|1, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (тест), INGEST_SECRET.
const { chromium } = require("playwright");

const APP = process.env.APP_URL || "https://app.pulse-rynka.ru";
const BOT = "ime_news_01_bot";
const THEME = process.env.THEME || "ru", TARGET = process.env.TARGET || "test", PRO = process.env.PRO === "1";
const CHAT = TARGET === "channel" ? process.env.FREECH_CHAT || "@pulse_rynka_news" : process.env.TELEGRAM_CHAT_ID;
const TITLE = { ru: "🇷🇺 Россия", us: "🇺🇸 США", crypto: "₿ Крипта", commodities: "🛢 Нефть и золото", x: "🗣 Что говорят люди" };
const HEAD = { ru: "🇷🇺 <b>Россия: главное за день</b>", us: "🇺🇸 <b>США: главное за день</b>", crypto: "₿ <b>Крипта: главное за день</b>",
  commodities: "🛢 <b>Нефть и золото: главное за день</b>", x: "🗣 <b>Что говорят люди, которые двигают рынки</b>" };
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

(async () => {
  const api = await fetch(`${APP}/api/freech`, { headers: { authorization: `Bearer ${process.env.INGEST_SECRET}` } });
  if (!api.ok) throw new Error("api/freech " + api.status);
  const { news, used } = await api.json();

  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 540, height: 675 }, deviceScaleFactor: 2, locale: "ru-RU", timezoneId: "Europe/Moscow" });
  p.on("pageerror", e => console.log("page error:", e.message));
  await p.goto(APP + "/", { waitUntil: "networkidle" });
  await p.waitForTimeout(1500);

  // подбор в странице — там же списки людей приложения (PERSON_RE)
  const pick = await p.evaluate(({ news, used, theme }) => {
    // без войны и происшествий (канал — про рынки), без повторов, сильные первыми; «люди» — новости с нашими персонами, по одной на человека
    const BAD = /беспилот|БПЛА|ПВО|дрон|теракт|жертв|погиб|ранен|землетряс|протест|митинг|ракет|обстрел|удар(ы|ов|ам)?\b|атак|военн|войн|армия|ВСУ|фронт|взрыв|эвакуир|хуситы|заложник|убит|арест|суд\b|приговор|покушени|подрыв|на минах|эскалац|КСИР/i;
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

  const day = new Date().toLocaleDateString("ru-RU", { day: "numeric", month: "long", timeZone: "Europe/Moscow" });
  const caption = `${HEAD[THEME]}\n${day} · листай карточки →\n\n` + pick.map((n, i) => `${i + 1}. ${esc(n.title)}`).join("\n") +
    `\n\nК каждой новости — вывод, как она влияет на рынок.\n📱 Лента под твои активы — <a href="https://t.me/${BOT}?startapp=ch">в приложении «Пульс рынка»</a>`;
  const fd = new FormData();
  fd.append("chat_id", CHAT);
  fd.append("media", JSON.stringify(shots.map((_, i) => ({ type: "photo", media: `attach://s${i}`, ...(i ? {} : { caption, parse_mode: "HTML" }) }))));
  shots.forEach((buf, i) => fd.append(`s${i}`, new Blob([buf], { type: "image/jpeg" }), `s${i}.jpg`));
  await tg("sendMediaGroup", fd);
  console.log("карусель отправлена:", CHAT);

  if (PRO) {
    const f2 = new FormData();
    f2.append("chat_id", CHAT);
    f2.append("photo", `${APP}/img/promo/ch_pro.jpg?v=1`);
    f2.append("parse_mode", "HTML");
    f2.append("caption", PRO_TEXT);
    f2.append("reply_markup", JSON.stringify({ inline_keyboard: [[{ text: "👑 Оформить PRO", url: `https://t.me/${BOT}?startapp=pro` }]] }));
    await tg("sendPhoto", f2);
    console.log("пост PRO отправлен");
  }
  if (TARGET === "channel") await fetch(`${APP}/api/freech/done`, { method: "POST", headers: { authorization: `Bearer ${process.env.INGEST_SECRET}`, "content-type": "application/json" },
    body: JSON.stringify({ theme: THEME, titles: pick.map(n => n.title) }) });
})().catch(e => { console.error(e); process.exit(1); });
