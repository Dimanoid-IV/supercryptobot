"""
Main entry point for Crypto Signal Bot.
Runs the main event loop for scanning markets and generating signals.
"""

import asyncio
import sys
from typing import List

from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

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
        
        # Send startup message and control menu
        await self.telegram_service.send_test_message()
        await self.telegram_service.send_control_menu()
        
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
                    
                    # Check if signals are allowed by schedule
                    if not self.telegram_service.is_signals_allowed():
                        logger.info(f"[SIGNAL] Signal blocked by schedule/settings")
                        continue
                    
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


# Global bot instance for command handlers
_bot_instance: Optional[CryptoSignalBot] = None

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    if _bot_instance:
        await _bot_instance.telegram_service.send_control_menu()

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command."""
    if _bot_instance:
        status = _bot_instance.telegram_service.get_schedule_status()
        await update.message.reply_text(status)

async def on_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /on command."""
    if _bot_instance:
        _bot_instance.telegram_service.signals_enabled = True
        await update.message.reply_text("🟢 Сигналы ВКЛЮЧЕНЫ")

async def off_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /off command."""
    if _bot_instance:
        _bot_instance.telegram_service.signals_enabled = False
        await update.message.reply_text("🔴 Сигналы ОТКЛЮЧЕНЫ")

async def schedule_day_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /schedule_day command."""
    if _bot_instance:
        from datetime import time
        _bot_instance.telegram_service.set_schedule(time(9, 0), time(21, 0))
        await update.message.reply_text("🌅 Расписание: 09:00 - 21:00")

async def schedule_night_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /schedule_night command."""
    if _bot_instance:
        from datetime import time
        _bot_instance.telegram_service.set_schedule(time(21, 0), time(9, 0))
        await update.message.reply_text("🌙 Расписание: 21:00 - 09:00")

async def schedule_always_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /schedule_always command."""
    if _bot_instance:
        _bot_instance.telegram_service.set_schedule(None, None)
        await update.message.reply_text("⚡ Сигналы активны 24/7")

async def add_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add_user command - add new subscriber (admin only)."""
    if not _bot_instance:
        return
    
    # Only admin can add users
    if str(update.effective_chat.id) != str(_bot_instance.telegram_service.chat_id):
        await update.message.reply_text("⛔ У вас нет прав для этой команды")
        return
    
    if not context.args:
        await update.message.reply_text("Использование: /add_user <chat_id>")
        return
    
    chat_id = context.args[0]
    if _bot_instance.telegram_service.add_subscriber(chat_id):
        await update.message.reply_text(f"✅ Пользователь {chat_id} добавлен")
    else:
        await update.message.reply_text(f"ℹ️ Пользователь {chat_id} уже в списке")

async def remove_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /remove_user command - remove subscriber (admin only)."""
    if not _bot_instance:
        return
    
    # Only admin can remove users
    if str(update.effective_chat.id) != str(_bot_instance.telegram_service.chat_id):
        await update.message.reply_text("⛔ У вас нет прав для этой команды")
        return
    
    if not context.args:
        await update.message.reply_text("Использование: /remove_user <chat_id>")
        return
    
    chat_id = context.args[0]
    if _bot_instance.telegram_service.remove_subscriber(chat_id):
        await update.message.reply_text(f"✅ Пользователь {chat_id} удалён")
    else:
        await update.message.reply_text(f"ℹ️ Пользователь {chat_id} не найден")

async def list_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list_users command - list all subscribers (admin only)."""
    if not _bot_instance:
        return
    
    # Only admin can list users
    if str(update.effective_chat.id) != str(_bot_instance.telegram_service.chat_id):
        await update.message.reply_text("⛔ У вас нет прав для этой команды")
        return
    
    subscribers = _bot_instance.telegram_service.subscribers
    count = len(subscribers)
    
    if count == 0:
        await update.message.reply_text("📋 Список подписчиков пуст")
    else:
        users_list = "\n".join([f"• {uid}" for uid in subscribers])
        await update.message.reply_text(f"📋 Подписчики ({count}):\n{users_list}")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard callbacks."""
    query = update.callback_query
    await query.answer()
    
    if _bot_instance:
        result = await _bot_instance.telegram_service.handle_callback(query.data)
        await query.edit_message_text(
            text=f"{result}\n\nНажмите /start для меню",
            reply_markup=_bot_instance.telegram_service.get_control_keyboard()
        )

async def run_telegram_app(bot: CryptoSignalBot):
    """Run Telegram bot application for command handling."""
    global _bot_instance
    _bot_instance = bot
    
    application = Application.builder().token(config.TELEGRAM_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("op", start_command))  # Alias for menu
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("on", on_command))
    application.add_handler(CommandHandler("off", off_command))
    application.add_handler(CommandHandler("schedule_day", schedule_day_command))
    application.add_handler(CommandHandler("schedule_night", schedule_night_command))
    application.add_handler(CommandHandler("schedule_always", schedule_always_command))
    application.add_handler(CommandHandler("add_user", add_user_command))
    application.add_handler(CommandHandler("remove_user", remove_user_command))
    application.add_handler(CommandHandler("list_users", list_users_command))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Start the bot
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    logger.info("Telegram command handlers started")
    
    # Keep running until main bot stops
    while bot.is_running:
        await asyncio.sleep(1)
    
    await application.updater.stop()
    await application.stop()
    await application.shutdown()

async def main():
    """Main entry point."""
    bot = CryptoSignalBot()
    
    try:
        # Run both the main bot and Telegram command handler
        await asyncio.gather(
            bot.start(),
            run_telegram_app(bot)
        )
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    # Run the bot
    asyncio.run(main())
