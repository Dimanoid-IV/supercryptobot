"""
Telegram service for sending trading signals.
Handles all Telegram bot interactions and message formatting.
"""

from typing import Optional, Callable
from dataclasses import dataclass
from datetime import datetime, time

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from config import config
from utils.helpers import logger, format_price, format_percentage


@dataclass
class SignalMessage:
    """Represents a trading signal message."""
    pair: str
    direction: str  # "LONG" or "SHORT"
    trend: str
    volatility: str
    volume_spike: bool
    oi_rising: bool
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    confidence: int
    additional_info: Optional[str] = None


class TelegramService:
    """Service class for Telegram notifications."""
    
    def __init__(self):
        """Initialize Telegram bot."""
        self.bot = Bot(token=config.TELEGRAM_TOKEN)
        self.chat_id = config.TELEGRAM_CHAT_ID
        
        # Signal control settings
        self.signals_enabled = True
        self.auto_start_time: Optional[time] = None  # e.g., time(9, 0) for 09:00
        self.auto_stop_time: Optional[time] = None   # e.g., time(21, 0) for 21:00
        
        # Callback for settings change
        self.on_settings_change: Optional[Callable] = None
        
        logger.info("TelegramService initialized")
    
    def is_signals_allowed(self) -> bool:
        """Check if signals are currently allowed (manual + auto schedule)."""
        # Manual override disabled
        if not self.signals_enabled:
            return False
        
        # No auto schedule set - always allowed
        if self.auto_start_time is None or self.auto_stop_time is None:
            return True
        
        # Check if current time is within allowed window
        now = datetime.now().time()
        
        if self.auto_start_time <= self.auto_stop_time:
            # Same day window (e.g., 09:00 - 21:00)
            return self.auto_start_time <= now <= self.auto_stop_time
        else:
            # Overnight window (e.g., 21:00 - 09:00)
            return now >= self.auto_start_time or now <= self.auto_stop_time
    
    def get_schedule_status(self) -> str:
        """Get human-readable schedule status."""
        if not self.signals_enabled:
            return "🔴 Сигналы ОТКЛЮЧЕНЫ"
        
        if self.auto_start_time is None or self.auto_stop_time is None:
            return "🟢 Сигналы ВКЛЮЧЕНЫ (без расписания, 24/7)"
        
        now = datetime.now().time()
        is_active = self.is_signals_allowed()
        
        status = "🟢" if is_active else "🟡"
        return f"{status} Расписание: {self.auto_start_time.strftime('%H:%M')} - {self.auto_stop_time.strftime('%H:%M')}"
    
    def toggle_signals(self) -> bool:
        """Toggle signals on/off."""
        self.signals_enabled = not self.signals_enabled
        logger.info(f"Signals toggled: {'ON' if self.signals_enabled else 'OFF'}")
        if self.on_settings_change:
            self.on_settings_change()
        return self.signals_enabled
    
    def set_schedule(self, start_time: Optional[time], stop_time: Optional[time]):
        """Set auto schedule for signals."""
        self.auto_start_time = start_time
        self.auto_stop_time = stop_time
        logger.info(f"Schedule set: {start_time} - {stop_time}")
        if self.on_settings_change:
            self.on_settings_change()
    
    def get_control_keyboard(self) -> InlineKeyboardMarkup:
        """Get inline keyboard for bot control."""
        keyboard = [
            [
                InlineKeyboardButton("🟢 Включить сигналы" if not self.signals_enabled else "🔴 Выключить сигналы", 
                                     callback_data="toggle_signals")
            ],
            [
                InlineKeyboardButton("📅 Установить расписание", callback_data="set_schedule")
            ],
            [
                InlineKeyboardButton("🌅 День (09:00-21:00)", callback_data="schedule_day"),
                InlineKeyboardButton("🌙 Ночь (21:00-09:00)", callback_data="schedule_night")
            ],
            [
                InlineKeyboardButton("⚡ Активно 24/7", callback_data="schedule_always")
            ],
            [
                InlineKeyboardButton("📊 Статус", callback_data="status")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)
    
    def _format_signal_message(self, signal: SignalMessage) -> str:
        """
        Format a signal message for Telegram.
        
        Args:
            signal: SignalMessage object containing signal details
            
        Returns:
            Formatted message string
        """
        # Determine emoji based on direction
        direction_emoji = "🟢" if signal.direction == "LONG" else "🔴"
        
        # Volatility emoji
        vol_emoji = "⚡" if "High" in signal.volatility else "📊"
        
        # Volume and OI indicators
        volume_indicator = "✅ Yes" if signal.volume_spike else "❌ No"
        oi_indicator = "📈 Rising" if signal.oi_rising else "📉 Falling"
        
        # Confidence bar
        confidence_bar = self._generate_confidence_bar(signal.confidence)
        
        message = f"""
{direction_emoji} <b>{signal.pair} — {signal.direction}</b>

{vol_emoji} <b>Market Conditions:</b>
• Trend: <code>{signal.trend}</code>
• Volatility: <code>{signal.volatility}</code>
• Volume Spike: {volume_indicator}
• Open Interest: {oi_indicator}

💰 <b>Trade Setup:</b>
• Entry: <code>{format_price(signal.entry_price)}</code>
• Stop Loss: <code>{format_price(signal.stop_loss)}</code>
• Take Profit: <code>{format_price(signal.take_profit)}</code>
• Risk/Reward: <code>1:{signal.risk_reward:.1f}</code>

📊 <b>Confidence: {signal.confidence}%</b>
{confidence_bar}

<i>Risk management is essential. This is not financial advice.</i>
"""
        
        if signal.additional_info:
            message += f"\n📝 <i>{signal.additional_info}</i>"
        
        return message.strip()
    
    def _generate_confidence_bar(self, confidence: int) -> str:
        """Generate a visual confidence bar."""
        filled = confidence // 10
        empty = 10 - filled
        bar = "█" * filled + "░" * empty
        return f"<code>[{bar}]</code>"
    
    async def send_signal(self, signal: SignalMessage) -> bool:
        """
        Send a trading signal to Telegram.
        
        Args:
            signal: SignalMessage object
            
        Returns:
            True if sent successfully, False otherwise
        """
        try:
            message = self._format_signal_message(signal)
            
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
            
            logger.info(f"Signal sent to Telegram for {signal.pair} {signal.direction}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False
    
    async def send_test_message(self) -> bool:
        """Send a test message to verify Telegram connection."""
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text="🤖 <b>Crypto Signal Bot</b> is now online and monitoring markets!",
                parse_mode=ParseMode.HTML
            )
            logger.info("Test message sent to Telegram")
            return True
        except Exception as e:
            logger.error(f"Failed to send test message: {e}")
            return False
    
    async def send_control_menu(self) -> bool:
        """Send control menu with inline keyboard."""
        try:
            status = self.get_schedule_status()
            message = f"""
🤖 <b>Управление Crypto Signal Bot</b>

{status}

<b>Команды:</b>
• /start - Показать это меню
• /status - Текущий статус
• /on - Включить сигналы
• /off - Выключить сигналы
• /schedule - Настроить расписание
"""
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode=ParseMode.HTML,
                reply_markup=self.get_control_keyboard()
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send control menu: {e}")
            return False
    
    async def handle_callback(self, callback_data: str) -> str:
        """Handle callback from inline keyboard."""
        if callback_data == "toggle_signals":
            new_state = self.toggle_signals()
            return f"Сигналы {'ВКЛЮЧЕНЫ' if new_state else 'ОТКЛЮЧЕНЫ'}"
        
        elif callback_data == "schedule_day":
            self.set_schedule(time(9, 0), time(21, 0))
            return "Расписание установлено: 09:00 - 21:00 (дневное)"
        
        elif callback_data == "schedule_night":
            self.set_schedule(time(21, 0), time(9, 0))
            return "Расписание установлено: 21:00 - 09:00 (ночное)"
        
        elif callback_data == "schedule_always":
            self.set_schedule(None, None)
            return "Расписание отключено. Сигналы активны 24/7"
        
        elif callback_data == "status":
            return self.get_schedule_status()
        
        elif callback_data == "set_schedule":
            return "Используйте команды:\n/schedule_day - 09:00-21:00\n/schedule_night - 21:00-09:00\n/schedule_always - 24/7"
        
        return "Неизвестная команда"
    
    async def send_status_update(self, message: str) -> bool:
        """
        Send a status update message.
        
        Args:
            message: Status message text
            
        Returns:
            True if sent successfully
        """
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=f"📊 <b>Bot Status</b>\n\n{message}",
                parse_mode=ParseMode.HTML
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send status update: {e}")
            return False
    
    async def send_error_notification(self, error_message: str) -> bool:
        """
        Send an error notification.
        
        Args:
            error_message: Error description
            
        Returns:
            True if sent successfully
        """
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=f"⚠️ <b>Error Alert</b>\n\n<code>{error_message}</code>",
                parse_mode=ParseMode.HTML
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send error notification: {e}")
            return False
    
    def create_signal_from_analysis(
        self,
        pair: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        score: int,
        trend_aligned: bool,
        volume_above_avg: bool,
        oi_rising: bool,
        atr_value: float,
        price_change_1h: float
    ) -> SignalMessage:
        """
        Create a SignalMessage from analysis results.
        
        Args:
            pair: Trading pair symbol
            direction: "LONG" or "SHORT"
            entry_price: Entry price
            stop_loss: Stop loss price
            take_profit: Take profit price
            score: Signal confidence score
            trend_aligned: Whether trend aligns with signal
            volume_above_avg: Whether volume is above average
            oi_rising: Whether open interest is rising
            atr_value: ATR value
            price_change_1h: 1h price change percentage
            
        Returns:
            SignalMessage object
        """
        # Determine trend description
        if direction == "LONG":
            trend = "Bullish" if trend_aligned else "Mixed"
        else:
            trend = "Bearish" if trend_aligned else "Mixed"
        
        # Determine volatility level
        abs_change = abs(price_change_1h)
        if abs_change > 3:
            volatility = "Very High"
        elif abs_change > 1.5:
            volatility = "High"
        elif abs_change > 0.5:
            volatility = "Moderate"
        else:
            volatility = "Low"
        
        # Calculate R:R ratio
        risk = abs(entry_price - stop_loss)
        reward = abs(take_profit - entry_price)
        risk_reward = reward / risk if risk > 0 else 0
        
        return SignalMessage(
            pair=pair,
            direction=direction,
            trend=trend,
            volatility=volatility,
            volume_spike=volume_above_avg,
            oi_rising=oi_rising,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward=risk_reward,
            confidence=score
        )
