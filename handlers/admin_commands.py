"""
Admin command handlers for Crypto Signal Bot.
Handles user management, subscriptions, and admin-only operations.
"""

from telegram import Update
from telegram.ext import ContextTypes

from utils.helpers import logger


# Global bot instance reference (set from main.py)
_bot_instance = None


def set_bot_instance(bot):
    """Set the global bot instance reference."""
    global _bot_instance
    _bot_instance = bot


def is_admin(update: Update) -> bool:
    """Check if the user is admin."""
    if not _bot_instance:
        return False
    return str(update.effective_chat.id) == str(_bot_instance.telegram_service.chat_id)


async def add_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add_user command - add new subscriber (admin only).
    
    Usage: /add_user <chat_id|@username> [period]
    Periods: 2 (trial), 30 (1 month), 90 (3 months), 180 (6 months)
    """
    if not _bot_instance:
        return
    
    if not is_admin(update):
        await update.message.reply_text("⛔ У вас нет прав для этой команды")
        return
    
    if not context.args:
        await update.message.reply_text(
            "Использование: /add_user <chat_id|@username> [период]\n\n"
            "Периоды:\n"
            "• 2 - 2 дня (пробный)\n"
            "• 30 - 1 месяц\n"
            "• 90 - 3 месяца\n"
            "• 180 - 6 месяцев\n\n"
            "Примеры:\n"
            "/add_user 123456789 30\n"
            "/add_user @username 30"
        )
        return
    
    user_input = context.args[0]
    
    # Get period (default 2 days for trial)
    days = 2
    if len(context.args) >= 2:
        try:
            days = int(context.args[1])
            if days not in [2, 30, 90, 180]:
                await update.message.reply_text("❌ Неверный период. Используйте: 2, 30, 90 или 180")
                return
        except ValueError:
            await update.message.reply_text("❌ Период должен быть числом: 2, 30, 90 или 180")
            return
    
    service = _bot_instance.telegram_service
    
    # Add subscriber (handles both chat_id and @username)
    success, message = service.add_subscriber(user_input, days)
    
    if success:
        # Resolve to actual chat_id for notification
        if user_input.startswith('@'):
            chat_id = service.resolve_username(user_input)
        else:
            chat_id = user_input
        
        settings = service.get_user_settings(chat_id)
        period_name = service._get_period_name(days)
        
        await update.message.reply_text(
            f"✅ {message}\n"
            f"⏳ Действует до: {settings.subscription_expiry[:10]}"
        )
        
        # Notify user
        if chat_id:
            try:
                await service.bot.send_message(
                    chat_id=chat_id,
                    text=f"🎉 Добро пожаловать!\n\n"
                         f"Вам предоставлен доступ к сигналам на {period_name}.\n"
                         f"⏳ Подписка действует до: {settings.subscription_expiry[:10]}\n\n"
                         f"Используйте /mysettings для настройки\n"
                         f"и /toggle для включения сигналов.",
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.error(f"Failed to notify new user {chat_id}: {e}")
    else:
        await update.message.reply_text(f"❌ {message}")


async def remove_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /remove_user command - remove subscriber (admin only)."""
    if not _bot_instance:
        return
    
    if not is_admin(update):
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
    """Handle /list_users command - list all subscribers with expiry info (admin only)."""
    if not _bot_instance:
        return
    
    if not is_admin(update):
        await update.message.reply_text("⛔ У вас нет прав для этой команды")
        return
    
    service = _bot_instance.telegram_service
    subscribers = service.subscribers
    count = len(subscribers)
    
    if count == 0:
        await update.message.reply_text("📋 Список подписчиков пуст")
    else:
        lines = []
        for uid in subscribers:
            settings = service.get_user_settings(uid)
            days_left = settings.get_days_remaining()
            if days_left is not None:
                if days_left == 0:
                    status = "🔴 истекает сегодня"
                elif days_left <= 3:
                    status = f"🟡 {days_left} дн."
                else:
                    status = f"🟢 {days_left} дн."
                expiry = settings.subscription_expiry[:10] if settings.subscription_expiry else "?"
                lines.append(f"• {uid} | {status} | до {expiry}")
            else:
                lines.append(f"• {uid} | ⚪ без срока")
        
        users_list = "\n".join(lines)
        await update.message.reply_text(f"📋 Подписчики ({count}):\n{users_list}")


