from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import settings
from app.core.bus import EventBus
from app.db.database import engine
from app.db.models import Base
from app.engine.copier import default_executor_factory
from app.sources.hl_wallet_poller import HlWalletPoller

log = logging.getLogger("copyflow")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

bus = EventBus()
poller: HlWalletPoller | None = None
reconciler = None
notifier = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global poller, reconciler, notifier
    from app.bot.telegram import TelegramNotifier
    from app.db.database import SessionFactory
    from app.delivery.channels import DeliveryManager
    from app.engine.copier import CopyEngine
    from app.engine.monitor import RiskMonitor
    from app.engine.reconciler import Reconciler

    notifier = TelegramNotifier()
    delivery = DeliveryManager(notifier=notifier)
    engine_srv = CopyEngine(bus, SessionFactory, default_executor_factory, delivery)
    poller = HlWalletPoller(bus, SessionFactory, poll_interval=settings.hl_poll_interval_sec)
    reconciler = Reconciler(
        SessionFactory, default_executor_factory, delivery,
        interval=settings.reconcile_interval_sec,
    )
    monitor = RiskMonitor(
        SessionFactory, default_executor_factory, delivery,
        interval=settings.risk_check_interval_sec,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    engine_srv.start()
    poller.start()
    reconciler.start()
    monitor.start()
    app.state.copy_engine = engine_srv
    log.info("copyflow started")
    yield
    await poller.stop()
    await reconciler.stop()
    await monitor.stop()
    await engine_srv.stop()
    await bus.close_all()
    await delivery.aclose()
    log.info("copyflow stopped")


app = FastAPI(title="copyflow", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
