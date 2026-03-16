"""
Signal logic module.
Generates entry and exit signals based on technical analysis.
"""

from typing import Optional, Tuple, List
from dataclasses import dataclass
from enum import Enum

import pandas as pd

from config import config
from strategy.indicators import Indicators, IndicatorValues
from strategy.trend import TrendDetector, TrendAnalysis
from strategy.scoring import SignalScorer, ScoreBreakdown
from services.bybit_service import OpenInterest, FundingRate
from utils.helpers import logger


class SignalDirection(Enum):
    """Enum for signal directions."""
    LONG = "LONG"
    SHORT = "SHORT"
    NONE = "NONE"


@dataclass
class TradingSignal:
    """Container for trading signal."""
    pair: str
    direction: SignalDirection
    entry_price: float
    stop_loss: float
    take_profit: float
    score: int
    score_breakdown: ScoreBreakdown
    trend_analysis: TrendAnalysis
    indicators: IndicatorValues
    
    @property
    def risk_reward_ratio(self) -> float:
        """Calculate risk/reward ratio."""
        risk = abs(self.entry_price - self.stop_loss)
        reward = abs(self.take_profit - self.entry_price)
        return reward / risk if risk > 0 else 0
    
    @property
    def is_valid(self) -> bool:
        """Check if signal meets minimum requirements."""
        return (
            self.direction != SignalDirection.NONE and
            self.score >= config.MIN_SIGNAL_SCORE and
            self.risk_reward_ratio >= config.MIN_RISK_REWARD_RATIO
        )


