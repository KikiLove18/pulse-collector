// Рендер каруселей PRO v6 (пакет для автомата). Нужны: Node 18+, playwright (Chromium).
//   node render.js evc --data data/evc.json --out out/evc   → out/evc/evc1.jpg … evcN.jpg (1620×2025)
//   node render.js monc|tuec|thuc --data … --out …            (Пн, Вт, Чт — v6.html, поля в schema.md)
//   node render.js ad [sat|wed|fri|sun] --out …               (Ср/Пт/Сб/Вс Арт-директора — ad-v6.html, пока образец недели 02–08.11 без --data)
// --full — для автомата: поля только из --data, образец не подмешивается (null / '' / [] = блок не показывать). Без --full пропущенные поля берутся из образца.
// Без --data — образец из шаблона. Текст любой длины подгоняется (кегль ↓ до 20 px, дальше «…»).
// Текст, не влезший и на 20 px, сокращается «…» — это предупреждение (код 0). Код выхода 2 — что-то вылезло за слайд (список в выводе), картинки всё равно сохраняются; --strict не сохраняет.
const { chromium } = require('playwright'); const path = require('path'); const fs = require('fs');
(async () => {
  const a = process.argv.slice(2), opt = k => { const i = a.indexOf(k); return i >= 0 ? a.splice(i, 2)[1] : null; };
  const strict = a.includes('--strict') && a.splice(a.indexOf('--strict'), 1), full = a.includes('--full') && a.splice(a.indexOf('--full'), 1);
  const data = opt('--data'), out = path.resolve(opt('--out') || path.join(__dirname, 'out')), id = a[0] || 'evc', ad = id == 'ad', only = ad ? a[1] : id;
  fs.mkdirSync(out, { recursive: true });
  const b = await chromium.launch(), p = await b.newPage({ viewport: { width: 1100, height: 1400 }, deviceScaleFactor: 1.5 });
  let bad = [];
  if (ad) {
    await p.goto('file://' + path.join(__dirname, 'ad-v6.html')); await p.evaluate(() => document.fonts.ready); await p.waitForTimeout(800);
    bad = await p.$$eval('.s', ss => ss.flatMap(s => { const r = s.getBoundingClientRect(); return [...s.querySelectorAll('*')].filter(e => { const q = e.getBoundingClientRect(); return q.width && (q.right > r.right + 1 || q.bottom > r.bottom + 1); }).map(e => s.id + ' .' + String(e.className).split(' ')[0]) }));
  } else {
    const d = data ? JSON.parse(fs.readFileSync(data, 'utf8')) : null;
    await p.addInitScript(`window.ONLY=${JSON.stringify(only)};window.DATA_FULL=${full ? 1 : 0};window.DATA_IN=${d ? JSON.stringify(d[only] ? d : { [only]: d }) : 'null'};`);
    await p.goto('file://' + path.join(__dirname, 'v6-2710.html'));
    await p.waitForFunction(() => window.FIT_DONE, null, { timeout: 60000 }); await p.waitForTimeout(400);
    bad = await p.evaluate(() => window.OVERFLOW || []);
    const cut = await p.evaluate(() => window.CUT || []); if (cut.length) console.error('Текст сокращён «…» (не влез даже на 20 px):', cut.join(' | '));
  }
  bad = [...new Set(bad)];
  if (bad.length) console.error('ВЫЛЕЗАЕТ за слайд:', bad.join(' | '));
  if (!(strict && bad.length)) {
    const sel = ad ? '.s' : '.cd';
    for (const x of await p.$$eval(sel, e => e.map(el => el.id))) if (!ad || !only || x.startsWith(only)) {
      const f = path.join(out, x + '.jpg'); await (await p.$('#' + x)).screenshot({ path: f, type: 'jpeg', quality: 88 }); console.log(f);
    }
  }
  await b.close(); process.exit(bad.length ? 2 : 0);
})();
