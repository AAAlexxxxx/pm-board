# PM Board: облачный дэшборд Polymarket

Поиск рынков и две бумажные книги (ручная A и движок Claude) на GitHub Pages, обновляются GitHub Actions,
открываются на iPhone как приложение. Архитектура и решения: [DESIGN.md](DESIGN.md).

```
cloud/
  backend/pmcloud/   Python: снимок рынков, книги, движок, аномалии, публикация JSON
  state/             состояние в git: книги, калибровка, история NAV, журналы (коммитит бот)
  site/              статика PWA: index.html, app.js, style.css, sw.js, manifest, icons; data/ генерируется
.github/workflows/pmcloud.yml   cron (каждые 30 мин refresh, 10:05 UTC cycle) + команды с телефона
```

## Запуск в GitHub (один раз)

1. Создать репозиторий на GitHub (публичный: минуты Actions не ограничены; приватный: лимит 2000 мин/мес, тогда cron раз в час).
2. Запушить ветку с этой папкой как `main`:
   ```
   git checkout -b main
   git add cloud .github .gitignore
   git commit -m "cloud dashboard: backend, PWA, workflow"
   git remote add origin https://github.com/<owner>/<repo>.git
   git push -u origin main
   ```
3. Settings → Pages → Source: **GitHub Actions**.
4. Actions → workflow `pmcloud` → Run workflow (mode `refresh`). Через ~2 минуты сайт живёт на
   `https://<owner>.github.io/<repo>/`.
5. На iPhone открыть адрес в Safari → Поделиться → «На экран “Домой”».
6. Для команд с телефона (сделки книги A, «Обновить сейчас», скан, цикл Claude): Settings → Developer settings →
   Fine-grained tokens → токен на этот репозиторий с правом **Contents: Read and write**. Ввести его на вкладке «Ещё»
   уже внутри установленного приложения.

## Локально

```
cd cloud/backend
python -m pmcloud refresh              # снимок, отметки, погашения, fair value, аномалии -> site/data
python -m pmcloud scan                 # + движок без сделок
python -m pmcloud cycle [--dry]        # + дневной цикл движка (сделки книги Claude)
python -m pmcloud trade open "diesel export ban" sell 100 --dry
python -m pmcloud trade close <pid>
python -m pmcloud publish              # пересобрать site/data из сохранённого снимка, без сети
cd ../site && python -m http.server 8123   # http://127.0.0.1:8123/
```

## Важно

- После пуша облако становится единственным источником истины по книгам: локальные `pmtrader app` и
  `engine/claude_cycle.cmd` нужно остановить, иначе книги разойдутся. Книги скопированы в `state/` 2026-09-27.
- Калибровка (`state/calibration.json`) строится локально из `pm_data/night_eval/cases.parquet`
  (`engine/policy.calibrate`), обновлять раз в месяц.
- Сайт Pages публичный (кроме GitHub Enterprise), секретов в нём нет; токен хранится только в телефоне.
- Бумажные деньги: на Polymarket ничего не отправляется, только публичные чтения.
