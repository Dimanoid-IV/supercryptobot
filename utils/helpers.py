"""
Utility functions and helpers for the Crypto Signal Bot.
Includes logging setup, common functions, and data processing utilities.
"""

import logging
import sys
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from dataclasses import dataclass

from config import config


# Setup logging
def setup_logging() -> logging.Logger:
    """Configure and return the main logger."""
    logger = logging.getLogger("crypto_bot")
    logger.setLevel(getattr(logging, config.LOG_LEVEL.upper()))
    
    # Clear existing handlers
    logger.handlers.clear()
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, config.LOG_LEVEL.upper()))
    
    # Formatter
    formatter = logging.Formatter(config.LOG_FORMAT)
    console_handler.setFormatter(formatter)
    
    logger.addHandler(console_handler)
    
    return logger


# Global logger instance
logger = setup_logging()


@dataclass
class SignalCooldown:
    """Track signal cooldowns per pair."""
    pair: str
    last_signal_time: Optional[datetime] = None
    
    def can_signal(self) -> bool:
        """Check if a new signal can be sent for this pair."""
        now = datetime.now()
        
        # Check pair-specific cooldown only (daily limit checked globally)
        if self.last_signal_time is not None:
            hours_since_last = (now - self.last_signal_time).total_seconds() / 3600
            if hours_since_last < config.MIN_HOURS_BETWEEN_SIGNALS_SAME_PAIR:
                logger.debug(f"[COOLDOWN] {self.pair}: pair cooldown active ({hours_since_last:.1f}h < {config.MIN_HOURS_BETWEEN_SIGNALS_SAME_PAIR}h)")
                return False
        
        return True
    
    def record_signal(self):
        """Record that a signal was sent."""
        self.last_signal_time = datetime.now()
        logger.info(f"[COOLDOWN] {self.pair}: pair cooldown started for {config.MIN_HOURS_BETWEEN_SIGNALS_SAME_PAIR}h")


class CooldownManager:
    """Manages cooldowns for all trading pairs."""
    
    def __init__(self):
        self._cooldowns: Dict[str, SignalCooldown] = {}
        self._total_signals_today = 0
        self._last_reset_date = datetime.now().date()
        self._last_reset_datetime = datetime.now()
    
    def _reset_daily_if_needed(self):
        """Reset daily counters if it's a new day."""
        today = datetime.now().date()
        if today != self._last_reset_date:
            logger.info(f"[COOLDOWN] New day detected! Resetting daily counter from {self._total_signals_today} to 0")
            self._total_signals_today = 0
            self._last_reset_date = today
            self._last_reset_datetime = datetime.now()
    
    def can_signal(self, pair: str) -> bool:
        """Check if a signal can be sent for the given pair."""
        self._reset_daily_if_needed()
        
        # Check global daily limit
        if self._total_signals_today >= config.MAX_SIGNALS_PER_DAY:
            logger.info(f"[COOLDOWN] BLOCKED {pair}: daily limit reached ({self._total_signals_today}/{config.MAX_SIGNALS_PER_DAY})")
            return False
        
        # Check pair-specific cooldown
        if pair not in self._cooldowns:
            self._cooldowns[pair] = SignalCooldown(pair=pair)
            logger.debug(f"[COOLDOWN] New pair tracking: {pair}")
        
        pair_allowed = self._cooldowns[pair].can_signal()
        if not pair_allowed:
            logger.info(f"[COOLDOWN] BLOCKED {pair}: pair-specific cooldown active")
            return False
        
        logger.debug(f"[COOLDOWN] ALLOWED {pair}: daily {self._total_signals_today}/{config.MAX_SIGNALS_PER_DAY}, pair cooldown OK")
        return True
    
    def record_signal(self, pair: str):
        """Record that a signal was sent for the given pair."""
        self._reset_daily_if_needed()
        
        if pair not in self._cooldowns:
            self._cooldowns[pair] = SignalCooldown(pair=pair)
        
        self._cooldowns[pair].record_signal()
        self._total_signals_today += 1
        
        logger.info(f"[COOLDOWN] RECORDED {pair}: total today {self._total_signals_today}/{config.MAX_SIGNALS_PER_DAY}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cooldown statistics."""
        self._reset_daily_if_needed()
        return {
            "total_signals_today": self._total_signals_today,
            "max_signals_per_day": config.MAX_SIGNALS_PER_DAY,
            "pairs_tracked": len(self._cooldowns),
            "last_reset": self._last_reset_datetime.isoformat(),
        }


def format_price(price: float, precision: int = 4) -> str:
    """Format price with appropriate precision."""
    if price >= 1000:
        return f"{price:,.2f}"
    elif price >= 1:
        return f"{price:.{precision}f}"
    else:
        return f"{price:.6f}"


def format_percentage(value: float) -> str:
    """Format percentage value."""
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def calculate_volatility_score(
    price_change_1h: float,
    atr_ratio: float,
    volume_spike: float
) -> float:
    """
    Calculate volatility score based on multiple factors.
    
    Formula: 0.4 * |1h % change| + 0.3 * (ATR / price) + 0.3 * Volume spike
    """
    score = (
        config.VOLATILITY_WEIGHT_PRICE_CHANGE * abs(price_change_1h) +
        config.VOLATILITY_WEIGHT_ATR * atr_ratio * 100 +  # Convert to percentage-like scale
        config.VOLATILITY_WEIGHT_VOLUME * volume_spike * 100
    )
    return score


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safely divide two numbers, returning default if denominator is zero."""
    if denominator == 0 or denominator is None:
        return default
    return numerator / denominator


def timestamp_to_datetime(timestamp_ms: int) -> datetime:
    """Convert milliseconds timestamp to datetime."""
    return datetime.fromtimestamp(timestamp_ms / 1000)


def get_timeframe_minutes(timeframe: str) -> int:
    """Convert timeframe string to minutes."""
    return int(timeframe)


def is_valid_trading_pair(symbol: str) -> bool:
    """Check if symbol is a valid USDT perpetual trading pair."""
    if not symbol.endswith("USDT"):
        return False
    if symbol in config.EXCLUDED_PAIRS:
        return False
    # Filter out non-perpetual pairs (spot, etc.)
    # Bybit perpetual symbols typically end with USDT
    return True


def log_signal_details(pair: str, score: int, details: Dict[str, Any]):
    """Log detailed signal information."""
    logger.info(f"=== Signal Analysis for {pair} ===")
    logger.info(f"Total Score: {score}/{config.MAX_SCORE}")
    for key, value in details.items():
        logger.info(f"  {key}: {value}")
    logger.info("=" * 40)
