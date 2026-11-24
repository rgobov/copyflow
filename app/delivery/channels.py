from __future__ import annotations

import hmac  # noqa: F401  (kept for verify helpers if needed)
import json
import logging
import time

import httpx
from copyflow_core.webhooks import sign

from app.db.models import SignalDelivery, Subscription

log = logging.getLogger(__name__)

WEBHOOK_TIMEOUT = 10.0
RETRIES = 2


class DeliveryManager:
    """Pushes signals and risk events to client apps.

    webhook: signed POST (X-Copyflow-Signature: sha256=HMAC(secret, ts.body));
    the client verifies the signature, applies its own risk logic and executes
    through its own trading system. telegram: human-readable push.
    """

    def __init__(self, notifier=None, http: httpx.AsyncClient | None = None):
        self._notifier = notifier
        self._http = http or httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def send_signal(self, session, sub: Subscription, signal_id: str, payload: dict) -> None:
        await self._send(session, sub, kind="signal", signal_id=signal_id, payload=payload)

    async def send_risk(self, session, sub: Subscription, payload: dict) -> None:
        await self._send(session, sub, kind="risk", signal_id=None, payload=payload)

    async def _send(self, session, sub: Subscription, kind: str, signal_id: str | None, payload: dict) -> None:
        channel = sub.delivery
        if channel == "auto":
            return  # server executes itself; nothing to deliver
        if channel == "webhook":
            status, detail = await self._post_webhook(sub, payload)
        elif channel == "telegram":
            status, detail = await self._telegram(kind, payload)
        else:
            status, detail = "skipped", f"unknown delivery {channel}"

        session.add(
            SignalDelivery(
                subscription_id=sub.id,
                signal_id=signal_id,
                kind=kind,
                channel=channel,
                payload=payload,
                status=status,
                detail=detail,
            )
        )
        log.info("delivery %s sub=%s -> %s (%s)", kind, sub.id, channel, status)

    async def _post_webhook(self, sub: Subscription, payload: dict) -> tuple[str, str | None]:
        if not sub.webhook_url:
            return "skipped", "webhook_url not set"
        body = json.dumps(payload, default=str).encode()
        ts = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            "X-Copyflow-Timestamp": ts,
            "X-Copyflow-Signature": sign(sub.webhook_secret or "", body, ts),
        }
        last_err = None
        for _ in range(RETRIES + 1):
            try:
                r = await self._http.post(sub.webhook_url, content=body, headers=headers)
                if r.status_code < 300:
                    return "sent", None
                last_err = f"http {r.status_code}"
            except httpx.HTTPError as e:
                last_err = str(e)[:200]
        return "failed", last_err

    async def _telegram(self, kind: str, payload: dict) -> tuple[str, str | None]:
        if not self._notifier:
            return "skipped", "telegram not configured"
        try:
            if kind == "signal":
                text = self._format_signal(payload)
            else:
                text = "⚠️ RISK: " + payload.get("reason", "breach")
            await self._notifier.notify(text)
            return "sent", None
        except Exception as e:  # pragma: no cover
            return "failed", str(e)[:200]

    @staticmethod
    def _format_signal(p: dict) -> str:
        sig = p.get("signal", {})
        icon = "🟢" if sig.get("side") == "long" else "🔴"
        lines = [
            f"{icon} SIGNAL {sig.get('action', '').upper()} {sig.get('symbol')} ({sig.get('side')})",
            f"leader: {p.get('leader_name', sig.get('leader_id'))} | size: {sig.get('size_frac')}",
        ]
        if sig.get("ref_price"):
            lines.append(f"ref price: {sig['ref_price']}")
        if sig.get("take_profit"):
            lines.append(f"TP: {sig['take_profit']}")
        if sig.get("stop_loss"):
            lines.append(f"SL: {sig['stop_loss']}")
        return "\n".join(lines)
