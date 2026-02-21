"""
Market scanner module.
Scans all USDT perpetual pairs to find the most volatile and active ones.
"""

from typing import List, Tuple
from dataclasses import dataclass

import pandas as pd
import numpy as np

from config import config
from services.bybit_service import BybitService, TickerInfo
from strategy.indicators import Indicators
from utils.helpers import logger, calculate_volatility_score


@dataclass
class VolatilePair:
    """Represents a volatile trading pair with metrics."""
    symbol: str
    price: float
    price_change_1h: float
    price_change_24h: float
    volume_24h: float
    volatility_score: float
    atr_ratio: float
    volume_spike: float


class MarketScanner:
    """Class for scanning markets and finding volatile pairs."""
    
    def __init__(self, bybit_service: BybitService):
        """
        Initialize market scanner.
        
        Args:
            bybit_service: BybitService instance for API calls
        """
        self.bybit = bybit_service
        self.indicators = Indicators()
        logger.info("MarketScanner initialized")
    
    async def scan_markets(self) -> List[VolatilePair]:
        """
        Scan all USDT perpetual markets and return top volatile pairs.
        
        Returns:
            List of VolatilePair objects sorted by volatility score
        """
        logger.info("Starting market scan...")
        
        # Step 1: Get all tickers
        tickers = await self.bybit.get_usdt_perpetual_tickers()
        
        if not tickers:
            logger.error("No tickers retrieved")
            return []
        
        logger.info(f"Retrieved {len(tickers)} tickers")
        
        # Step 2: Filter by minimum volume
        filtered_tickers = self._filter_by_volume(tickers)
        logger.info(f"After volume filter: {len(filtered_tickers)} pairs")
        
        # Step 3: Calculate volatility for each pair
        volatile_pairs = await self._calculate_volatility_for_pairs(filtered_tickers)
        
        # Step 4: Sort by volatility score and return top N
        volatile_pairs.sort(key=lambda x: x.volatility_score, reverse=True)
        top_pairs = volatile_pairs[:config.TOP_PAIRS_COUNT]
        
        logger.info(f"Top {len(top_pairs)} volatile pairs selected:")
        for pair in top_pairs:
            logger.info(f"  {pair.symbol}: score={pair.volatility_score:.2f}, "
                       f"1h_change={pair.price_change_1h:.2f}%, vol={pair.volume_24h:,.0f}")
        
        return top_pairs
    
    def _filter_by_volume(self, tickers: List[TickerInfo]) -> List[TickerInfo]:
        """
        Filter tickers by minimum 24h volume.
        
        Args:
            tickers: List of TickerInfo objects
            
        Returns:
            Filtered list
        """
        filtered = []
        for ticker in tickers:
            # Skip excluded pairs
            if ticker.symbol in config.EXCLUDED_PAIRS:
                continue
            
            # Check minimum volume
            if ticker.turnover_24h >= config.MIN_24H_VOLUME_USDT:
                filtered.append(ticker)
        
        return filtered
    
    async def _calculate_volatility_for_pairs(
        self,
        tickers: List[TickerInfo]
    ) -> List[VolatilePair]:
        """
        Calculate volatility metrics for each pair.
        
        Args:
            tickers: List of TickerInfo objects
            
        Returns:
            List of VolatilePair objects
        """
        volatile_pairs = []
        
        for ticker in tickers:
            try:
                # Get 1h klines for ATR calculation
                df_1h = await self.bybit.get_klines(
                    symbol=ticker.symbol,
                    interval=config.TIMEFRAME_VOLATILITY,
                    limit=50
                )
                
                if df_1h.empty or len(df_1h) < 20:
                    logger.debug(f"Insufficient 1h data for {ticker.symbol}")
                    continue
                
                # Calculate indicators
                indicators = self.indicators.calculate_all(df_1h)
                
                # Calculate ATR ratio
                atr_ratio = indicators.atr_ratio or 0
                
                # Calculate volume spike (compare to average)
                volume_spike = indicators.volume_ratio or 1.0
                
                # Calculate 1h price change from klines
                if len(df_1h) >= 2:
                    price_1h_ago = df_1h.iloc[-2]["close"]
                    price_change_1h = ((ticker.last_price - price_1h_ago) / price_1h_ago) * 100
                else:
                    price_change_1h = ticker.price_change_24h / 24  # Estimate from 24h
                
                # Calculate volatility score
                volatility_score = calculate_volatility_score(
                    price_change_1h=price_change_1h,
                    atr_ratio=atr_ratio,
                    volume_spike=volume_spike - 1.0  # Convert to excess over average
                )
                
                volatile_pair = VolatilePair(
                    symbol=ticker.symbol,
                    price=ticker.last_price,
                    price_change_1h=price_change_1h,
                    price_change_24h=ticker.price_change_24h,
                    volume_24h=ticker.volume_24h,
                    volatility_score=volatility_score,
                    atr_ratio=atr_ratio,
                    volume_spike=volume_spike
                )
                
                volatile_pairs.append(volatile_pair)
                
            except Exception as e:
                logger.warning(f"Error calculating volatility for {ticker.symbol}: {e}")
                continue
        
        return volatile_pairs
    
    async def get_pair_data_for_analysis(
        self,
        pair: VolatilePair
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """
        Get historical data for a pair for trend and entry analysis.
        
        Args:
            pair: VolatilePair object
            
        Returns:
            Tuple of (df_trend_15m, df_entry_5m)
        """
        # Get 15m data for trend analysis (need 200 periods for EMA200)
        df_trend = await self.bybit.get_klines(
            symbol=pair.symbol,
            interval=config.TIMEFRAME_TREND,
            limit=250
        )
        
        # Get 5m data for entry analysis
        df_entry = await self.bybit.get_klines(
            symbol=pair.symbol,
            interval=config.TIMEFRAME_ENTRY,
            limit=100
        )
        
        return df_trend, df_entry
    
    def is_market_volatile(self, pairs: List[VolatilePair]) -> bool:
        """
        Check if overall market is volatile enough for trading.
        
        Args:
            pairs: List of VolatilePair objects
            
        Returns:
            True if market conditions are suitable
        """
        if not pairs:
            return False
        
        # Calculate average volatility score
        avg_score = sum(p.volatility_score for p in pairs) / len(pairs)
        
        # Market is considered volatile if average score > threshold
        min_volatility_threshold = 1.0  # Adjust based on testing
        
        is_volatile = avg_score >= min_volatility_threshold
        
        if not is_volatile:
            logger.info(f"Market volatility low: avg_score={avg_score:.2f}")
        
        return is_volatile
    
    def get_scan_summary(self, pairs: List[VolatilePair]) -> dict:
        """
        Get summary statistics from scan.
        
        Args:
            pairs: List of VolatilePair objects
            
        Returns:
            Dictionary with summary stats
        """
        if not pairs:
            return {
                "pairs_scanned": 0,
                "top_volatility": 0,
                "avg_volatility": 0,
                "most_active_pair": None
            }
        
        scores = [p.volatility_score for p in pairs]
        top_pair = max(pairs, key=lambda x: x.volatility_score)
        
        return {
            "pairs_scanned": len(pairs),
            "top_volatility": max(scores),
            "avg_volatility": sum(scores) / len(scores),
            "most_active_pair": top_pair.symbol,
            "top_5_pairs": [p.symbol for p in pairs[:5]]
        }
