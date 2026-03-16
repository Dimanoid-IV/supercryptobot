"""
Signal scoring module.
Calculates signal confidence score based on multiple factors.
Maximum score: 100 points
Minimum signal threshold: 75 points
"""

from typing import Dict, Any
from dataclasses import dataclass

from config import config
from utils.helpers import logger


@dataclass
class ScoreBreakdown:
    """Detailed breakdown of signal score."""
    # Base scores (existing)
    trend_alignment: int = 0
    ema_pullback: int = 0
    rsi_zone: int = 0
    volume_spike: int = 0
    atr_level: int = 0
    oi_confirmation: int = 0
    
    # Enhanced scores (new)
    htf_trend_filter: int = 0  # HTF EMA50/EMA200 alignment
    volume_anomaly: int = 0  # Volume > 2x average
    oi_trend_confirmation: int = 0  # OI confirms price direction
    market_regime: int = 0  # Trending vs flat market adjustment
    
    @property
    def total(self) -> int:
        """Calculate total score."""
        return (
            self.trend_alignment +
            self.ema_pullback +
            self.rsi_zone +
            self.volume_spike +
            self.atr_level +
            self.oi_confirmation
        )
    
    @property
    def weighted_total(self) -> int:
        """Calculate weighted score with enhanced factors."""
        return (
            self.total +
            self.htf_trend_filter +
            self.volume_anomaly +
            self.oi_trend_confirmation +
            self.market_regime
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging."""
        return {
            "trend_alignment": self.trend_alignment,
            "ema_pullback": self.ema_pullback,
            "rsi_zone": self.rsi_zone,
            "volume_spike": self.volume_spike,
            "atr_level": self.atr_level,
            "oi_confirmation": self.oi_confirmation,
            "total": self.total,
            "htf_trend_filter": self.htf_trend_filter,
            "volume_anomaly": self.volume_anomaly,
            "oi_trend_confirmation": self.oi_trend_confirmation,
            "market_regime": self.market_regime,
            "weighted_total": self.weighted_total
        }


class SignalScorer:
    """Class for calculating signal scores."""
    
    def __init__(self):
        """Initialize signal scorer."""
        pass
    
    def calculate_score(
        self,
        trend_aligned: bool,
        ema_pullback: bool,
        rsi_in_zone: bool,
        volume_above_avg: bool,
        atr_adequate: bool,
        oi_confirms: bool
    ) -> ScoreBreakdown:
        """
        Calculate signal score based on multiple factors.
        
        Scoring weights:
        - Trend alignment: 25 points
        - EMA pullback: 20 points
        - RSI in zone: 15 points
        - Volume spike: 15 points
        - ATR level: 10 points
        - OI confirmation: 15 points
        
        Args:
            trend_aligned: Whether trend aligns with signal direction
            ema_pullback: Whether price pulled back to EMA21 or EMA50
            rsi_in_zone: Whether RSI is in entry zone
            volume_above_avg: Whether volume is above average
            atr_adequate: Whether ATR is adequate for trading
            oi_confirms: Whether open interest confirms the move
            
        Returns:
            ScoreBreakdown object
        """
        breakdown = ScoreBreakdown()
        
        # Trend alignment (25 points)
        if trend_aligned:
            breakdown.trend_alignment = config.SCORE_TREND_ALIGNMENT
        
        # EMA pullback (20 points)
        if ema_pullback:
            breakdown.ema_pullback = config.SCORE_EMA_PULLBACK
        
        # RSI in zone (15 points)
        if rsi_in_zone:
            breakdown.rsi_zone = config.SCORE_RSI_ZONE
        
        # Volume spike (15 points)
        if volume_above_avg:
            breakdown.volume_spike = config.SCORE_VOLUME_SPIKE
        
        # ATR level (10 points)
        if atr_adequate:
            breakdown.atr_level = config.SCORE_ATR_LEVEL
        
        # OI confirmation (15 points)
        if oi_confirms:
            breakdown.oi_confirmation = config.SCORE_OI_CONFIRMATION
        
        return breakdown
    
    def is_signal_valid(self, score: int) -> bool:
        """
        Check if signal score meets minimum threshold.
        
        Args:
            score: Total signal score
            
        Returns:
            True if score >= MIN_SIGNAL_SCORE
        """
        return score >= config.MIN_SIGNAL_SCORE
    
    def get_score_quality(self, score: int) -> str:
        """
        Get quality rating for a score.
        
        Args:
            score: Total signal score
            
        Returns:
            Quality string: "excellent", "good", "acceptable", "poor"
        """
        if score >= 90:
            return "excellent"
        elif score >= 80:
            return "good"
        elif score >= config.MIN_SIGNAL_SCORE:
            return "acceptable"
        else:
            return "poor"
    
    def calculate_partial_score(
        self,
        trend_aligned: bool,
        ema_pullback: bool,
        rsi_value: float,
        rsi_zone_type: str,  # "long" or "short"
        volume_ratio: float,
        atr_ratio: float,
        oi_change_percent: float
    ) -> ScoreBreakdown:
        """
        Calculate score with partial credit for near-miss conditions.
        
        Args:
            trend_aligned: Whether trend aligns
            ema_pullback: Whether price is near EMA
            rsi_value: Current RSI value
            rsi_zone_type: "long" or "short" entry zone
            volume_ratio: Current volume / average volume
            atr_ratio: ATR / price ratio
            oi_change_percent: Open interest change percentage
            
        Returns:
            ScoreBreakdown with potentially partial scores
        """
        breakdown = ScoreBreakdown()
        
        # Trend alignment (binary: 25 or 0)
        if trend_aligned:
            breakdown.trend_alignment = config.SCORE_TREND_ALIGNMENT
        
        # EMA pullback (binary: 20 or 0)
        if ema_pullback:
            breakdown.ema_pullback = config.SCORE_EMA_PULLBACK
        
        # RSI zone (can have partial credit)
        breakdown.rsi_zone = self._score_rsi_zone(rsi_value, rsi_zone_type)
        
        # Volume (scaled based on how much above average)
        breakdown.volume_spike = self._score_volume(volume_ratio)
        
        # ATR (binary based on threshold)
        if atr_ratio >= 0.002:  # At least 0.2% of price
            breakdown.atr_level = config.SCORE_ATR_LEVEL
        
        # OI confirmation (scaled based on change)
        breakdown.oi_confirmation = self._score_oi_confirmation(oi_change_percent)
        
        return breakdown
    
    def _score_rsi_zone(self, rsi: float, zone_type: str) -> int:
        """
        Score RSI based on how well it's positioned in the entry zone.
        
        Args:
            rsi: RSI value
            zone_type: "long" or "short"
            
        Returns:
            Score from 0 to SCORE_RSI_ZONE
        """
        if rsi is None:
            return 0
        
        if zone_type == "long":
            # Ideal zone: 40-55
            if config.RSI_LONG_MIN <= rsi <= config.RSI_LONG_MAX:
                return config.SCORE_RSI_ZONE
            # Near zone: 35-40 or 55-60
            elif 35 <= rsi < config.RSI_LONG_MIN or config.RSI_LONG_MAX < rsi <= 60:
                return config.SCORE_RSI_ZONE // 2
            else:
                return 0
        else:  # short
            # Ideal zone: 45-60
            if config.RSI_SHORT_MIN <= rsi <= config.RSI_SHORT_MAX:
                return config.SCORE_RSI_ZONE
            # Near zone: 40-45 or 60-65
            elif 40 <= rsi < config.RSI_SHORT_MIN or config.RSI_SHORT_MAX < rsi <= 65:
                return config.SCORE_RSI_ZONE // 2
            else:
                return 0
    
    def _score_volume(self, volume_ratio: float) -> int:
        """
        Score volume based on ratio to average.
        
        Args:
            volume_ratio: Current volume / average volume
            
        Returns:
            Score from 0 to SCORE_VOLUME_SPIKE
        """
        if volume_ratio >= 1.5:  # 50% above average
            return config.SCORE_VOLUME_SPIKE
        elif volume_ratio >= 1.2:  # 20% above average
            return int(config.SCORE_VOLUME_SPIKE * 0.7)
        elif volume_ratio >= 1.0:  # At average
            return int(config.SCORE_VOLUME_SPIKE * 0.3)
        else:
            return 0
    
    def _score_oi_confirmation(self, oi_change_percent: float) -> int:
        """
        Score open interest confirmation.
        
        Args:
            oi_change_percent: OI change percentage over recent period
            
        Returns:
            Score from 0 to SCORE_OI_CONFIRMATION
        """
        abs_change = abs(oi_change_percent)
        
        if abs_change >= 5:  # Strong confirmation
            return config.SCORE_OI_CONFIRMATION
        elif abs_change >= 2:  # Moderate confirmation
            return int(config.SCORE_OI_CONFIRMATION * 0.6)
        elif abs_change >= 0.5:  # Weak confirmation
            return int(config.SCORE_OI_CONFIRMATION * 0.3)
        else:
            return 0
    
    def log_score_breakdown(self, pair: str, breakdown: ScoreBreakdown):
        """
        Log detailed score breakdown.
        
        Args:
            pair: Trading pair symbol
            breakdown: ScoreBreakdown object
        """
        logger.info(f"Score breakdown for {pair}:")
        logger.info(f"  Trend Alignment: {breakdown.trend_alignment}/{config.SCORE_TREND_ALIGNMENT}")
        logger.info(f"  EMA Pullback: {breakdown.ema_pullback}/{config.SCORE_EMA_PULLBACK}")
        logger.info(f"  RSI Zone: {breakdown.rsi_zone}/{config.SCORE_RSI_ZONE}")
        logger.info(f"  Volume Spike: {breakdown.volume_spike}/{config.SCORE_VOLUME_SPIKE}")
        logger.info(f"  ATR Level: {breakdown.atr_level}/{config.SCORE_ATR_LEVEL}")
        logger.info(f"  OI Confirmation: {breakdown.oi_confirmation}/{config.SCORE_OI_CONFIRMATION}")
        logger.info(f"  TOTAL: {breakdown.total}/{config.MAX_SCORE}")
        
        # Log enhanced scores if enabled
        if config.ENABLE_WEIGHTED_SCORE:
            logger.info(f"  HTF Trend Filter: {breakdown.htf_trend_filter:+d}")
            logger.info(f"  Volume Anomaly: {breakdown.volume_anomaly:+d}")
            logger.info(f"  OI Trend Confirmation: {breakdown.oi_trend_confirmation:+d}")
            logger.info(f"  Market Regime: {breakdown.market_regime:+d}")
            logger.info(f"  WEIGHTED TOTAL: {breakdown.weighted_total}")
    
    def calculate_enhanced_scores(
        self,
        breakdown: ScoreBreakdown,
        htf_trend_aligned: bool = False,
        volume_anomaly: bool = False,
        oi_confirms_trend: bool = False,
        is_trending_market: bool = False,
        signal_direction: str = ""
    ) -> ScoreBreakdown:
        """
        Calculate enhanced score components.
        
        Args:
            breakdown: Base ScoreBreakdown to enhance
            htf_trend_aligned: Whether 4H trend aligns with signal
            volume_anomaly: Whether volume > 2x average
            oi_confirms_trend: Whether OI confirms price direction
            is_trending_market: Whether ADX > 25
            signal_direction: "LONG" or "SHORT"
            
        Returns:
            Updated ScoreBreakdown with enhanced scores
        """
        try:
            # HTF Trend Filter bonus
            if config.ENABLE_HTF_FILTER and htf_trend_aligned:
                breakdown.htf_trend_filter = config.SCORE_TREND_ALIGNMENT // 2  # Half of trend score
                logger.debug(f"HTF trend aligned for {signal_direction}: +{breakdown.htf_trend_filter}")
            
            # Volume Anomaly bonus
            if config.ENABLE_VOLUME_ANOMALY and volume_anomaly:
                breakdown.volume_anomaly = config.VOLUME_ANOMALY_BOOST
                logger.debug(f"Volume anomaly detected: +{breakdown.volume_anomaly}")
            
            # OI Trend Confirmation bonus
            if config.ENABLE_OI_FILTER and oi_confirms_trend:
                breakdown.oi_trend_confirmation = config.OI_TREND_SCORE_BOOST
                logger.debug(f"OI confirms trend: +{breakdown.oi_trend_confirmation}")
            
            # Market Regime adjustment
            if config.ENABLE_MARKET_REGIME:
                if is_trending_market:
                    # Boost trend-following signals in trending markets
                    if signal_direction in ["LONG", "SHORT"]:
                        breakdown.market_regime = config.TREND_REGIME_BOOST
                        logger.debug(f"Trending market regime: +{breakdown.market_regime}")
                else:
                    # Penalize signals in flat markets
                    breakdown.market_regime = config.FLAT_REGIME_PENALTY
                    logger.debug(f"Flat market regime: {breakdown.market_regime}")
            
        except Exception as e:
            logger.error(f"Error calculating enhanced scores: {e}")
            # Continue with base scores on error
        
        return breakdown
