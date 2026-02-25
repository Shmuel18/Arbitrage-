"""
Unit tests for src.execution.blacklist — BlacklistManager.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from src.execution.blacklist import BlacklistManager


class TestBlacklistManager:
    # ── add & is_blacklisted ────────────────────────────────────

    def test_not_blacklisted_initially(self):
        bl = BlacklistManager()
        assert not bl.is_blacklisted("BTC/USDT", "exchange_a", "exchange_b")

    def test_blacklisted_after_add(self):
        bl = BlacklistManager()
        bl.add("BTC/USDT", "exchange_a")
        assert bl.is_blacklisted("BTC/USDT", "exchange_a", "exchange_b")

    def test_blacklist_is_per_exchange(self):
        bl = BlacklistManager()
        bl.add("BTC/USDT", "exchange_a")
        # exchange_b alone should not trigger the blacklist
        assert not bl.is_blacklisted("BTC/USDT", "exchange_c", "exchange_d")

    def test_blacklist_triggers_on_short_exchange(self):
        bl = BlacklistManager()
        bl.add("BTC/USDT", "exchange_b")
        # exchange_b is the short leg here → still blocked
        assert bl.is_blacklisted("BTC/USDT", "exchange_a", "exchange_b")

    def test_blacklist_is_per_symbol(self):
        bl = BlacklistManager()
        bl.add("ETH/USDT", "exchange_a")
        # BTC/USDT on exchange_a should NOT be blocked
        assert not bl.is_blacklisted("BTC/USDT", "exchange_a", "exchange_b")

    def test_multiple_symbols_independent(self):
        bl = BlacklistManager()
        bl.add("ETH/USDT", "exchange_a")
        bl.add("BTC/USDT", "exchange_b")
        assert bl.is_blacklisted("ETH/USDT", "exchange_a", "exchange_b")
        assert bl.is_blacklisted("BTC/USDT", "exchange_a", "exchange_b")
        assert not bl.is_blacklisted("SOL/USDT", "exchange_a", "exchange_b")

    # ── TTL / expiry ────────────────────────────────────────────

    def test_entry_expires_after_ttl(self):
        bl = BlacklistManager(duration_sec=1)
        bl.add("BTC/USDT", "exchange_a")
        # Advance time past TTL via mock
        with patch("src.execution.blacklist.time") as mock_time:
            mock_time.time.return_value = time.time() + 2  # 2 seconds ahead
            assert not bl.is_blacklisted("BTC/USDT", "exchange_a", "exchange_b")

    def test_custom_duration_overrides_default(self):
        bl = BlacklistManager(duration_sec=3600)
        bl.add("BTC/USDT", "exchange_a", duration_sec=10)
        # At t+5 (within 10s) still blacklisted
        now = time.time()
        with patch("src.execution.blacklist.time") as mock_time:
            mock_time.time.return_value = now + 5
            assert bl.is_blacklisted("BTC/USDT", "exchange_a", "exchange_b")
        # At t+15 (past 10s) expired
        with patch("src.execution.blacklist.time") as mock_time:
            mock_time.time.return_value = now + 15
            assert not bl.is_blacklisted("BTC/USDT", "exchange_a", "exchange_b")

    def test_re_adding_resets_ttl(self):
        bl = BlacklistManager(duration_sec=1)
        now = time.time()
        bl.add("BTC/USDT", "exchange_a")
        # Re-add before expiry with a longer duration
        bl.add("BTC/USDT", "exchange_a", duration_sec=3600)
        with patch("src.execution.blacklist.time") as mock_time:
            mock_time.time.return_value = now + 2  # past original 1s TTL
            # Should still be blocked due to re-add with 1h TTL
            assert bl.is_blacklisted("BTC/USDT", "exchange_a", "exchange_b")

    # ── _evict_expired ──────────────────────────────────────────

    def test_evict_removes_expired_entries(self):
        bl = BlacklistManager(duration_sec=1)
        bl.add("BTC/USDT", "exchange_a")
        bl.add("ETH/USDT", "exchange_b")
        assert len(bl._entries) == 2
        with patch("src.execution.blacklist.time") as mock_time:
            mock_time.time.return_value = time.time() + 2
            bl._evict_expired()
        assert len(bl._entries) == 0

    def test_evict_keeps_non_expired_entries(self):
        bl = BlacklistManager(duration_sec=3600)
        bl.add("BTC/USDT", "exchange_a")         # long TTL
        bl.add("ETH/USDT", "exchange_b", duration_sec=1)
        with patch("src.execution.blacklist.time") as mock_time:
            mock_time.time.return_value = time.time() + 2
            bl._evict_expired()
        # Only BTC entry should remain
        assert len(bl._entries) == 1
        assert "BTC/USDT:exchange_a" in bl._entries
