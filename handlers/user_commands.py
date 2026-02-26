"""
User command handlers for Crypto Signal Bot.
Handles user settings and preferences.
"""

from telegram import Update
from telegram.ext import ContextTypes


# Global bot instance reference (set from main.py)
_bot_instance = None


def set_bot_instance(bot):
    """Set the global bot instance reference."""
    global _bot_instance
    _bot_instance = bot


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    # Register username for future reference
    if _bot_instance and update.effective_user:
        username = update.effective_user.username
        chat_id = str(update.effective_chat.id)
        if username:
            _bot_instance.telegram_service.register_username(username, chat_id)
        
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


async def mysettings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /mysettings command - show user their settings."""
    if not _bot_instance:
        return
    
    chat_id = str(update.effective_chat.id)
    settings = _bot_instance.telegram_service.get_user_settings(chat_id)
    
    schedule_str = "24/7 (без ограничений)"
    if settings.schedule_start and settings.schedule_end:
        schedule_str = f"{settings.schedule_start} - {settings.schedule_end}"
    
    status = "🟢 Включены" if settings.signals_enabled else "🔴 Отключены"
    
    message = f"""⚙️ <b>Ваши настройки:</b>

📊 <b>Сигналы:</b> {status}
🎯 <b>Мин. confidence:</b> {settings.min_confidence}%
📅 <b>Расписание:</b> {schedule_str}

<b>Команды для изменения:</b>
• /toggle - Вкл/выкл сигналы
• /setconf 80 - Установить confidence (75-95)
• /setschedule_day - День (09:00-21:00)
• /setschedule_night - Ночь (21:00-09:00)
• /setschedule_always - 24/7
"""
    await update.message.reply_text(message, parse_mode='HTML')


async def toggle_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /toggle command - toggle signals for user."""
    if not _bot_instance:
        return
    
    chat_id = str(update.effective_chat.id)
    service = _bot_instance.telegram_service
    settings = service.get_user_settings(chat_id)
    
    new_state = not settings.signals_enabled
    service.update_user_settings(chat_id, signals_enabled=new_state)
    
    status = "🟢 ВКЛЮЧЕНЫ" if new_state else "🔴 ОТКЛЮЧЕНЫ"
    await update.message.reply_text(f"Сигналы {status}")


async def setconf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /setconf command - set min confidence for user."""
    if not _bot_instance:
        return
    
    chat_id = str(update.effective_chat.id)
    
    if not context.args:
        await update.message.reply_text("Использование: /setconf <75-95>\nПример: /setconf 80")
        return
    
    try:
        conf = int(context.args[0])
        if conf < 50 or conf > 95:
            await update.message.reply_text("❌ Confidence должен быть от 50 до 95")
            return
        
        _bot_instance.telegram_service.update_user_settings(chat_id, min_confidence=conf)
        await update.message.reply_text(f"✅ Минимальный confidence установлен: {conf}%")
    except ValueError:
        await update.message.reply_text("❌ Укажите число от 50 до 95")


async def setschedule_day_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /setschedule_day command - set day schedule for user."""
    if not _bot_instance:
        return
    
    chat_id = str(update.effective_chat.id)
    _bot_instance.telegram_service.update_user_settings(chat_id, schedule_start="09:00", schedule_end="21:00")
    await update.message.reply_text("🌅 Расписание установлено: 09:00 - 21:00")


async def setschedule_night_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /setschedule_night command - set night schedule for user."""
    if not _bot_instance:
        return
    
    chat_id = str(update.effective_chat.id)
    _bot_instance.telegram_service.update_user_settings(chat_id, schedule_start="21:00", schedule_end="09:00")
    await update.message.reply_text("🌙 Расписание установлено: 21:00 - 09:00")


async def setschedule_always_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /setschedule_always command - set 24/7 schedule for user."""
    if not _bot_instance:
        return
    
    chat_id = str(update.effective_chat.id)
    _bot_instance.telegram_service.update_user_settings(chat_id, schedule_start=None, schedule_end=None)
    await update.message.reply_text("⚡ Расписание отключено. Сигналы 24/7")
