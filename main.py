"""
Main entry point for Crypto Signal Bot.
Runs the main event loop for scanning markets and generating signals.
"""

import asyncio
import sys
from typing import List, Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from config import config
from utils.helpers import logger, CooldownManager
from services.bybit_service import BybitService
from services.telegram_service import TelegramService, SignalMessage
from scanner.market_scanner import MarketScanner, VolatilePair
from strategy.signal_logic import SignalGenerator, TradingSignal, SignalDirection

# Import command handlers
from handlers import admin_commands, user_commands


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

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard callbacks."""
    query = update.callback_query
    await query.answer()
    
    if not _bot_instance:
        return
    
    callback_data = query.data
    
    # Handle new user callbacks
    if callback_data == "request_trial":
        await query.edit_message_text(
            text="✅ <b>Запрос отправлен!</b>\n\n"
                 "Администратор скоро активирует ваш пробный период.\n"
                 "Вы получите уведомление когда всё будет готово.\n\n"
                 "Обычно это занимает несколько минут.",
            parse_mode='HTML'
        )
        
        # Notify admin about trial request
        user = update.effective_user
        chat_id = str(update.effective_chat.id)
        try:
            admin_msg = f"""⏰ <b>Запрос на пробный период!</b>

👤 Имя: {user.first_name if user else 'N/A'}
🔹 Username: @{user.username if user and user.username else 'N/A'}
🆔 Chat ID: <code>{chat_id}</code>

Быстрое добавление:
<code>/add_user {chat_id} 2</code>
"""
            await _bot_instance.telegram_service.bot.send_message(
                chat_id=_bot_instance.telegram_service.chat_id,
                text=admin_msg,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Failed to notify admin about trial request: {e}")
        return
    
    if callback_data == "about_signals":
        about_text = """📊 <b>О наших сигналах:</b>

<b>Анализируем:</b>
• Тренд на 15-минутном таймфрейме
• Точку входа на 5-минутном
• Объемы торгов (Volume Spike)
• Open Interest изменения
• Funding Rate
• ATR для расчета стоп-лосса

<b>Каждый сигнал содержит:</b>
• Направление (LONG/SHORT)
• Точку входа
• Stop Loss
• Take Profit
• Risk/Reward ratio
• Confidence score (75%+)

<b>Пример сигнала:</b>
<code>BTCUSDT — LONG
Entry: 43250.00
SL: 42800.00
TP: 44100.00
R/R: 1:2.5
Confidence: 82%</code>

Нажмите /start чтобы запросить пробный период!
"""
        await query.edit_message_text(
            text=about_text,
            parse_mode='HTML'
        )
        return
    
    # Handle existing control menu callbacks
    result = await _bot_instance.telegram_service.handle_callback(callback_data)
    await query.edit_message_text(
        text=f"{result}\n\nНажмите /start для меню",
        reply_markup=_bot_instance.telegram_service.get_control_keyboard()
    )


def setup_command_handlers(bot_instance: CryptoSignalBot):
    """Setup bot instance for all command handlers."""
    global _bot_instance
    _bot_instance = bot_instance
    admin_commands.set_bot_instance(bot_instance)
    user_commands.set_bot_instance(bot_instance)

async def subscription_checker_task(bot: CryptoSignalBot):
    """Background task to check subscription expirations daily."""
    from datetime import datetime, timedelta
    
    logger.info("Subscription checker task started")
    
    # Wait a bit for bot to fully initialize
    await asyncio.sleep(60)
    
    last_check_date = None
    
    while bot.is_running:
        try:
            now = datetime.now()
            current_date = now.date()
            
            # Check once per day at 9:00 AM
            if current_date != last_check_date and now.hour >= 9:
                logger.info("Running daily subscription check...")
                
                service = bot.telegram_service
                
                # Check for expiring and expired subscriptions
                expiring_soon, just_expired = await service.check_expiring_subscriptions(days_before=3)
                
                # Notify users about upcoming expiry
                if expiring_soon:
                    await service.notify_expiring_users(expiring_soon)
                
                # Notify users whose subscriptions just expired
                if just_expired:
                    await service.notify_expired_users(just_expired)
                
                # Remove expired subscribers from the list
                removed = service.remove_expired_subscribers()
                
                # Notify admin about all expirations
                await service.notify_admin_about_expired(removed, expiring_soon)
                
                if expiring_soon or removed:
                    logger.info(f"Subscription check complete: {len(expiring_soon)} expiring soon, {len(removed)} removed")
                else:
                    logger.info("Subscription check complete: no expirations")
                
                last_check_date = current_date
            
            # Check every hour
            await asyncio.sleep(3600)
            
        except Exception as e:
            logger.error(f"Error in subscription checker: {e}")
            await asyncio.sleep(3600)  # Wait an hour before retrying

async def run_telegram_app(bot: CryptoSignalBot):
    """Run Telegram bot application for command handling."""
    # Setup command handlers with bot instance
    setup_command_handlers(bot)
    
    application = Application.builder().token(config.TELEGRAM_TOKEN).build()
    
    # Add command handlers - Admin commands
    application.add_handler(CommandHandler("add_user", admin_commands.add_user_command))
    application.add_handler(CommandHandler("remove_user", admin_commands.remove_user_command))
    application.add_handler(CommandHandler("list_users", admin_commands.list_users_command))
    application.add_handler(CommandHandler("extend", admin_commands.extend_command))
    application.add_handler(CommandHandler("user_info", admin_commands.user_info_command))
    
    # Add command handlers - User commands
    application.add_handler(CommandHandler("start", user_commands.start_command))
    application.add_handler(CommandHandler("op", user_commands.start_command))  # Alias for menu
    application.add_handler(CommandHandler("status", user_commands.status_command))
    application.add_handler(CommandHandler("on", user_commands.on_command))
    application.add_handler(CommandHandler("off", user_commands.off_command))
    application.add_handler(CommandHandler("schedule_day", user_commands.schedule_day_command))
    application.add_handler(CommandHandler("schedule_night", user_commands.schedule_night_command))
    application.add_handler(CommandHandler("schedule_always", user_commands.schedule_always_command))
    application.add_handler(CommandHandler("mysettings", user_commands.mysettings_command))
    application.add_handler(CommandHandler("toggle", user_commands.toggle_command))
    application.add_handler(CommandHandler("setconf", user_commands.setconf_command))
    application.add_handler(CommandHandler("setschedule_day", user_commands.setschedule_day_command))
    application.add_handler(CommandHandler("setschedule_night", user_commands.setschedule_night_command))
    application.add_handler(CommandHandler("setschedule_always", user_commands.setschedule_always_command))
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
        # Run the main bot, Telegram command handler, and subscription checker
        await asyncio.gather(
            bot.start(),
            run_telegram_app(bot),
            subscription_checker_task(bot)
        )
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    # Run the bot
    asyncio.run(main())
