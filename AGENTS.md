# AGENTS.md — copyflow (сервер)

Инструкции для ИИ-агентов и разработчиков, работающих с этим репозиторием.

## Что это

Сервер копитрейдинга: сигналы лидеров (стратегии через webhook, on-chain
кошельки Hyperliquid, CEX-трейдеры с read-only ключами) доставляются
подписчикам (подписанный webhook / Telegram) или исполняются автоматически
(paper / Hyperliquid / Binance). Сервер не принимает торговых решений за
клиента в delivery-режиме — исполнение через клиентское приложение
(репозиторий `rgobov/copyflow-client`, публикует пакет `copyflow-core`).

## Структура

```
app/
├── core/        # EventBus (канонический SignalEvent живёт в copyflow_core)
├── sources/     # источники сигналов:
│   │            #   strategy webhook (API), hl_wallet_poller (дифф позиций),
│   │            #   cex_leader_poller (read-only ключи Binance)
├── engine/      # копи-движок и сервисы:
│   ├── copier.py            # исполнение/доставка сигналов по подпискам
│   ├── risk.py              # оценка дневного PnL / drawdown (только детект)
│   ├── monitor.py           # фоновый риск-мониторинг открытых позиций
│   ├── reconciler.py        # сверка биржи с локальным леджером филлов
│   ├── billing.py           # profit share (HWM), леджер комиссий
│   └── leader_portfolio.py  # виртуальный мультирыночный портфель лидера
├── delivery/    # DeliveryManager: подписанные webhook + telegram пуш
├── api/         # FastAPI REST (leaders, subscriptions, signals, billing, portfolio)
├── bot/         # Telegram-уведомления
├── db/          # SQLAlchemy-модели (SQLite dev / Postgres prod)
└── main.py      # сборка: lifespan стартует engine, pollers, monitor, reconciler
web/             # Next.js дашборд (см. README)
```

Исполнители бирж живут в **публичном пакете** `copyflow-core`
(репо `rgobov/copyflow-client`): `copyflow_core/executors/{paper,hyperliquid,binance}.py`,
котировки — `copyflow_core/marketdata.py`, метаданные инструментов —
`copyflow_core/instruments.py`. Сервер подключает пакет зависимостью
`copyflow-client @ git+https://github.com/rgobov/copyflow-client.git`.

## Команды

```bash
# окружение
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'

# тесты (24) и линт — обязательны перед коммитом
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check app tests scripts

# запуск API
.venv/bin/uvicorn app.main:app --port 8000

# веб-дашборд
cd web && npm install && npm run dev   # :3000

# живые проверки
.venv/bin/python scripts/smoke.py          # сигнал -> paper исполнение
.venv/bin/python scripts/demo_webhook.py   # подписанный webhook клиенту
.venv/bin/python scripts/demo_client.py    # e2e: сервер -> клиент
```

## Критичные инварианты (НЕ ЛОМАТЬ)

1. **Идемпотентность**: `client_order_id = f"cf{sub_id}{signal_id[:16]}{hash}"`.
   Повторная обработка сигнала не создаёт второго ордера.
2. **Сервер не торгует за клиента**: в `delivery != "auto"` сигнал только
   доставляется (`SignalDelivery`), исполнения нет. Риск-нарушения — тоже
   только уведомления: никакой серверной паузы и закрытия позиций.
3. **Биллинг**: начисление только в live-режиме и только на новом all-time
   high equity (`accrue_fees` вызывать ДО `evaluate_risk` — он владеет
   обновлением `peak_equity`). Paper не тарифицируется.
4. **Новое биржа/рынок** = адаптер в `copyflow_core/executors/` (общий
   интерфейс `ExecutionAdapter`) + регистрация в `ExecutorFactory`; ядро
   движка venue-агностично — не добавляйте ветвления по биржам в copier.
5. **Ключи бирж** лидеров/подписчиков — только через `app/security.py`
   (Fernet, `FERNET_KEY`), никогда в открытом виде в БД или логах.
6. **Реконсиляция**: источник истины — леджер филлов; расхождение с биржей =
   `ReconEvent` + алерт, автоматических «исправлений» нет.

## Конвенции

- Python 3.12+, ruff (line-length 110, ignore E501/B008; UP042 игнор для моделей)
- pytest-asyncio `asyncio_mode = auto`
- Комментарии — только там, где без них неочевидно; docstring на публичных классах
- Секреты: `.env` (FERNET_KEY, ключи бирж, токены) — gitignored;
  `.env.example` обновлять при появлении новых настроек

## Грабли

- `setuptools` packages.find pinned на `app*` — появление python-пакета
  верхнего уровня в `web/` ломает `pip install -e .`
- SQLite dev: смены моделей БД = `rm copyflow.db` (create_all не мигрирует)
- CI матрица 3.12/3.13 — локально 3.14; не использовать фичи только 3.13+
- `pkill -f uvicorn` в составной bash-команде убивает собственную обёртку —
  рестарт сервера делать через отдельный скрипт-файл
- `MARKET_TLS_VERIFY=false` — только для сетей с MITM публичных эндпоинтов
  (самоподписанные сертификаты Минцифры на MOEX)

## Быстрая проверка

```bash
curl http://127.0.0.1:8000/api/v1/health
curl -H "X-Api-Key: <key>" http://127.0.0.1:8000/api/v1/leaders
sqlite3 copyflow.db 'select status, count(*) from orders group by 1;'
```