async def extend_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /extend command - extend subscription (admin only)."""
    if not _bot_instance:
        return
    
    if not is_admin(update):
        await update.message.reply_text("⛔ У вас нет прав для этой команды")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "Использование: /extend <chat_id|@username> <дни>\n\n"
            "Примеры:\n"
            "/extend 123456789 30\n"
            "/extend @username 30"
        )
        return
    
    user_input = context.args[0]
    
    # Resolve username if provided
    service = _bot_instance.telegram_service
    if user_input.startswith('@'):
        chat_id = service.resolve_username(user_input)
        if not chat_id:
            await update.message.reply_text(f"❌ Пользователь {user_input} не найден. Сначала попросите его написать боту.")
            return
    else:
        chat_id = user_input
    
    try:
        days = int(context.args[1])
        if days not in [2, 30, 90, 180]:
            await update.message.reply_text("❌ Неверный период. Используйте: 2, 30, 90 или 180")
            return
    except ValueError:
        await update.message.reply_text("❌ Дни должны быть числом")
        return
    
    if chat_id not in service.subscribers:
        await update.message.reply_text(f"❌ Пользователь {user_input} не найден в подписчиках")
        return
    
    if service.extend_subscription(chat_id, days):
        settings = service.get_user_settings(chat_id)
        period_name = service._get_period_name(days)
        await update.message.reply_text(
            f"✅ Подписка продлена для {user_input}\n"
            f"📅 Добавлено: {period_name}\n"
            f"⏳ Новый срок: {settings.subscription_expiry[:10]}"
        )
        
        # Notify user
        try:
            await service.bot.send_message(
                chat_id=chat_id,
                text=f"🔄 Ваша подписка продлена!\n\n"
                     f"📅 Добавлено: {period_name}\n"
                     f"⏳ Новый срок: {settings.subscription_expiry[:10]}\n\n"
                     f"Спасибо за продолжение работы с нами! 🚀",
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Failed to notify user {chat_id} about extension: {e}")
    else:
        await update.message.reply_text(f"❌ Ошибка при продлении подписки")


async def user_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /user_info command - show detailed user info (admin only)."""
    if not _bot_instance:
        return
    
    if not is_admin(update):
        await update.message.reply_text("⛔ У вас нет прав для этой команды")
        return
    
    if not context.args:
        await update.message.reply_text("Использование: /user_info <chat_id>")
        return
    
    chat_id = context.args[0]
    service = _bot_instance.telegram_service
    
    if chat_id not in service.subscribers:
        await update.message.reply_text(f"❌ Пользователь {chat_id} не найден")
        return
    
    settings = service.get_user_settings(chat_id)
    days_left = settings.get_days_remaining()
    
    status = "🟢 Активен" if settings.signals_enabled else "🔴 Отключен"
    schedule = f"{settings.schedule_start}-{settings.schedule_end}" if settings.schedule_start else "24/7"
    
    message = f"""👤 <b>Пользователь:</b> <code>{chat_id}</code>

📊 <b>Статус:</b> {status}
🎯 <b>Min confidence:</b> {settings.min_confidence}%
📅 <b>Расписание:</b> {schedule}

⏳ <b>Подписка:</b>
• Добавлен: {settings.added_date[:10] if settings.added_date else '?'}
• Истекает: {settings.subscription_expiry[:10] if settings.subscription_expiry else '?'}
• Осталось: {days_left if days_left is not None else '?'} дней
"""
    await update.message.reply_text(message, parse_mode='HTML')
