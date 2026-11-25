[![CI](https://github.com/rgobov/copyflow/actions/workflows/ci.yml/badge.svg)](https://github.com/rgobov/copyflow/actions/workflows/ci.yml)
# copyflow

Copy-trading / автоследование (аналог Tinkoff Pulse и 4collective) для крипторынка:
лидеры — ваши стратегии, on-chain кошельки Hyperliquid и трейдеры CEX;
исполнение — paper или live (Hyperliquid, Binance).

## Архитектура

- `app/core` — канонический `SignalEvent` (open/increase/reduce/flip/close) + шина
- `app/sources` — источники сигналов: webhook для стратегий, HL wallet poller (дифф позиций)
- `app/engine` — копи-движок: сайзинг (equity_frac / fixed_usd / proportional), лимиты
  (плечо, макс. позиций, blacklist, уход цены, мин. нотионал), kill-switch по дневному убытку
- `app/executors` — `ExecutionAdapter`: `PaperExecutor` (виртуальный счёт), `HyperliquidExecutor` (agent wallet), `BinanceExecutor` (ccxt, USD-M futures)
- `app/api` — REST (FastAPI): лидеры, подписки, webhook сигналов, позиции, филлы
- `app/bot` — Telegram-уведомления

## Запуск

```bash
# локально
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/uvicorn app.main:app --reload

# docker (postgres + redis)
cp .env.example .env
docker compose up --build
```

## Пример сценария

```bash
# 1. создать лидера (стратегия)
curl -X POST :8000/api/v1/leaders -H 'X-Api-Key: <key>' \
  -d '{"name": "VBO ORB", "source": "strategy", "equity_usd": 10000}'

# 2. подписка: paper-счёт $1000, frac-сайзинг
curl -X POST :8000/api/v1/subscriptions -H 'X-Api-Key: <key>' \
  -d '{"leader_id": 1, "executor": "paper", "mode": "paper", "equity_usd": 1000}'

# 3. стратегия шлёт сигнал
curl -X POST :8000/api/v1/signals -H 'X-Api-Key: <key>' \
  -d '{"leader_id": 1, "symbol": "BTC", "action": "open", "side": "long", "size_frac": 0.3, "ref_price": 95000}'
```

## Риск-контроль открытых позиций: детект + пуш, без серверных действий

`RiskMonitor` каждые `RISK_CHECK_INTERVAL_SEC` секунд (по умолчанию 10) проверяет equity
каждой подписки — даже когда новых сигналов нет и позиции открыты. Пробой дневного лимита
или просадки от пика **не** ставит подписку на паузу и **не** закрывает позиции: сервер
только фиксирует нарушение (edge-детект: один пуш на эпизод пробоя) и доставляет
риск-событие клиенту — решение всегда за клиентом.

## Доставка сигналов клиенту

`subscriptions.delivery`:
- `auto` — сервер сам исполняет сигналы через исполнителей (paper/HL/Binance)
- `webhook` — сервер POST-ит подписанный payload на URL клиентского приложения:
  `X-Copyflow-Signature: sha256=HMAC_SHA256(secret, "{ts}.{body}")`, `X-Copyflow-Timestamp`.
  Клиент проверяет подпись, применяет свои риск-лимиты и исполняет через свою торговую
  систему (модель Пульса: подписки следят за сделками, исполнение руками/клиентом)
- `telegram` — человекочитаемый пуш

Payload сигнала содержит всё необходимое: сигнал (instrument/action/side/size_frac/tp/sl/ref_price),
контекст риска (лимиты подписки, peak equity). Все доставки пишутся в `signal_deliveries`.

## Клиентское приложение (copyflow-client)

Сервер — только сигналы и факты; **исполнение и риск-лимиты — у клиента**.
`client/` — отдельное приложение, запускается на машине пользователя, держит
свои биржевые ключи и никогда не отдаёт их серверу.

```
copyflow server ──(подписанный webhook)──> client app ──> своя торговая система
        ▲   сигналы + риск-события          HMAC-проверка, анти-replay
        └────────── leader_equity ──────────┘
```

Запуск:
```bash
# .env клиента (префикс CF_): секрет из подписки, ключи бирж, Telegram
CF_WEBHOOK_SECRET=<из подписки на сервере>

# client.json — риск-профиль подписок (клиентские лимиты!)
{
  "subscriptions": {
    "1": {"executor": "paper", "confirm_mode": "auto", "equity_usd": 1000,
          "leverage_cap": 2.0, "daily_loss_limit_pct": 5.0,
          "max_drawdown_pct": 20.0, "on_breach": "pause_and_close"}
  }
}

.venv/bin/uvicorn client.main:app --port 9000
.venv/bin/python scripts/demo_client.py   # e2e: сервер -> клиент -> исполнение
```

Режимы `confirm_mode`: `auto` — исполнять сразу; `confirm` — Telegram-кнопки
Execute/Skip (TTL 10 мин); `manual` — только уведомление (клиент торгует руками).
Риск-движок клиента: дневной лимит, drawdown от пика, `on_breach`:
`pause` / `pause_and_close` / `notify_only` — здесь пауза легитимна, это
собственные деньги пользователя. `POST /resume` снимает паузу, `GET /status` —
equity, позиции, решения.

API клиента: `POST /hook` (сигналы), `GET /status`, `POST /resume`.

## Проверка работоспособности

```bash
# 1. юнит/интеграционные тесты (движок, сайзинг, риск, реконсиляция, доставка)
.venv/bin/python -m pytest tests -v

# 2. авто-режим: сервер -> paper-исполнение (localhost:8000 должен работать)
(.venv/bin/uvicorn app.main:app --port 8000 &)
.venv/bin/python scripts/smoke.py
#   -> открытие/закрытие BTC, equity 1000 -> 999.49 (комиссии+slippage), fills

# 3. клиентский режим: сервер -> подписанный webhook -> клиентское приложение
.venv/bin/python scripts/demo_webhook.py
#   -> поднимает фейкового клиента на :9001, присылает сигнал,
#      клиент проверяет HMAC-подпись (signature_ok=True), сервер не торгует

# 4. наблюдение
tail -f /tmp/opencode/uvicorn.log   # filled / RISK BREACH / RECON MISMATCH
sqlite3 copyflow.db 'select * from signal_deliveries;'
```

Live-проверка (Hyperliquid/Binance): заполнить `.env` ключами (trade-only),
создать подписку `mode=live` с `equity_usd` малым, проверить `/positions` и филлы
на бирже. Реконсиляция сверяет позиции с леджером каждые 30 сек.

## Лидеры: on-chain и CEX с read-only ключами

Три типа лидеров (`source`):
- `strategy` — ваши системы шлют сигналы через `POST /signals` webhook
- `hl_wallet` — любой кошелёк Hyperliquid, публичные позиции (поллер диффит
  снапшот каждые 7 сек, не нужен даже ключ)
- `cex_trader` — трейдер CEX (Binance USD-M) с **read-only** API-ключами:
  ключи шифруются Fernet'ом при создании (`POST /leaders` с `api_key/api_secret`,
  обязательны trade/withdraw выключены на бирже), поллер читает позиции каждые 15 сек

Дифф позиций → канонические сигналы (open/increase/reduce/flip/close) с
notional-based `size_frac` (изменение позиции × mark price / equity лидера).
Подписка на CEX-лидера — кнопка **Follow** на `/leaders` (1 клик, paper-режим).

## Веб-UI (`web/`)

Next.js (App Router, TS): лента лидеров (статистика: сигналы, подписчики, последняя
активность), дашборд подписчика (подписки, позиции, fills, tape сигналов),
auth по API-ключу (хранится в localStorage, передаётся `X-Api-Key`).

```bash
cd web && npm install && npm run build && npm run start   # :3000
# или npm run dev
```

Сервер разрешает CORS для localhost:3000. Эндпоинты фронта:
`GET /me`, `GET /leaders` (+signal_count/followers/last_signal_at),
`GET /signals` (tape), `GET /positions`, `GET /fills`, `GET /subscriptions`.

## Стадии

1. [x] Сквозной автокопи: signal core + engine + paper/HL/Binance исполнители + Telegram
2. [x] Реконсиляция позиций (сверка биржи с локальным леджером филлов + алерты),
       риск-мониторинг открытых позиций (детект + пуш клиенту, без серверных пауз),
       режим доставки клиентскому приложению (подписанный webhook / telegram),
       per-subscription зашифрованные ключи исполнителей
3. [x] Веб-UI (Next.js): лента лидеров, дашборд подписчика, auth по API-ключу
4. [x] On-chain кошельки + CEX-лидеры с read-only ключами + режим «1 клик»
       (кнопка Follow в вебе; клиентский `confirm_mode` по умолчанию = confirm)
5. [ ] Монетизация (builder fee / profit share) -> SaaS
