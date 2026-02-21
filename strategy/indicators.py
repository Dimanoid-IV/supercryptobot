"""
Technical indicators module.
Calculates EMA, RSI, ATR, and other indicators using pure pandas.
"""

from typing import Optional, Tuple
from dataclasses import dataclass

import pandas as pd
import numpy as np

from config import config
from utils.helpers import logger, safe_divide


@dataclass
class IndicatorValues:
    """Container for calculated indicator values."""
    # EMA values
    ema_21: Optional[float] = None
    ema_50: Optional[float] = None
    ema_200: Optional[float] = None
    
    # RSI
    rsi: Optional[float] = None
    
    # ATR
    atr: Optional[float] = None
    atr_ratio: Optional[float] = None  # ATR / price
    
    # Volume
    volume_sma: Optional[float] = None
    volume_ratio: Optional[float] = None  # Current volume / SMA
    
    # Price data
    current_price: Optional[float] = None
    current_volume: Optional[float] = None


class Indicators:
    """Class for calculating technical indicators."""
    
    @staticmethod
    def calculate_ema(df: pd.DataFrame, period: int) -> pd.Series:
        """
        Calculate Exponential Moving Average using pandas ewm.
        
        Args:
            df: DataFrame with OHLCV data
            period: EMA period
            
        Returns:
            Series with EMA values
        """
        if len(df) < period:
            return pd.Series([np.nan] * len(df))
        return df["close"].ewm(span=period, adjust=False).mean()
    
    @staticmethod
    def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        Calculate Relative Strength Index using pandas.
        
        Args:
            df: DataFrame with OHLCV data
            period: RSI period
            
        Returns:
            Series with RSI values
        """
        if len(df) < period + 1:
            return pd.Series([np.nan] * len(df))
        
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        Calculate Average True Range using pandas.
        
        Args:
            df: DataFrame with OHLCV data
            period: ATR period
            
        Returns:
            Series with ATR values
        """
        if len(df) < period + 1:
            return pd.Series([np.nan] * len(df))
        
        high_low = df["high"] - df["low"]
        high_close = np.abs(df["high"] - df["close"].shift())
        low_close = np.abs(df["low"] - df["close"].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        atr = true_range.rolling(window=period).mean()
        return atr
    
    @staticmethod
    def calculate_volume_sma(df: pd.DataFrame, period: int = 20) -> pd.Series:
        """
        Calculate Volume Simple Moving Average.
        
        Args:
            df: DataFrame with OHLCV data
            period: SMA period
            
        Returns:
            Series with volume SMA values
        """
        if len(df) < period:
            return pd.Series([np.nan] * len(df))
        return df["volume"].rolling(window=period).mean()
    
    @classmethod
    def calculate_all(cls, df: pd.DataFrame) -> IndicatorValues:
        """
        Calculate all indicators for a DataFrame.
        
        Args:
            df: DataFrame with OHLCV data (must have open, high, low, close, volume columns)
            
        Returns:
            IndicatorValues object with latest values
        """
        if df.empty or len(df) < 50:
            logger.warning("Insufficient data for indicator calculation")
            return IndicatorValues()
        
        try:
            # Calculate EMAs
            df["ema_21"] = cls.calculate_ema(df, config.EMA_SHORT)
            df["ema_50"] = cls.calculate_ema(df, config.EMA_MEDIUM)
            df["ema_200"] = cls.calculate_ema(df, config.EMA_LONG)
            
            # Calculate RSI
            df["rsi"] = cls.calculate_rsi(df, config.RSI_PERIOD)
            
            # Calculate ATR
            df["atr"] = cls.calculate_atr(df, config.ATR_PERIOD)
            
            # Calculate Volume SMA
            df["volume_sma"] = cls.calculate_volume_sma(df, config.VOLUME_SMA_PERIOD)
            
            # Get latest values
            latest = df.iloc[-1]
            current_price = latest["close"]
            current_volume = latest["volume"]
            
            # Calculate ATR ratio (ATR as percentage of price)
            atr_value = latest["atr"] if pd.notna(latest["atr"]) else 0
            atr_ratio = safe_divide(atr_value, current_price, 0)
            
            # Calculate volume ratio
            volume_sma = latest["volume_sma"] if pd.notna(latest["volume_sma"]) else 0
            volume_ratio = safe_divide(current_volume, volume_sma, 1.0)
            
            return IndicatorValues(
                ema_21=latest["ema_21"] if pd.notna(latest["ema_21"]) else None,
                ema_50=latest["ema_50"] if pd.notna(latest["ema_50"]) else None,
                ema_200=latest["ema_200"] if pd.notna(latest["ema_200"]) else None,
                rsi=latest["rsi"] if pd.notna(latest["rsi"]) else None,
                atr=atr_value,
                atr_ratio=atr_ratio,
                volume_sma=volume_sma,
                volume_ratio=volume_ratio,
                current_price=current_price,
                current_volume=current_volume
            )
            
        except Exception as e:
            logger.error(f"Error calculating indicators: {e}")
            return IndicatorValues()
    
    @staticmethod
    def is_price_near_ema(
        price: float,
        ema_value: float,
        tolerance_percent: float = 0.5
    ) -> bool:
        """
        Check if price is near an EMA value within tolerance.
        
        Args:
            price: Current price
            ema_value: EMA value
            tolerance_percent: Tolerance percentage (default 0.5%)
            
        Returns:
            True if price is near EMA
        """
        if ema_value == 0 or ema_value is None:
            return False
        
        diff_percent = abs(price - ema_value) / ema_value * 100
        return diff_percent <= tolerance_percent
    
    @staticmethod
    def calculate_local_extremes(
        df: pd.DataFrame,
        lookback: int = 20
    ) -> Tuple[float, float]:
        """
        Calculate local minimum and maximum prices.
        
        Args:
            df: DataFrame with OHLCV data
            lookback: Number of periods to look back
            
        Returns:
            Tuple of (local_min, local_max)
        """
        if len(df) < lookback:
            lookback = len(df)
        
        recent_data = df.tail(lookback)
        local_min = recent_data["low"].min()
        local_max = recent_data["high"].max()
        
        return local_min, local_max
    
    @staticmethod
    def calculate_candle_size(df: pd.DataFrame) -> Optional[float]:
        """
        Calculate the size of the latest candle as ATR multiple.
        
        Args:
            df: DataFrame with OHLCV and ATR data
            
        Returns:
            Candle size as ATR multiple, or None if cannot calculate
        """
        if len(df) < 2:
            return None
        
        latest = df.iloc[-1]
        candle_size = abs(latest["close"] - latest["open"])
        atr = latest.get("atr", 0)
        
        if atr and atr > 0:
            return candle_size / atr
        return None
    
    @classmethod
    def get_trend_alignment(
        cls,
        price: float,
        ema_50: Optional[float],
        ema_200: Optional[float]
    ) -> str:
        """
        Determine trend alignment based on EMAs.
        
        Args:
            price: Current price
            ema_50: EMA 50 value
            ema_200: EMA 200 value
            
        Returns:
            "bullish", "bearish", or "neutral"
        """
        if ema_50 is None or ema_200 is None:
            return "neutral"
        
        if price > ema_200 and ema_50 > ema_200:
            return "bullish"
        elif price < ema_200 and ema_50 < ema_200:
            return "bearish"
        else:
            return "neutral"
