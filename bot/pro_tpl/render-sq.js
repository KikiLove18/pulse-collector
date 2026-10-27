// Рендер квадратных 1:1 (1620×1620) каруселей АД: node render-sq.js <html> <префикс wed|sun|…> <папка> [суффикс]
// --data <json> — данные для шаблонов-с-полями (fric-sq.html): window.DATA_IN.
// Проверка: ничего не заходит в нижнюю зону (подпись источника/счётчик/копирайт) и за края.
const { chromium } = require('playwright'); const path = require('path'); const fs = require('fs');
(async () => {
  const av = process.argv.slice(2), di = av.indexOf('--data'), data = di >= 0 ? av.splice(di, 2)[1] : null;
  const [html, only, outd, suf = ''] = av; const out = path.resolve(outd); fs.mkdirSync(out, { recursive: true });
  const b = await chromium.launch(), p = await b.newPage({ viewport: { width: 1100, height: 1400 }, deviceScaleFactor: 1.5 });
  if (data) await p.addInitScript(`window.DATA_IN=${fs.readFileSync(data, 'utf8')};`);
  await p.goto('file://' + path.resolve(html)); await p.evaluate(() => document.fonts.ready); await p.waitForTimeout(800);
  const bad = await p.$$eval('.s', ss => ss.flatMap(s => { const r = s.getBoundingClientRect(), lim = r.bottom - 150;
    const zone = [...s.querySelectorAll('.src,.cnt,.ft')]; const top = Math.min(...zone.map(z => z.getBoundingClientRect().top));
    return [...s.querySelectorAll('*')].filter(e => !e.closest('.src,.cnt,.ft,.hd')).filter(e => { const q = e.getBoundingClientRect(); return q.width && q.height && (q.right > r.right + 1 || q.bottom > top - 8); })
      .map(e => s.id + ' .' + String(e.className || e.tagName).split(' ')[0] + ' ' + Math.round(e.getBoundingClientRect().bottom - r.top) + '/' + Math.round(top - r.top)); }));
  if (bad.length) console.error('НАЕЗД:', [...new Set(bad)].join(' | '));
  for (const x of await p.$$eval('.s', e => e.map(el => el.id))) if (x.startsWith(only)) {
    const f = path.join(out, x + suf + '.jpg'); await (await p.$('#' + x)).screenshot({ path: f, type: 'jpeg', quality: 88 }); console.log(f); }
  await b.close();
})();
