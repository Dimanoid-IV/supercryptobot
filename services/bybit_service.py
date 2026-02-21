"""
Bybit API service wrapper.
Handles all interactions with Bybit API including rate limiting and error handling.
"""

import asyncio
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

import pandas as pd
from pybit.unified_trading import HTTP

from config import config
from utils.helpers import logger


@dataclass
class TickerInfo:
    """Represents ticker information for a trading pair."""
    symbol: str
    last_price: float
    price_change_24h: float
    price_change_percent_24h: float
    volume_24h: float
    turnover_24h: float
    high_price_24h: float
    low_price_24h: float


@dataclass
class KlineData:
    """Represents OHLCV kline data."""
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float


@dataclass
class OpenInterest:
    """Represents open interest data."""
    symbol: str
    open_interest: float
    timestamp: int


@dataclass
class FundingRate:
    """Represents funding rate data."""
    symbol: str
    funding_rate: float
    next_funding_time: int


class BybitService:
    """Service class for Bybit API interactions."""
    
    def __init__(self):
        """Initialize Bybit API client."""
        self.session = HTTP(
            testnet=config.BYBIT_TESTNET,
            api_key=config.BYBIT_API_KEY,
            api_secret=config.BYBIT_SECRET,
        )
        self._last_api_call_time = 0
        logger.info("BybitService initialized")
    
    async def _rate_limit(self):
        """Apply rate limiting between API calls."""
        import time
        current_time = time.time() * 1000
        time_since_last = current_time - self._last_api_call_time
        
        if time_since_last < config.API_CALL_DELAY_MS:
            delay = (config.API_CALL_DELAY_MS - time_since_last) / 1000
            await asyncio.sleep(delay)
        
        self._last_api_call_time = time.time() * 1000
    
    async def _make_request_with_retry(self, method, *args, **kwargs) -> Any:
        """Make API request with retry logic."""
        for attempt in range(config.MAX_RETRIES):
            try:
                await self._rate_limit()
                result = method(*args, **kwargs)
                
                # Check if result contains error
                if isinstance(result, dict) and result.get("retCode") != 0:
                    error_msg = result.get("retMsg", "Unknown error")
                    logger.warning(f"API error: {error_msg}")
                    if attempt < config.MAX_RETRIES - 1:
                        await asyncio.sleep(config.RETRY_DELAY_SECONDS * (attempt + 1))
                        continue
                    return None
                
                return result
                
            except Exception as e:
                logger.error(f"API request failed (attempt {attempt + 1}): {e}")
                if attempt < config.MAX_RETRIES - 1:
                    await asyncio.sleep(config.RETRY_DELAY_SECONDS * (attempt + 1))
                else:
                    raise
        
        return None
    
    async def get_usdt_perpetual_tickers(self) -> List[TickerInfo]:
        """
        Get all USDT perpetual trading pairs.
        
        Returns:
            List of TickerInfo objects for USDT perpetual pairs.
        """
        try:
            response = await self._make_request_with_retry(
                self.session.get_tickers,
                category="linear",
                baseCoin="USDT"
            )
            
            if not response or "result" not in response:
                logger.error("Failed to get tickers: invalid response")
                return []
            
            tickers = []
            for item in response["result"].get("list", []):
                symbol = item.get("symbol", "")
                
                # Filter for USDT perpetual only
                if not symbol.endswith("USDT"):
                    continue
                
                try:
                    ticker = TickerInfo(
                        symbol=symbol,
                        last_price=float(item.get("lastPrice", 0) or 0),
                        price_change_24h=float(item.get("price24hPcnt", 0) or 0) * 100,  # Convert to percentage
                        price_change_percent_24h=float(item.get("price24hPcnt", 0) or 0) * 100,
                        volume_24h=float(item.get("volume24h", 0) or 0),
                        turnover_24h=float(item.get("turnover24h", 0) or 0),
                        high_price_24h=float(item.get("highPrice24h", 0) or 0),
                        low_price_24h=float(item.get("lowPrice24h", 0) or 0),
                    )
                    tickers.append(ticker)
                except (ValueError, TypeError) as e:
                    logger.warning(f"Failed to parse ticker for {symbol}: {e}")
                    continue
            
            logger.info(f"Fetched {len(tickers)} USDT perpetual tickers")
            return tickers
            
        except Exception as e:
            logger.error(f"Error fetching tickers: {e}")
            return []
    
    async def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 200
    ) -> pd.DataFrame:
        """
        Get kline/candlestick data for a symbol.
        
        Args:
            symbol: Trading pair symbol (e.g., "BTCUSDT")
            interval: Timeframe interval (e.g., "5", "15", "60")
            limit: Number of candles to fetch
            
        Returns:
            DataFrame with OHLCV data
        """
        try:
            response = await self._make_request_with_retry(
                self.session.get_kline,
                category="linear",
                symbol=symbol,
                interval=interval,
                limit=limit
            )
            
            if not response or "result" not in response:
                logger.error(f"Failed to get klines for {symbol}: invalid response")
                return pd.DataFrame()
            
            klines = response["result"].get("list", [])
            if not klines:
                return pd.DataFrame()
            
            # Parse kline data
            data = []
            for kline in klines:
                try:
                    data.append({
                        "timestamp": int(kline[0]),
                        "open": float(kline[1]),
                        "high": float(kline[2]),
                        "low": float(kline[3]),
                        "close": float(kline[4]),
                        "volume": float(kline[5]),
                        "turnover": float(kline[6]),
                    })
                except (ValueError, TypeError, IndexError) as e:
                    logger.warning(f"Failed to parse kline for {symbol}: {e}")
                    continue
            
            df = pd.DataFrame(data)
            if not df.empty:
                df = df.sort_values("timestamp").reset_index(drop=True)
            
            return df
            
        except Exception as e:
            logger.error(f"Error fetching klines for {symbol}: {e}")
            return pd.DataFrame()
    
    async def get_open_interest(
        self,
        symbol: str,
        interval: str = "5min",
        limit: int = 50
    ) -> List[OpenInterest]:
        """
        Get open interest data for a symbol.
        
        Args:
            symbol: Trading pair symbol
            interval: Time interval for OI data
            limit: Number of data points
            
        Returns:
            List of OpenInterest objects
        """
        try:
            response = await self._make_request_with_retry(
                self.session.get_open_interest,
                category="linear",
                symbol=symbol,
                interval=interval,
                limit=limit
            )
            
            if not response or "result" not in response:
                return []
            
            oi_data = []
            for item in response["result"].get("list", []):
                try:
                    oi_data.append(OpenInterest(
                        symbol=symbol,
                        open_interest=float(item.get("openInterest", 0)),
                        timestamp=int(item.get("openInterestValue", 0))
                    ))
                except (ValueError, TypeError) as e:
                    logger.warning(f"Failed to parse OI for {symbol}: {e}")
                    continue
            
            return oi_data
            
        except Exception as e:
            logger.error(f"Error fetching open interest for {symbol}: {e}")
            return []
    
    async def get_funding_rate(self, symbol: str) -> Optional[FundingRate]:
        """
        Get current funding rate for a symbol.
        
        Args:
            symbol: Trading pair symbol
            
        Returns:
            FundingRate object or None
        """
        try:
            response = await self._make_request_with_retry(
                self.session.get_funding_rate,
                category="linear",
                symbol=symbol,
                limit=1
            )
            
            if not response or "result" not in response:
                return None
            
            items = response["result"].get("list", [])
            if not items:
                return None
            
            item = items[0]
            return FundingRate(
                symbol=symbol,
                funding_rate=float(item.get("fundingRate", 0)),
                next_funding_time=int(item.get("nextFundingTime", 0))
            )
            
        except Exception as e:
            logger.error(f"Error fetching funding rate for {symbol}: {e}")
            return None
    
    async def get_recent_trades(self, symbol: str, limit: int = 100) -> List[Dict]:
        """
        Get recent trades for a symbol.
        
        Args:
            symbol: Trading pair symbol
            limit: Number of trades to fetch
            
        Returns:
            List of trade dictionaries
        """
        try:
            response = await self._make_request_with_retry(
                self.session.get_public_trade_history,
                category="linear",
                symbol=symbol,
                limit=limit
            )
            
            if not response or "result" not in response:
                return []
            
            return response["result"].get("list", [])
            
        except Exception as e:
            logger.error(f"Error fetching recent trades for {symbol}: {e}")
            return []
