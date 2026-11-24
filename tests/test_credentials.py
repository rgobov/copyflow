from __future__ import annotations

import json

import pytest
from copyflow_core.executors.base import ExecutionError
from copyflow_core.executors.hyperliquid import HyperliquidExecutor
from cryptography.fernet import Fernet

from app.config import settings
from app.db.models import ExecutorKind, Subscription
from app.engine.copier import ExecutorFactory


async def test_hl_credentials_from_subscription():
    key = Fernet.generate_key().decode()
    old = settings.fernet_key
    settings.fernet_key = key
    try:
        from app.security import encrypt

        sub = Subscription(
            executor=ExecutorKind.HYPERLIQUID,
            encrypted_credentials=encrypt(
                json.dumps({"private_key": "0xabc", "address": "0xdef"})
            ),
        )
        adapter = await ExecutorFactory().get(sub)
        assert isinstance(adapter, HyperliquidExecutor)
        assert adapter._pk == "0xabc"
        assert adapter._address == "0xdef"
    finally:
        settings.fernet_key = old


async def test_bad_fernet_key_raises():
    key = Fernet.generate_key().decode()
    old = settings.fernet_key
    settings.fernet_key = key
    try:
        sub = Subscription(executor="hyperliquid", encrypted_credentials="not-encrypted")
        with pytest.raises(ExecutionError, match="decrypt"):
            await ExecutorFactory().get(sub)
    finally:
        settings.fernet_key = old