class SignalGenerator:
    """Class for generating trading signals."""
    
    def __init__(self):
        """Initialize signal generator."""
        self.indicators = Indicators()
        self.trend_detector = TrendDetector()
        self.scorer = SignalScorer()
    
    async def generate_signal(
        self,
        pair: str,
        df_trend: pd.DataFrame,  # 15m timeframe
        df_entry: pd.DataFrame,   # 5m timeframe
        oi_data: List[OpenInterest],
        funding_rate: Optional[FundingRate],
        df_htf: Optional[pd.DataFrame] = None  # 4H timeframe for HTF filter (optional)
    ) -> Optional[TradingSignal]:
        """
        Generate trading signal for a pair.
        
        Args:
            pair: Trading pair symbol
            df_trend: 15m timeframe DataFrame for trend analysis
            df_entry: 5m timeframe DataFrame for entry analysis
            oi_data: Open interest data
            funding_rate: Current funding rate
            df_htf: 4H timeframe DataFrame for HTF trend filter (optional)
            
        Returns:
            TradingSignal if valid signal found, None otherwise
        """
        try:
            # Step 1: Analyze trend (15m)
            trend_analysis = self.trend_detector.analyze(df_trend)
            
            # Step 2: Calculate indicators for entry (5m)
            entry_indicators = self.indicators.calculate_all(df_entry)
            
            if entry_indicators.current_price is None:
                logger.warning(f"Cannot generate signal for {pair}: no price data")
                return None
            
            # Step 3: Check funding rate filter
            if funding_rate and not self._is_funding_rate_valid(funding_rate):
                logger.info(f"Skipping {pair}: extreme funding rate {funding_rate.funding_rate}")
                return None
            
            # Step 4: Check candle size filter
            candle_size = self.indicators.calculate_candle_size(df_entry)
            if candle_size and candle_size > config.MAX_CANDLE_ATR_MULTIPLIER:
                logger.info(f"Skipping {pair}: candle size {candle_size:.2f} ATR exceeds limit")
                return None
            
            # Step 5: Determine signal direction
            direction = self._determine_direction(trend_analysis, entry_indicators)
            
            if direction == SignalDirection.NONE:
                logger.debug(f"No valid direction for {pair}")
                return None
            
            # Step 5.5: Check HTF trend alignment (optional, with safe fallback)
            htf_aligned = True
            if config.ENABLE_HTF_FILTER and df_htf is not None and not df_htf.empty:
                try:
                    htf_indicators = self.indicators.calculate_all(df_htf)
                    htf_aligned = self._check_htf_trend_alignment(htf_indicators, direction)
                    if not htf_aligned:
                        logger.info(f"Skipping {pair}: HTF trend does not align with {direction.value}")
                        return None
                    logger.debug(f"HTF trend aligned for {pair} {direction.value}")
                except Exception as e:
                    logger.error(f"Error checking HTF trend for {pair}: {e}")
                    # Continue with signal generation on HTF error
            
            # Step 6: Check entry conditions
            entry_valid, entry_details = self._check_entry_conditions(
                direction, trend_analysis, entry_indicators
            )
            
            if not entry_valid:
                logger.debug(f"Entry conditions not met for {pair}")
                return None
            
            # Step 7: Calculate stop loss and take profit
            stop_loss, take_profit = self._calculate_stop_take(
                direction, entry_indicators, df_entry
            )
            
            # Step 8: Check risk/reward ratio
            risk = abs(entry_indicators.current_price - stop_loss)
            reward = abs(take_profit - entry_indicators.current_price)
            risk_reward = reward / risk if risk > 0 else 0
            
            if risk_reward < config.MIN_RISK_REWARD_RATIO:
                logger.info(f"Skipping {pair}: R:R {risk_reward:.2f} below minimum")
                return None
            
            # Step 9: Calculate base score
            oi_confirms = self._check_oi_confirmation(oi_data, direction)
            
            score_breakdown = self.scorer.calculate_partial_score(
                trend_aligned=entry_details["trend_aligned"],
                ema_pullback=entry_details["ema_pullback"],
                rsi_value=entry_indicators.rsi,
                rsi_zone_type="long" if direction == SignalDirection.LONG else "short",
                volume_ratio=entry_indicators.volume_ratio or 1.0,
                atr_ratio=entry_indicators.atr_ratio or 0,
                oi_change_percent=self._calculate_oi_change(oi_data)
            )
            
            # Step 9.5: Calculate enhanced scores (optional features)
            if config.ENABLE_WEIGHTED_SCORE:
                try:
                    # Calculate price change for OI confirmation
                    price_change = 0
                    if len(df_entry) >= 5:
                        price_change = ((df_entry["close"].iloc[-1] - df_entry["close"].iloc[-5]) 
                                       / df_entry["close"].iloc[-5] * 100)
                    
                    # Check OI trend confirmation
                    oi_trend_confirms = self._check_oi_trend_confirmation(
                        oi_data, direction, price_change
                    )
                    
                    # Apply enhanced scoring
                    score_breakdown = self.scorer.calculate_enhanced_scores(
                        breakdown=score_breakdown,
                        htf_trend_aligned=htf_aligned,
                        volume_anomaly=entry_indicators.volume_anomaly,
                        oi_confirms_trend=oi_trend_confirms,
                        is_trending_market=entry_indicators.is_trending,
                        signal_direction=direction.value
                    )
                except Exception as e:
                    logger.error(f"Error calculating enhanced scores for {pair}: {e}")
                    # Continue with base scores on error
            
            # Use weighted score if enabled, otherwise use base score
            total_score = (score_breakdown.weighted_total 
                          if config.ENABLE_WEIGHTED_SCORE 
                          else score_breakdown.total)
            
            # Step 10: Check minimum score
            if total_score < config.MIN_SIGNAL_SCORE:
                logger.info(f"Skipping {pair}: score {total_score} below threshold")
                return None
            
            # Log signal quality
            confidence_level = "HIGH" if total_score >= config.HIGH_CONFIDENCE_THRESHOLD else "NORMAL"
            
            # Create signal
            signal = TradingSignal(
                pair=pair,
                direction=direction,
                entry_price=entry_indicators.current_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                score=total_score,
                score_breakdown=score_breakdown,
                trend_analysis=trend_analysis,
                indicators=entry_indicators
            )
            
            logger.info(f"Signal generated for {pair}: {direction.value} (Score: {total_score}, Confidence: {confidence_level})")
            
            # Log enhanced analysis details
            if config.ENABLE_WEIGHTED_SCORE:
                self.scorer.log_score_breakdown(pair, score_breakdown)
            
            return signal
            
        except Exception as e:
            logger.error(f"Error generating signal for {pair}: {e}")
            return None
    
    def _determine_direction(
        self,
        trend_analysis: TrendAnalysis,
        indicators: IndicatorValues
    ) -> SignalDirection:
        """
        Determine signal direction based on trend and price action.
        
        Args:
            trend_analysis: Trend analysis result
            indicators: Current indicator values
            
        Returns:
            SignalDirection
        """
        price = indicators.current_price
        
        # LONG conditions
        if trend_analysis.is_aligned_for_long:
            return SignalDirection.LONG
        
        # SHORT conditions
        if trend_analysis.is_aligned_for_short:
            return SignalDirection.SHORT
        
        return SignalDirection.NONE
    
    def _check_entry_conditions(
        self,
        direction: SignalDirection,
        trend_analysis: TrendAnalysis,
        indicators: IndicatorValues
    ) -> Tuple[bool, dict]:
        """
        Check if entry conditions are met.
        
        Args:
            direction: Signal direction
            trend_analysis: Trend analysis
            indicators: Current indicators
            
        Returns:
            Tuple of (is_valid, details_dict)
        """
        details = {
            "trend_aligned": False,
            "ema_pullback": False,
            "rsi_valid": False,
            "volume_valid": False,
            "atr_valid": False
        }
        
        price = indicators.current_price
        
        # Check trend alignment
        if direction == SignalDirection.LONG:
            details["trend_aligned"] = trend_analysis.is_aligned_for_long
        else:
            details["trend_aligned"] = trend_analysis.is_aligned_for_short
        
        # Check EMA pullback
        if indicators.ema_21 and indicators.ema_50:
            near_ema21 = self.indicators.is_price_near_ema(price, indicators.ema_21, 1.0)
            near_ema50 = self.indicators.is_price_near_ema(price, indicators.ema_50, 1.0)
            details["ema_pullback"] = near_ema21 or near_ema50
        
        # Check RSI zone
        if indicators.rsi is not None:
            if direction == SignalDirection.LONG:
                details["rsi_valid"] = config.RSI_LONG_MIN <= indicators.rsi <= config.RSI_LONG_MAX
            else:
                details["rsi_valid"] = config.RSI_SHORT_MIN <= indicators.rsi <= config.RSI_SHORT_MAX
        
        # Check volume
        if indicators.volume_ratio is not None:
            details["volume_valid"] = indicators.volume_ratio >= config.VOLUME_THRESHOLD
        
        # Check ATR
        if indicators.atr_ratio is not None:
            details["atr_valid"] = indicators.atr_ratio >= 0.002  # Minimum 0.2%
        
        # Entry is valid if trend aligns and at least 3 other conditions are met
        conditions_met = sum([
            details["trend_aligned"],
            details["ema_pullback"],
            details["rsi_valid"],
            details["volume_valid"],
            details["atr_valid"]
        ])
        
        is_valid = details["trend_aligned"] and conditions_met >= 3
        
        return is_valid, details
    
    def _calculate_stop_take(
        self,
        direction: SignalDirection,
        indicators: IndicatorValues,
        df: pd.DataFrame
    ) -> Tuple[float, float]:
        """
        Calculate stop loss and take profit levels.
        
        Args:
            direction: Signal direction
            indicators: Current indicators
            df: Price DataFrame
            
        Returns:
            Tuple of (stop_loss, take_profit)
        """
        price = indicators.current_price
        atr = indicators.atr or (price * 0.01)  # Default to 1% if no ATR
        
        # Get local extremes for stop placement
        local_min, local_max = self.indicators.calculate_local_extremes(df, 20)
        
        if direction == SignalDirection.LONG:
            # Stop below local minimum or ATR-based
            atr_stop = price - (atr * config.STOP_LOSS_ATR_MULTIPLIER)
            stop_loss = min(atr_stop, local_min * 0.998)  # Slightly below local min
            
            # Take profit at 2x risk minimum
            risk = price - stop_loss
            take_profit = price + (risk * config.MIN_RISK_REWARD_RATIO)
            
        else:  # SHORT
            # Stop above local maximum or ATR-based
            atr_stop = price + (atr * config.STOP_LOSS_ATR_MULTIPLIER)
            stop_loss = max(atr_stop, local_max * 1.002)  # Slightly above local max
            
            # Take profit at 2x risk minimum
            risk = stop_loss - price
            take_profit = price - (risk * config.MIN_RISK_REWARD_RATIO)
        
        return stop_loss, take_profit
    
    def _is_funding_rate_valid(self, funding_rate: FundingRate) -> bool:
        """
        Check if funding rate is within acceptable range.
        
        Args:
            funding_rate: FundingRate object
            
        Returns:
            True if funding rate is acceptable
        """
        return (
            config.MIN_FUNDING_RATE <= funding_rate.funding_rate <= config.MAX_FUNDING_RATE
        )
    
    def _check_oi_confirmation(
        self,
        oi_data: List[OpenInterest],
        direction: SignalDirection
    ) -> bool:
        """
        Check if open interest confirms the signal direction.
        
        Args:
            oi_data: List of OI data points
            direction: Signal direction
            
        Returns:
            True if OI confirms the move
        """
        if len(oi_data) < 5:
            return False
        
        # Check if OI is rising (for both long and short entries in trending markets)
        recent_oi = [oi.open_interest for oi in oi_data[-5:]]
        return recent_oi[-1] > recent_oi[0]
    
    def _check_oi_trend_confirmation(
        self,
        oi_data: List[OpenInterest],
        direction: SignalDirection,
        price_change: float
    ) -> bool:
        """
        Check if OI trend confirms price direction (enhanced OI analysis).
        
        Args:
            oi_data: List of OI data points
            direction: Signal direction
            price_change: Price change percentage
            
        Returns:
            True if OI confirms the price trend
        """
        try:
            if not config.ENABLE_OI_FILTER or len(oi_data) < 5:
                return False
            
            # Calculate OI change
            oi_change = self._calculate_oi_change(oi_data)
            
            # For LONG: price up + OI up = strong confirmation
            # For SHORT: price down + OI up = strong confirmation
            if direction == SignalDirection.LONG:
                return price_change > 0 and oi_change > config.OI_CONFIRMATION_THRESHOLD
            elif direction == SignalDirection.SHORT:
                return price_change < 0 and oi_change > config.OI_CONFIRMATION_THRESHOLD
            
            return False
        except Exception as e:
            logger.error(f"Error checking OI trend confirmation: {e}")
            return False
    
    def _check_htf_trend_alignment(
        self,
        htf_indicators: IndicatorValues,
        direction: SignalDirection
    ) -> bool:
        """
        Check if higher timeframe (4H) trend aligns with signal direction.
        
        Args:
            htf_indicators: Higher timeframe indicator values
            direction: Signal direction
            
        Returns:
            True if HTF trend aligns
        """
        try:
            if not config.ENABLE_HTF_FILTER:
                return True  # Skip check if disabled
            
            if htf_indicators.ema_50 is None or htf_indicators.ema_200 is None:
                logger.debug("HTF EMA data not available")
                return True  # Allow signal if data unavailable
            
            htf_bullish = htf_indicators.ema_50 > htf_indicators.ema_200
            htf_bearish = htf_indicators.ema_50 < htf_indicators.ema_200
            
            if direction == SignalDirection.LONG:
                return htf_bullish
            elif direction == SignalDirection.SHORT:
                return htf_bearish
            
            return False
        except Exception as e:
            logger.error(f"Error checking HTF trend: {e}")
            return True  # Allow signal on error
    
    def _calculate_oi_change(self, oi_data: List[OpenInterest]) -> float:
        """
        Calculate percentage change in open interest.
        
        Args:
            oi_data: List of OI data points
            
        Returns:
            Percentage change
        """
        if len(oi_data) < 2:
            return 0.0
        
        first_oi = oi_data[0].open_interest
        last_oi = oi_data[-1].open_interest
        
        if first_oi == 0:
            return 0.0
        
        return ((last_oi - first_oi) / first_oi) * 100
