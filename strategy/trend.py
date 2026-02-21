"""
Trend detection module.
Analyzes market trend using 15m timeframe and EMA alignment.
"""

from typing import Optional
from dataclasses import dataclass
from enum import Enum

import pandas as pd

from config import config
from strategy.indicators import Indicators, IndicatorValues
from utils.helpers import logger


class TrendDirection(Enum):
    """Enum for trend directions."""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass
class TrendAnalysis:
    """Container for trend analysis results."""
    direction: TrendDirection
    strength: str  # "strong", "moderate", "weak"
    price_above_ema200: bool
    ema50_above_ema200: bool
    indicators: IndicatorValues
    
    @property
    def is_bullish(self) -> bool:
        """Check if trend is bullish."""
        return self.direction == TrendDirection.BULLISH
    
    @property
    def is_bearish(self) -> bool:
        """Check if trend is bearish."""
        return self.direction == TrendDirection.BEARISH
    
    @property
    def is_aligned_for_long(self) -> bool:
        """Check if trend is aligned for LONG entry."""
        return self.is_bullish and self.price_above_ema200 and self.ema50_above_ema200
    
    @property
    def is_aligned_for_short(self) -> bool:
        """Check if trend is aligned for SHORT entry."""
        return self.is_bearish and not self.price_above_ema200 and not self.ema50_above_ema200


class TrendDetector:
    """Class for detecting market trends."""
    
    def __init__(self):
        """Initialize trend detector."""
        self.indicators = Indicators()
    
    def analyze(self, df: pd.DataFrame) -> TrendAnalysis:
        """
        Analyze trend from price data.
        
        Uses 15m timeframe with EMA200 and EMA50 alignment:
        - LONG: price > EMA200 AND EMA50 > EMA200
        - SHORT: price < EMA200 AND EMA50 < EMA200
        
        Args:
            df: DataFrame with OHLCV data (15m timeframe)
            
        Returns:
            TrendAnalysis object
        """
        if df.empty or len(df) < 200:
            logger.warning("Insufficient data for trend analysis")
            return TrendAnalysis(
                direction=TrendDirection.NEUTRAL,
                strength="weak",
                price_above_ema200=False,
                ema50_above_ema200=False,
                indicators=IndicatorValues()
            )
        
        # Calculate all indicators
        indicator_values = self.indicators.calculate_all(df)
        
        # Check if we have valid EMA values
        if (indicator_values.ema_50 is None or 
            indicator_values.ema_200 is None or 
            indicator_values.current_price is None):
            logger.warning("Missing EMA values for trend analysis")
            return TrendAnalysis(
                direction=TrendDirection.NEUTRAL,
                strength="weak",
                price_above_ema200=False,
                ema50_above_ema200=False,
                indicators=indicator_values
            )
        
        price = indicator_values.current_price
        ema_50 = indicator_values.ema_50
        ema_200 = indicator_values.ema_200
        
        # Determine trend direction
        price_above_ema200 = price > ema_200
        ema50_above_ema200 = ema_50 > ema_200
        
        if price_above_ema200 and ema50_above_ema200:
            direction = TrendDirection.BULLISH
        elif not price_above_ema200 and not ema50_above_ema200:
            direction = TrendDirection.BEARISH
        else:
            direction = TrendDirection.NEUTRAL
        
        # Determine trend strength
        strength = self._calculate_trend_strength(
            price, ema_50, ema_200, indicator_values
        )
        
        return TrendAnalysis(
            direction=direction,
            strength=strength,
            price_above_ema200=price_above_ema200,
            ema50_above_ema200=ema50_above_ema200,
            indicators=indicator_values
        )
    
    def _calculate_trend_strength(
        self,
        price: float,
        ema_50: float,
        ema_200: float,
        indicators: IndicatorValues
    ) -> str:
        """
        Calculate trend strength based on EMA separation and price position.
        
        Args:
            price: Current price
            ema_50: EMA 50 value
            ema_200: EMA 200 value
            indicators: Indicator values
            
        Returns:
            Strength string: "strong", "moderate", or "weak"
        """
        # Calculate EMA separation percentage
        ema_separation = abs(ema_50 - ema_200) / ema_200 * 100
        
        # Calculate price distance from EMA50
        price_distance = abs(price - ema_50) / ema_50 * 100
        
        # Strong trend: EMAs well separated and price following trend
        if ema_separation > 2.0 and price_distance < 3.0:
            return "strong"
        # Moderate trend: Some separation
        elif ema_separation > 0.5:
            return "moderate"
        # Weak trend: EMAs close together
        else:
            return "weak"
    
    def get_trend_description(self, analysis: TrendAnalysis) -> str:
        """
        Get human-readable trend description.
        
        Args:
            analysis: TrendAnalysis object
            
        Returns:
            Description string
        """
        direction_str = analysis.direction.value.capitalize()
        return f"{direction_str} ({analysis.strength})"
    
    @staticmethod
    def is_valid_trend_for_entry(
        analysis: TrendAnalysis,
        direction: str
    ) -> bool:
        """
        Check if trend is valid for entry in specified direction.
        
        Args:
            analysis: TrendAnalysis object
            direction: "LONG" or "SHORT"
            
        Returns:
            True if trend is aligned for entry
        """
        if direction == "LONG":
            return analysis.is_aligned_for_long
        elif direction == "SHORT":
            return analysis.is_aligned_for_short
        return False
