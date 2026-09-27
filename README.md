# PM Board

Дэшборд Polymarket для iPhone: поиск рынков, сигналы движка и две бумажные книги (ручная A и движок Claude).
Бэкенд живёт в GitHub Actions, страница на GitHub Pages: https://aaalexxxxx.github.io/pm-board/

- Как устроено и почему: [cloud/DESIGN.md](cloud/DESIGN.md)
- Запуск, настройка токена, локальные команды: [cloud/README.md](cloud/README.md)
- Код: [cloud/backend/pmcloud](cloud/backend/pmcloud) (Python), [cloud/site](cloud/site) (PWA без сборки),
  [.github/workflows/pmcloud.yml](.github/workflows/pmcloud.yml) (cron и команды с телефона)

Деньги бумажные: на Polymarket отправляются только публичные запросы на чтение.
