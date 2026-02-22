"""
Main entry point for Crypto Signal Bot.
Runs the main event loop for scanning markets and generating signals.
"""

import asyncio
import sys
from typing import List

from config import config
from utils.helpers import logger, CooldownManager
from services.bybit_service import BybitService
from services.telegram_service import TelegramService, SignalMessage
from scanner.market_scanner import MarketScanner, VolatilePair
from strategy.signal_logic import SignalGenerator, TradingSignal, SignalDirection


class CryptoSignalBot:
    """Main bot class that orchestrates scanning and signal generation."""
    
    def __init__(self):
        """Initialize the bot and its components."""
        # Validate configuration
        config_errors = config.validate()
        if config_errors:
            logger.error("Configuration errors:")
            for error in config_errors:
                logger.error(f"  - {error}")
            sys.exit(1)
        
        logger.info("Initializing Crypto Signal Bot...")
        
        # Initialize services
        self.bybit_service = BybitService()
        self.telegram_service = TelegramService()
        self.market_scanner = MarketScanner(self.bybit_service)
        self.signal_generator = SignalGenerator()
        self.cooldown_manager = CooldownManager()
        
        # Track running state
        self.is_running = False
        
        logger.info("Bot initialization complete")
    
    async def start(self):
        """Start the bot and run main loop."""
        self.is_running = True
        
        # Send startup message
        await self.telegram_service.send_test_message()
        
        logger.info("=" * 50)
        logger.info("Crypto Signal Bot started")
        logger.info(f"Scan interval: {config.SCAN_INTERVAL_SECONDS}s")
        logger.info(f"Min signal score: {config.MIN_SIGNAL_SCORE}")
        logger.info(f"Max signals per day: {config.MAX_SIGNALS_PER_DAY}")
        logger.info("=" * 50)
        
        # Main loop
        while self.is_running:
            try:
                await self._run_scan_cycle()
                
                # Wait before next scan
                logger.info(f"Sleeping for {config.SCAN_INTERVAL_SECONDS} seconds...")
                await asyncio.sleep(config.SCAN_INTERVAL_SECONDS)
                
            except KeyboardInterrupt:
                logger.info("Bot stopped by user")
                self.is_running = False
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                await self.telegram_service.send_error_notification(str(e))
                await asyncio.sleep(config.SCAN_INTERVAL_SECONDS)
    
    async def _run_scan_cycle(self):
        """Run a single scan cycle."""
        logger.info("\n" + "=" * 50)
        logger.info("Starting new scan cycle")
        logger.info("=" * 50)
        
        # Step 1: Scan markets for volatile pairs
        top_pairs = await self.market_scanner.scan_markets()
        
        if not top_pairs:
            logger.warning("No volatile pairs found")
            return
        
        # Check market volatility
        if not self.market_scanner.is_market_volatile(top_pairs):
            logger.info("Market conditions not suitable for trading")
            return
        
        # Log scan summary
        summary = self.market_scanner.get_scan_summary(top_pairs)
        logger.info(f"Scan summary: {summary}")
        
        # Step 2: Analyze each pair for signals
        signals_found = 0
        
        for pair in top_pairs:
            # Check cooldown
            if not self.cooldown_manager.can_signal(pair.symbol):
                logger.info(f"Skipping {pair.symbol}: cooldown active")
                continue
            
            try:
                signal = await self._analyze_pair(pair)
                
                if signal and signal.is_valid:
                    logger.info(f"[SIGNAL] VALID SIGNAL FOUND: {signal.pair} {signal.direction.value} (score: {signal.score})")
                    # Send signal
                    await self._send_signal(signal)
                    self.cooldown_manager.record_signal(pair.symbol)
                    signals_found += 1
                    logger.info(f"[SIGNAL] Signal processing complete for {signal.pair}")
                    
                    # Stop if max signals reached
                    if signals_found >= config.MAX_SIGNALS_PER_DAY:
                        logger.info("Max daily signals reached")
                        break
                
            except Exception as e:
                logger.error(f"Error analyzing {pair.symbol}: {e}")
                continue
        
        logger.info(f"Scan cycle complete. Signals found: {signals_found}")
        
        # Log cooldown stats
        cooldown_stats = self.cooldown_manager.get_stats()
        logger.info(f"Cooldown stats: {cooldown_stats}")
    
    async def _analyze_pair(self, pair: VolatilePair) -> TradingSignal:
        """
        Analyze a single pair for trading signals.
        
        Args:
            pair: VolatilePair to analyze
            
        Returns:
            TradingSignal if found, None otherwise
        """
        logger.info(f"\nAnalyzing {pair.symbol}...")
        
        # Get historical data
        df_trend, df_entry = await self.market_scanner.get_pair_data_for_analysis(pair)
        
        if df_trend.empty or df_entry.empty:
            logger.warning(f"Insufficient data for {pair.symbol}")
            return None
        
        # Get Open Interest data
        oi_data = await self.bybit_service.get_open_interest(
            symbol=pair.symbol,
            interval="5min",
            limit=50
        )
        
        # Get Funding Rate
        funding_rate = await self.bybit_service.get_funding_rate(pair.symbol)
        
        # Generate signal
        signal = await self.signal_generator.generate_signal(
            pair=pair.symbol,
            df_trend=df_trend,
            df_entry=df_entry,
            oi_data=oi_data,
            funding_rate=funding_rate
        )
        
        return signal
    
    async def _send_signal(self, signal: TradingSignal):
        """
        Send signal via Telegram.
        
        Args:
            signal: TradingSignal to send
        """
        logger.info(f"[TELEGRAM] SENDING SIGNAL: {signal.pair} {signal.direction.value} @ {signal.entry_price}")
        
        try:
            # Create SignalMessage from TradingSignal
            signal_message = self.telegram_service.create_signal_from_analysis(
                pair=signal.pair,
                direction=signal.direction.value,
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                score=signal.score,
                trend_aligned=(
                    signal.trend_analysis.is_aligned_for_long 
                    if signal.direction == SignalDirection.LONG 
                    else signal.trend_analysis.is_aligned_for_short
                ),
                volume_above_avg=(
                    signal.indicators.volume_ratio is not None and 
                    signal.indicators.volume_ratio >= config.VOLUME_THRESHOLD
                ),
                oi_rising=True,  # Simplified - could be enhanced
                atr_value=signal.indicators.atr or 0,
                price_change_1h=0  # Could be passed from scanner
            )
            
            # Send to Telegram
            success = await self.telegram_service.send_signal(signal_message)
            
            if success:
                logger.info(f"[TELEGRAM] Signal sent successfully for {signal.pair}")
            else:
                logger.error(f"[TELEGRAM] Failed to send signal for {signal.pair}")
                
        except Exception as e:
            logger.error(f"[TELEGRAM] Exception while sending signal for {signal.pair}: {e}")
            raise
    
    def stop(self):
        """Stop the bot."""
        logger.info("Stopping bot...")
        self.is_running = False


async def main():
    """Main entry point."""
    bot = CryptoSignalBot()
    
    try:
        await bot.start()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    # Run the bot
    asyncio.run(main())
