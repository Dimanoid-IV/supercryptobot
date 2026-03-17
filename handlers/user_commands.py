"""
User command handlers for Crypto Signal Bot.
Handles user settings and preferences.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import config
from utils.helpers import logger


# Global bot instance reference (set from main.py)
_bot_instance = None


def set_bot_instance(bot):
    """Set the global bot instance reference."""
    global _bot_instance
    _bot_instance = bot


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command with welcome message and trial offer."""
    if not _bot_instance:
        return
    
    user = update.effective_user
    chat_id = str(update.effective_chat.id)
    
    # Register username for future reference and add to all_users
    if user:
        _bot_instance.telegram_service.register_username(
            user.username, chat_id, user.first_name
        )
        logger.info(f"New user started bot: @{user.username or 'no_username'} ({chat_id})")
    else:
        logger.info(f"New user started bot: {chat_id} (no user info)")
    
    # Check if user already has subscription
    service = _bot_instance.telegram_service
    is_subscribed = chat_id in service.subscribers
    
    if is_subscribed:
        # Existing subscriber - show control menu
        await service.send_control_menu()
    else:
        # New user - show welcome message with trial offer
        welcome_text = f"""🎉 <b>Добро пожаловать в Crypto Signal Bot!</b>

Привет, {user.first_name if user else 'друг'}!

Я отправляю торговые сигналы на основе технического анализа:
• Трендовые линии и EMA
• Объемы и волатильность
• Open Interest и Funding Rate
• Risk/Reward минимум 1:2

<b>🎁 Пробный период: 2 дня БЕСПЛАТНО</b>

Чтобы получить доступ:
1. Нажмите кнопку ниже
2. Администратор активирует вашу подписку
3. Начните получать сигналы!

<i>После пробного периода доступны подписки:
• 1 месяц
• 3 месяца  
• 6 месяцев</i>
"""
        
        # Create keyboard with request button
        keyboard_buttons = [
            [InlineKeyboardButton("🚀 Запросить пробный период", callback_data="request_trial")],
            [InlineKeyboardButton("📊 Подробнее о сигналах", callback_data="about_signals")]
        ]
        
        # Add support button if username is configured
        if config.SUPPORT_USERNAME:
            keyboard_buttons.append(
                [InlineKeyboardButton("💬 Связаться с поддержкой", url=f"https://t.me/{config.SUPPORT_USERNAME}")]
            )
        
        keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
        await update.message.reply_text(
            welcome_text,
            parse_mode='HTML',
            reply_markup=keyboard
        )
        
        # Notify admin about new user
        try:
            admin_msg = f"""📢 <b>Новый пользователь!</b>

👤 Имя: {user.first_name if user else 'N/A'}
🔹 Username: @{user.username if user and user.username else 'N/A'}
🆔 Chat ID: <code>{chat_id}</code>

Источник: {context.args[0] if context.args else 'прямой заход'}

Добавить пробный период:
<code>/add_user {chat_id} 2</code>
"""
            await service.bot.send_message(
                chat_id=service.chat_id,
                text=admin_msg,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Failed to notify admin about new user: {e}")


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


async def set_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /set command - show settings menu with buttons."""
    if not _bot_instance:
        return
    
    chat_id = str(update.effective_chat.id)
    settings = _bot_instance.telegram_service.get_user_settings(chat_id)
    
    # Create keyboard with confidence buttons
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{'✅ ' if settings.min_confidence == 75 else ''}75%", callback_data="setconf_75"),
         InlineKeyboardButton(f"{'✅ ' if settings.min_confidence == 80 else ''}80%", callback_data="setconf_80"),
         InlineKeyboardButton(f"{'✅ ' if settings.min_confidence == 85 else ''}85%", callback_data="setconf_85")],
        [InlineKeyboardButton(f"{'✅ ' if settings.min_confidence == 90 else ''}90%", callback_data="setconf_90"),
         InlineKeyboardButton(f"{'✅ ' if settings.min_confidence == 95 else ''}95%", callback_data="setconf_95")],
        [InlineKeyboardButton("🔙 Назад", callback_data="mysettings")]
    ])
    
    message = f"""⚙️ <b>Настройка минимального confidence</b>

Текущее значение: <b>{settings.min_confidence}%</b>

Выберите новое значение:
• 75% - Больше сигналов, меньше точность
• 80% - Баланс (рекомендуется)
• 85% - Меньше сигналов, выше точность
• 90% - Только лучшие сигналы
• 95% - Очень редкие, но качественные

<i>Чем выше confidence, тем меньше сигналов вы получите.</i>"""
    
    await update.message.reply_text(message, parse_mode='HTML', reply_markup=keyboard)


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
