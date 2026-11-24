from __future__ import annotations

import enum
from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class ExecutionMode(str, enum.Enum):
    PAPER = "paper"
    LIVE = "live"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    api_key: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Leader(Base):
    __tablename__ = "leaders"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(32))  # strategy | hl_wallet | cex_trader
    config: Mapped[dict] = mapped_column(JSON, default=dict)  # e.g. wallet address
    equity_usd: Mapped[float] = mapped_column(Float, default=10_000.0)  # nominal leader equity for frac sizing
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    subscriptions: Mapped[list[Subscription]] = relationship(back_populates="leader")


class ExecutorKind(str, enum.Enum):
    PAPER = "paper"
    HYPERLIQUID = "hyperliquid"
    BINANCE = "binance"


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    leader_id: Mapped[int] = mapped_column(ForeignKey("leaders.id"))
    executor: Mapped[ExecutorKind] = mapped_column(Enum(ExecutorKind), default=ExecutorKind.PAPER)
    mode: Mapped[ExecutionMode] = mapped_column(Enum(ExecutionMode), default=ExecutionMode.PAPER)

    # sizing
    sizing: Mapped[str] = mapped_column(String(16), default="equity_frac")  # equity_frac | fixed_usd | proportional
    fixed_usd: Mapped[float] = mapped_column(Float, default=0.0)
    equity_usd: Mapped[float] = mapped_column(Float, default=1_000.0)  # paper / virtual equity
    leverage_cap: Mapped[float] = mapped_column(Float, default=3.0)

    # guards
    max_price_deviation_pct: Mapped[float] = mapped_column(Float, default=1.0)
    slippage_bps: Mapped[int] = mapped_column(Integer, default=10)
    min_notional_usd: Mapped[float] = mapped_column(Float, default=10.0)
    max_positions: Mapped[int] = mapped_column(Integer, default=10)
    blacklisted_symbols: Mapped[list] = mapped_column(JSON, default=list)

    # delivery: "auto" (server executes) | "webhook" (client app) | "telegram" (manual)
    delivery: Mapped[str] = mapped_column(String(16), default="auto")
    webhook_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    webhook_secret: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # risk thresholds: server only detects breaches and pushes alerts,
    # the client decides what to do (no server-side pause / flatten)
    daily_loss_limit_pct: Mapped[float] = mapped_column(Float, default=10.0)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=25.0)
    peak_equity: Mapped[float] = mapped_column(Float, default=0.0)

    encrypted_credentials: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    leader: Mapped[Leader] = relationship(back_populates="subscriptions")


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    leader_id: Mapped[int] = mapped_column(ForeignKey("leaders.id"))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OrderStatus(str, enum.Enum):
    NEW = "new"
    FILLED = "filled"
    PARTIAL = "partial"
    REJECTED = "rejected"
    SKIPPED = "skipped"
    FAILED = "failed"


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(96), unique=True)
    #: null for risk-monitor-initiated closes (no originating signal)
    signal_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("signals.id"), nullable=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"))
    venue: Mapped[str] = mapped_column(String(32))
    symbol: Mapped[str] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(8))  # buy | sell
    kind: Mapped[str] = mapped_column(String(16), default="market")  # open|increase|reduce|flip|close
    notional_usd: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.NEW)
    skip_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"))
    symbol: Mapped[str] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    fee_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


Index("ix_signals_leader_created", Signal.leader_id, Signal.created_at)
Index("ix_orders_subscription", Order.subscription_id, Order.created_at)


class DailyState(Base):
    """Per-day risk bookkeeping: equity at day start and intraday peak."""

    __tablename__ = "daily_states"
    __table_args__ = (UniqueConstraint("subscription_id", "day", name="uq_daily_sub_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"))
    day: Mapped[date] = mapped_column(Date)
    start_equity: Mapped[float] = mapped_column(Float, default=0.0)
    peak_equity: Mapped[float] = mapped_column(Float, default=0.0)
    daily_loss_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    drawdown_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AccountSnapshot(Base):
    """Equity snapshots written on every processed signal."""

    __tablename__ = "account_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"))
    equity_usd: Mapped[float] = mapped_column(Float)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


Index("ix_snapshots_sub_ts", AccountSnapshot.subscription_id, AccountSnapshot.ts)


class ReconEvent(Base):
    """Position mismatch found by the reconciler (exchange vs local ledger)."""

    __tablename__ = "recon_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"))
    detail: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SignalDelivery(Base):
    """Audit of pushes sent to client apps / telegram (kind: signal | risk)."""

    __tablename__ = "signal_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"))
    signal_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(16))  # signal | risk
    channel: Mapped[str] = mapped_column(String(16))  # webhook | telegram
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16))  # sent | failed | skipped
    detail: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


Index("ix_deliveries_sub_created", SignalDelivery.subscription_id, SignalDelivery.created_at)
