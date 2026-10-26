# pro_tpl — шаблоны канала PRO v6 (утверждены владельцем 26.10.2026)

Карусели 1620×2025 (Telegram 4:5) из HTML через Playwright/Chromium. Стиль не менять без Арт-директора и «да» владельца.

| Файл | Что |
|---|---|
| `v6.html` + `pro.css` | Пн `monc`, «События дня» `evc`, Вт `tuec`, Чт `thuc` — с автоподгоном текста |
| `ad-v6.html` | Ср, Пт, Сб, Вс (Арт-директор) — пока образец недели 02–08.11 |
| `render.js` | единый рендер (см. шапку файла) |
| `schema.md` | поля каждой рубрики — формат данных для Редактора |
| `data/*.json` | образцы данных; `evc_long.json` — проверка длинного текста |
| `img/`, `credits.md` | только фото с лицензией PD / CC0 / CC BY (автор и ссылка в `credits.md`) |

## Запуск
```bash
npm i --no-save playwright@1.48.2 && npx playwright install --with-deps chromium   # один раз
node bot/pro_tpl/render.js evc --data свои.json --out out/evc      # → out/evc/evc1.jpg … evcN.jpg
```
Шрифты (Inter, Onest, Playfair Display, Prata, Cormorant Garamond, Unbounded, JetBrains Mono — все OFL) грузятся с Google Fonts — нужен интернет (в GitHub Actions есть). Эмодзи-флаги — пакет `fonts-noto-color-emoji`.
Проверка в Actions: `gh workflow run pro_tpl.yml -R KikiLove18/pulse-collector -f rubric=evc` — картинки в артефакте `pro-slides`, в каналы ничего не отправляется.

## Автоподгон
Блоки с текстом уменьшают кегль до 20 px; не влезло — сокращение по словам «…» (в выводе — предупреждение, код 0). Обложка «Событий»: сначала убираются подстроки, потом последние события → «+ ещё N — листайте». Если что-то вылезло за слайд — список в выводе и код выхода 2.
Нельзя: ключи и личные данные (репозиторий публичный), фото без лицензии.
