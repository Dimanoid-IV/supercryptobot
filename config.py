"""
Configuration module for Crypto Signal Bot.
Contains all environment variables, constants, and settings.
"""

import os
from dataclasses import dataclass
from typing import List


@dataclass
class Config:
    """Main configuration class."""
    
    # API Keys - loaded from environment variables
    BYBIT_API_KEY: str = os.getenv("BYBIT_API_KEY", "")
    BYBIT_SECRET: str = os.getenv("BYBIT_SECRET", "")
    TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
    
    # Bybit API settings
    BYBIT_BASE_URL: str = "https://api.bybit.com"  # Mainnet
    BYBIT_TESTNET: bool = False
    
    # Scanning settings
    SCAN_INTERVAL_SECONDS: int = 90  # Reduced from 120s to 90s for more frequent scans
    TOP_PAIRS_COUNT: int = 10  # Increased from 5 to 10 pairs to analyze
    
    # Timeframes
    TIMEFRAME_TREND: str = "15"  # 15 minutes for trend detection
    TIMEFRAME_ENTRY: str = "5"   # 5 minutes for entry detection
    TIMEFRAME_VOLATILITY: str = "60"  # 1 hour for volatility calc
    
    # Indicator settings
    EMA_SHORT: int = 21
    EMA_MEDIUM: int = 50
    EMA_LONG: int = 200
    RSI_PERIOD: int = 14
    ATR_PERIOD: int = 14
    VOLUME_SMA_PERIOD: int = 20
    
    # Scoring thresholds
    MIN_SIGNAL_SCORE: int = 75  # Minimum confidence 75% for signals (out of 100)
    MAX_SCORE: int = 100
    
    # Scoring weights
    SCORE_TREND_ALIGNMENT: int = 25
    SCORE_EMA_PULLBACK: int = 20
    SCORE_RSI_ZONE: int = 15
    SCORE_VOLUME_SPIKE: int = 15
    SCORE_ATR_LEVEL: int = 10
    SCORE_OI_CONFIRMATION: int = 15
    
    # Signal filters
    MAX_SIGNALS_PER_DAY: int = 1000  # Unlimited signals (high limit for continuous flow)
    MIN_HOURS_BETWEEN_SIGNALS_SAME_PAIR: int = 0  # No cooldown between signals on same pair
    MAX_CANDLE_ATR_MULTIPLIER: float = 4.0  # Increased from 3.0 to allow more signals
    
    # Funding rate limits (avoid extreme values)
    MAX_FUNDING_RATE: float = 0.001  # 0.1%
    MIN_FUNDING_RATE: float = -0.001  # -0.1%
    
    # Risk management
    MIN_RISK_REWARD_RATIO: float = 2.0  # Minimum 1:2 R:R
    STOP_LOSS_ATR_MULTIPLIER: float = 1.5
    TAKE_PROFIT_ATR_MULTIPLIER: float = 3.0
    
    # Volatility calculation weights
    VOLATILITY_WEIGHT_PRICE_CHANGE: float = 0.4
    VOLATILITY_WEIGHT_ATR: float = 0.3
    VOLATILITY_WEIGHT_VOLUME: float = 0.3
    
    # RSI zones for entry
    RSI_LONG_MIN: float = 40.0
    RSI_LONG_MAX: float = 55.0
    RSI_SHORT_MIN: float = 45.0
    RSI_SHORT_MAX: float = 60.0
    
    # Volume threshold (multiplier above average)
    VOLUME_THRESHOLD: float = 1.2  # 20% above average
    
    # API rate limiting (for Render free tier optimization)
    API_CALL_DELAY_MS: int = 100  # Delay between API calls
    MAX_RETRIES: int = 3
    RETRY_DELAY_SECONDS: int = 5
    
    # Payment settings
    STRIPE_PAYMENT_LINK: str = os.getenv("STRIPE_PAYMENT_LINK", "")
    CRYPTO_WALLET_USDT: str = os.getenv("CRYPTO_WALLET_USDT", "")
    CRYPTO_NETWORK: str = os.getenv("CRYPTO_NETWORK", "TRC20")  # TRC20, ERC20, etc.
    SUPPORT_USERNAME: str = os.getenv("SUPPORT_USERNAME", "")  # Without @, e.g. "username"
    
    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    # Pair filters
    MIN_24H_VOLUME_USDT: float = 10_000_000  # Minimum $10M volume
    EXCLUDED_PAIRS: List[str] = None
    
    def __post_init__(self):
        """Initialize derived values."""
        if self.EXCLUDED_PAIRS is None:
            self.EXCLUDED_PAIRS = [
                "USDCUSDT",  # Stable pair
            ]
    
    def validate(self) -> List[str]:
        """Validate configuration and return list of missing/invalid settings."""
        errors = []
        
        if not self.BYBIT_API_KEY:
            errors.append("BYBIT_API_KEY is not set")
        if not self.BYBIT_SECRET:
            errors.append("BYBIT_SECRET is not set")
        if not self.TELEGRAM_TOKEN:
            errors.append("TELEGRAM_TOKEN is not set")
        if not self.TELEGRAM_CHAT_ID:
            errors.append("TELEGRAM_CHAT_ID is not set")
        
        return errors
    
    def is_valid(self) -> bool:
        """Check if configuration is valid."""
        return len(self.validate()) == 0


# Global config instance
config = Config()
