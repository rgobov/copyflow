from __future__ import annotations

from copyflow_core.executors.paper import PaperConfig, PaperExecutor
from copyflow_core.models import Action, Instrument, Side, SignalEvent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.bus import EventBus
from app.db.database import make_engine
from app.db.models import Base, Leader, SignalDelivery, Subscription, User
from app.engine.copier import CopyEngine, ExecutorFactory
from app.engine.monitor import RiskMonitor


class FluxPaper(PaperExecutor):
    """Paper executor with a movable price."""

    def __init__(self):
        self.price = 100.0

        async def provider(symbol):
            return self.price

        super().__init__(PaperConfig(starting_equity_usd=1000.0), price_provider=provider)


class SeqFactory(ExecutorFactory):
    def __init__(self, executor: PaperExecutor):
        super().__init__()
        self._executor = executor

    async def get(self, sub):
        return self._executor


class StubDelivery:
    def __init__(self):
        self.signals: list[dict] = []
        self.risks: list[dict] = []

    async def send_signal(self, session, sub, signal_id, payload):
        self.signals.append(payload)

    async def send_risk(self, session, sub, payload):
        self.risks.append(payload)


async def setup(tmp_path, delivery="auto"):
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path}/m.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SF = async_sessionmaker(engine, expire_on_commit=False)
    executor = FluxPaper()
    stub = StubDelivery()
    bus = EventBus()
    eng = CopyEngine(bus, SF, SeqFactory(executor), delivery=stub)

    async with SF() as s:
        u = User(api_key="k")
        s.add(u)
        ld = Leader(name="l", source="strategy", equity_usd=10_000)
        s.add(ld)
        await s.flush()
        s.add(Subscription(
            user_id=u.id, leader_id=ld.id, daily_loss_limit_pct=5.0,
            delivery=delivery,
        ))
        await s.commit()

    return engine, SF, executor, eng, bus, stub


async def open_position(eng):
    await eng.handle_signal(SignalEvent(
        leader_id=1, instrument=Instrument(venue="paper", symbol="BTC"),
        action=Action.OPEN, side=Side.LONG, size_frac=0.5, ref_price=100.0,
    ))


async def test_monitor_pushes_risk_once_per_episode(tmp_path):
    engine, SF, executor, eng, bus, stub = await setup(tmp_path)
    await open_position(eng)
    assert len(await executor.positions()) == 1

    monitor = RiskMonitor(SF, SeqFactory(executor), delivery=stub, interval=9999)

    # price unchanged -> nothing
    assert await monitor.check_once() == []
    assert not stub.risks

    # crash: -10% day pnl, breach -> one risk push
    executor.price = 80.0
    breaches = await monitor.check_once()
    assert len(breaches) == 1
    assert len(stub.risks) == 1
    assert stub.risks[0]["risk"]["daily_breach"] is True
    assert stub.risks[0]["reason"].startswith("daily loss")

    # no pause: position stays open, subscription active
    assert len(await executor.positions()) == 1
    async with SF() as s:
        sub = (await s.execute(select(Subscription))).scalar_one()
        assert not hasattr(sub, "paused")

    # second pass while still below limit: no duplicate push (edge)
    assert await monitor.check_once() == []
    assert len(stub.risks) == 1

    # recovery resets the edge
    executor.price = 99.0
    assert await monitor.check_once() == []

    # breach again -> new push
    executor.price = 80.0
    await monitor.check_once()
    assert len(stub.risks) == 2

    await eng.stop()
    await bus.close_all()
    await engine.dispose()


async def test_delivery_mode_sends_signal_instead_of_executing(tmp_path):
    engine, SF, executor, eng, bus, stub = await setup(tmp_path, delivery="webhook")
    await open_position(eng)

    # client app model: server did NOT trade
    assert await executor.positions() == []
    assert len(stub.signals) == 1
    payload = stub.signals[0]
    assert payload["signal"]["action"] == "open"
    assert payload["signal"]["side"] == "long"
    assert payload["risk"]["daily_loss_limit_pct"] == 5.0

    async with SF() as s:
        # audit rows are written by the real DeliveryManager (see test_webhook_channel)
        from app.db.models import Order

        assert (await s.execute(select(Order))).scalars().all() == []

    await eng.stop()
    await bus.close_all()
    await engine.dispose()


async def test_webhook_channel_signs_and_audits(tmp_path):
    """Real DeliveryManager: HMAC signature + SignalDelivery audit row."""
    import json as json_mod

    import httpx as httpx_mod

    from app.db.database import make_engine as mk
    from app.db.models import Base as B
    from app.delivery.channels import DeliveryManager

    engine = mk(f"sqlite+aiosqlite:///{tmp_path}/d.db")
    async with engine.begin() as conn:
        await conn.run_sync(B.metadata.create_all)
    SF = async_sessionmaker(engine, expire_on_commit=False)

    seen: dict = {}

    def handler(request: httpx_mod.Request) -> httpx_mod.Response:
        seen["body"] = request.content
        seen["sig"] = request.headers.get("X-Copyflow-Signature")
        seen["ts"] = request.headers.get("X-Copyflow-Timestamp")
        return httpx_mod.Response(200)

    dm = DeliveryManager(http=httpx_mod.AsyncClient(transport=httpx_mod.MockTransport(handler)))

    async with SF() as s:
        u = User(api_key="k")
        s.add(u)
        ld = Leader(name="l", source="strategy")
        s.add(ld)
        await s.flush()
        sub = Subscription(
            user_id=u.id, leader_id=ld.id, delivery="webhook",
            webhook_url="https://client.example/hook", webhook_secret="topsecret",
        )
        s.add(sub)
        await s.commit()
        await dm.send_signal(s, sub, "sig-1", {"signal": {"action": "open"}})

        rows = (await s.execute(select(SignalDelivery))).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "sent"
        assert rows[0].channel == "webhook"

    # signature: sha256=HMAC(secret, ts.body)
    from app.delivery.channels import sign

    assert seen["sig"] == sign("topsecret", seen["body"], seen["ts"])
    assert json_mod.loads(seen["body"])["signal"]["action"] == "open"

    await dm.aclose()
    await engine.dispose()
