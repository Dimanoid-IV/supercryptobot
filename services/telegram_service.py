"""
Telegram service for sending trading signals.
Handles all Telegram bot interactions and message formatting.
"""

import json
import os
import subprocess
from typing import Optional, Callable, List
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


@dataclass
class UserSettings:
    """Per-user settings for signal filtering."""
    chat_id: str
    signals_enabled: bool = True
    min_confidence: int = 75  # Default from config
    schedule_start: Optional[str] = None  # "09:00" format or None for 24/7
    schedule_end: Optional[str] = None    # "21:00" format or None for 24/7
    max_signals_per_day: int = 1000  # Default high limit
    subscription_expiry: Optional[str] = None  # ISO format date or None
    added_date: Optional[str] = None  # ISO format date when user was added
    username: Optional[str] = None  # Telegram username for reference
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            'chat_id': self.chat_id,
            'signals_enabled': self.signals_enabled,
            'min_confidence': self.min_confidence,
            'schedule_start': self.schedule_start,
            'schedule_end': self.schedule_end,
            'max_signals_per_day': self.max_signals_per_day,
            'subscription_expiry': self.subscription_expiry,
            'added_date': self.added_date,
            'username': self.username
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'UserSettings':
        """Create UserSettings from dictionary."""
        return cls(
            chat_id=data.get('chat_id', ''),
            signals_enabled=data.get('signals_enabled', True),
            min_confidence=data.get('min_confidence', 75),
            schedule_start=data.get('schedule_start'),
            schedule_end=data.get('schedule_end'),
            max_signals_per_day=data.get('max_signals_per_day', 1000),
            subscription_expiry=data.get('subscription_expiry'),
            added_date=data.get('added_date'),
            username=data.get('username')
        )
    
    def is_signals_allowed_now(self) -> bool:
        """Check if signals are allowed based on schedule and enabled status."""
        if not self.signals_enabled:
            return False
        
        # Check subscription expiry
        if self.subscription_expiry:
            try:
                expiry = datetime.fromisoformat(self.subscription_expiry)
                if datetime.now() > expiry:
                    return False  # Subscription expired
            except Exception:
                pass
        
        # No schedule set - always allowed
        if self.schedule_start is None or self.schedule_end is None:
            return True
        
        try:
            now = datetime.now().time()
            start_hour, start_min = map(int, self.schedule_start.split(':'))
            end_hour, end_min = map(int, self.schedule_end.split(':'))
            start_time = time(start_hour, start_min)
            end_time = time(end_hour, end_min)
            
            if start_time <= end_time:
                return start_time <= now <= end_time
            else:
                return now >= start_time or now <= end_time
        except Exception:
            return True  # Allow on error
    
    def get_days_remaining(self) -> Optional[int]:
        """Get number of days remaining in subscription."""
        if not self.subscription_expiry:
            return None
        try:
            expiry = datetime.fromisoformat(self.subscription_expiry)
            remaining = expiry - datetime.now()
            return max(0, remaining.days)
        except Exception:
            return None
    
    def is_expired(self) -> bool:
        """Check if subscription has expired."""
        if not self.subscription_expiry:
            return False
        try:
            expiry = datetime.fromisoformat(self.subscription_expiry)
            return datetime.now() > expiry
        except Exception:
            return False


class TelegramService:
    """Service class for Telegram notifications."""
    
    def __init__(self):
        """Initialize Telegram bot."""
        self.bot = Bot(token=config.TELEGRAM_TOKEN)
        self.chat_id = config.TELEGRAM_CHAT_ID
        
        # Load subscribed users from file
        self.subscribers_file = "subscribers.json"
        self.subscribers = self._load_subscribers()
        
        # Load per-user settings
        self.user_settings: dict[str, UserSettings] = self._load_user_settings()
        
        # Username to chat_id mapping (for adding users by @username)
        self.username_to_chat_id: dict[str, str] = self._load_username_mapping()
        
        # Pending trial requests (users who requested trial but not yet approved)
        self.pending_requests: dict[str, dict] = self._load_pending_requests()
        
        # All users who ever interacted with bot (for tracking)
        self.all_users: dict[str, dict] = self._load_all_users()
        
        # Signal control settings (admin/global)
        self.signals_enabled = True
        self.auto_start_time: Optional[time] = None  # e.g., time(9, 0) for 09:00
        self.auto_stop_time: Optional[time] = None   # e.g., time(21, 0) for 21:00
        
        # Callback for settings change
        self.on_settings_change: Optional[Callable] = None
        
        logger.info(f"TelegramService initialized with {len(self.subscribers)} subscribers, {len(self.pending_requests)} pending requests")
    
    def _load_username_mapping(self) -> dict[str, str]:
        """Load username to chat_id mapping from file."""
        if os.path.exists(self.subscribers_file):
            try:
                with open(self.subscribers_file, 'r') as f:
                    data = json.load(f)
                    return data.get('username_mapping', {})
            except Exception as e:
                logger.error(f"Failed to load username mapping: {e}")
        return {}
    
    def _save_username_mapping(self):
        """Save username to chat_id mapping to file."""
        try:
            data = {}
            if os.path.exists(self.subscribers_file):
                with open(self.subscribers_file, 'r') as f:
                    data = json.load(f)
            
            data['username_mapping'] = self.username_to_chat_id
            
            with open(self.subscribers_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save username mapping: {e}")
    
    def register_username(self, username: str, chat_id: str, first_name: str = None):
        """Register username to chat_id mapping when user interacts with bot."""
        chat_id_str = str(chat_id)
        username_clean = username.lstrip('@').lower() if username else None
        
        # Add to all_users (everyone who ever interacted with bot)
        if chat_id_str not in self.all_users:
            self.all_users[chat_id_str] = {
                'chat_id': chat_id_str,
                'username': username_clean,
                'first_name': first_name,
                'first_seen': datetime.now().isoformat()
            }
            self._save_all_users()
            logger.info(f"New user added to all_users: {username_clean or chat_id_str}")
        
        if username:
            self.username_to_chat_id[username_clean] = chat_id_str
            self._save_username_mapping()
            
            # Also save username in user settings
            settings = self.get_user_settings(chat_id_str)
            settings.username = username_clean
            self.user_settings[chat_id_str] = settings
            self._save_subscribers()
            
            logger.info(f"Registered username @{username_clean} -> {chat_id_str}")
    
    def resolve_username(self, username: str) -> Optional[str]:
        """Resolve @username to chat_id."""
        username_clean = username.lstrip('@').lower()
        return self.username_to_chat_id.get(username_clean)
    
    def _load_pending_requests(self) -> dict[str, dict]:
        """Load pending trial requests from file."""
        if os.path.exists(self.subscribers_file):
            try:
                with open(self.subscribers_file, 'r') as f:
                    data = json.load(f)
                    return data.get('pending_requests', {})
            except Exception as e:
                logger.error(f"Failed to load pending requests: {e}")
        return {}
    
    def _save_pending_requests(self):
        """Save pending trial requests to file."""
        try:
            data = {}
            if os.path.exists(self.subscribers_file):
                with open(self.subscribers_file, 'r') as f:
                    data = json.load(f)
            
            data['pending_requests'] = self.pending_requests
            
            with open(self.subscribers_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save pending requests: {e}")
    
    def _load_all_users(self) -> dict[str, dict]:
        """Load all users who ever interacted with bot."""
        if os.path.exists(self.subscribers_file):
            try:
                with open(self.subscribers_file, 'r') as f:
                    data = json.load(f)
                    return data.get('all_users', {})
            except Exception as e:
                logger.error(f"Failed to load all users: {e}")
        return {}
    
    def _save_all_users(self):
        """Save all users to file."""
        try:
            data = {}
            if os.path.exists(self.subscribers_file):
                with open(self.subscribers_file, 'r') as f:
                    data = json.load(f)
            
            data['all_users'] = self.all_users
            
            with open(self.subscribers_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save all users: {e}")
    
    def add_pending_request(self, chat_id: str, username: Optional[str], first_name: Optional[str]):
        """Add a new trial request."""
        from datetime import datetime
        
        chat_id_str = str(chat_id)
        self.pending_requests[chat_id_str] = {
            'chat_id': chat_id_str,
            'username': username,
            'first_name': first_name,
            'requested_at': datetime.now().isoformat(),
            'status': 'pending'
        }
        
        # Also add to all users
        self.all_users[chat_id_str] = {
            'chat_id': chat_id_str,
            'username': username,
            'first_name': first_name,
            'first_seen': datetime.now().isoformat()
        }
        
        self._save_pending_requests()
        self._save_all_users()
        logger.info(f"New trial request from {username or chat_id}")
    
    def approve_request(self, chat_id: str, days: int = 2) -> tuple[bool, str]:
        """Approve a pending request and add user as subscriber."""
        chat_id_str = str(chat_id)
        
        if chat_id_str not in self.pending_requests:
            return False, "Запрос не найден"
        
        # Add as subscriber
        success, message = self.add_subscriber(chat_id_str, days)
        
        if success:
            # Update request status
            self.pending_requests[chat_id_str]['status'] = 'approved'
            self.pending_requests[chat_id_str]['approved_at'] = datetime.now().isoformat()
            self._save_pending_requests()
            
            # Notify user
            try:
                self.bot.send_message(
                    chat_id=chat_id_str,
                    text=f"✅ <b>Ваш пробный период активирован!</b>\n\nВы будете получать сигналы в течение {days} дней.\n\nИспользуйте /mysettings для настройки.",
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.error(f"Failed to notify approved user: {e}")
        
        return success, message
    
    def reject_request(self, chat_id: str) -> bool:
        """Reject a pending request."""
        chat_id_str = str(chat_id)
        
        if chat_id_str not in self.pending_requests:
            return False
        
        self.pending_requests[chat_id_str]['status'] = 'rejected'
        self.pending_requests[chat_id_str]['rejected_at'] = datetime.now().isoformat()
        self._save_pending_requests()
        
        # Notify user
        try:
            self.bot.send_message(
                chat_id=chat_id_str,
                text="❌ <b>К сожалению, ваш запрос отклонён.</b>\n\nОбратитесь в поддержку для уточнения причин.",
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Failed to notify rejected user: {e}")
        
        return True
    
    def get_pending_requests(self) -> list[dict]:
        """Get all pending requests."""
        return [
            req for req in self.pending_requests.values()
            if req.get('status') == 'pending'
        ]
    
    def get_all_users(self) -> list[dict]:
        """Get all users who ever interacted with bot."""
        return list(self.all_users.values())
    
    def _load_subscribers(self) -> List[str]:
        """Load subscriber chat IDs from file."""
        if os.path.exists(self.subscribers_file):
            try:
                with open(self.subscribers_file, 'r') as f:
                    data = json.load(f)
                    return data.get('chat_ids', [])
            except Exception as e:
                logger.error(f"Failed to load subscribers: {e}")
        return []
    
    def _save_subscribers(self):
        """Save subscriber chat IDs to file and commit to git."""
        try:
            data = {
                'chat_ids': self.subscribers,
                'user_settings': {k: v.to_dict() for k, v in self.user_settings.items()}
            }
            with open(self.subscribers_file, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Subscribers saved: {len(self.subscribers)} total")
            
            # Auto-commit to git for persistence on Render
            self._auto_commit()
        except Exception as e:
            logger.error(f"Failed to save subscribers: {e}")
    
    def _auto_commit(self):
        """Auto-commit subscribers.json to git for persistence."""
        try:
            # Check if we're in a git repo
            result = subprocess.run(
                ['git', 'rev-parse', '--git-dir'],
                capture_output=True,
                text=True,
                cwd=os.path.dirname(os.path.abspath(__file__))
            )
            if result.returncode != 0:
                return  # Not a git repo
            
            # Check if file has changes
            result = subprocess.run(
                ['git', 'status', '--porcelain', self.subscribers_file],
                capture_output=True,
                text=True,
                cwd=os.path.dirname(os.path.abspath(__file__))
            )
            if not result.stdout.strip():
                return  # No changes to commit
            
            # Add, commit and push
            subprocess.run(
                ['git', 'add', self.subscribers_file],
                capture_output=True,
                cwd=os.path.dirname(os.path.abspath(__file__))
            )
            subprocess.run(
                ['git', 'commit', '-m', f'Auto-update subscribers: {len(self.subscribers)} users'],
                capture_output=True,
                cwd=os.path.dirname(os.path.abspath(__file__))
            )
            # Push to remote (for Render persistence)
            push_result = subprocess.run(
                ['git', 'push', 'origin', 'main'],
                capture_output=True,
                text=True,
                cwd=os.path.dirname(os.path.abspath(__file__))
            )
            if push_result.returncode == 0:
                logger.info(f"Auto-committed and pushed subscribers.json ({len(self.subscribers)} users)")
            else:
                logger.debug(f"Push skipped: {push_result.stderr}")
        except Exception as e:
            logger.debug(f"Auto-commit skipped: {e}")  # Debug level - not critical
    
    def _load_user_settings(self) -> dict[str, UserSettings]:
        """Load per-user settings from file."""
        if os.path.exists(self.subscribers_file):
            try:
                with open(self.subscribers_file, 'r') as f:
                    data = json.load(f)
                    settings_data = data.get('user_settings', {})
                    return {k: UserSettings.from_dict(v) for k, v in settings_data.items()}
            except Exception as e:
                logger.error(f"Failed to load user settings: {e}")
        return {}
    
    def get_user_settings(self, chat_id: str) -> UserSettings:
        """Get or create user settings for a chat_id."""
        chat_id_str = str(chat_id)
        if chat_id_str not in self.user_settings:
            self.user_settings[chat_id_str] = UserSettings(chat_id=chat_id_str)
            self._save_subscribers()
        return self.user_settings[chat_id_str]
    
    def update_user_settings(self, chat_id: str, **kwargs) -> bool:
        """Update user settings."""
        chat_id_str = str(chat_id)
        settings = self.get_user_settings(chat_id_str)
        
        for key, value in kwargs.items():
            if hasattr(settings, key):
                setattr(settings, key, value)
        
        self.user_settings[chat_id_str] = settings
        self._save_subscribers()
        logger.info(f"Updated settings for user {chat_id_str}: {kwargs}")
        return True
    
    def add_subscriber(self, chat_id: str, days: int = 2) -> tuple[bool, str]:
        """Add a new subscriber with subscription period.
        
        Args:
            chat_id: User's chat ID or @username
            days: Subscription duration in days (default 2 for trial)
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        from datetime import timedelta
        
        # Resolve username if provided
        original_input = chat_id
        if chat_id.startswith('@'):
            resolved = self.resolve_username(chat_id)
            if not resolved:
                return False, f"Пользователь {chat_id} не найден. Сначала попросите его написать боту."
            chat_id = resolved
        
        chat_id_str = str(chat_id)
        is_new = chat_id_str not in self.subscribers
        
        if is_new:
            self.subscribers.append(chat_id_str)
        
        # Calculate expiry date
        added_date = datetime.now()
        expiry_date = added_date + timedelta(days=days)
        
        # Get or create user settings
        settings = self.get_user_settings(chat_id_str)
        settings.added_date = added_date.isoformat()
        settings.subscription_expiry = expiry_date.isoformat()
        settings.signals_enabled = True
        
        self.user_settings[chat_id_str] = settings
        self._save_subscribers()
        
        period_name = self._get_period_name(days)
        if is_new:
            logger.info(f"New subscriber added: {chat_id_str} for {period_name}")
            return True, f"Пользователь {original_input} добавлен на {period_name}"
        else:
            logger.info(f"Subscriber {chat_id_str} subscription updated to {period_name}")
            return True, f"Подписка обновлена для {original_input} на {period_name}"
    
    def _get_period_name(self, days: int) -> str:
        """Get human-readable period name."""
        if days == 2:
            return "2 дня (пробный период)"
        elif days == 30:
            return "1 месяц"
        elif days == 90:
            return "3 месяца"
        elif days == 180:
            return "6 месяцев"
        else:
            return f"{days} дней"
    
    def extend_subscription(self, chat_id: str, days: int) -> tuple[bool, str]:
        """Extend subscriber's subscription. Returns (success, message)."""
        from datetime import timedelta
        
        chat_id_str = str(chat_id)
        if chat_id_str not in self.subscribers:
            return False, "Пользователь не найден"
        
        settings = self.get_user_settings(chat_id_str)
        
        # If already has expiry, extend from that date, otherwise from now
        if settings.subscription_expiry and not settings.is_expired():
            current_expiry = datetime.fromisoformat(settings.subscription_expiry)
            new_expiry = current_expiry + timedelta(days=days)
        else:
            new_expiry = datetime.now() + timedelta(days=days)
        
        settings.subscription_expiry = new_expiry.isoformat()
        settings.signals_enabled = True
        self.user_settings[chat_id_str] = settings
        self._save_subscribers()
        
        logger.info(f"Extended subscription for {chat_id_str} by {days} days")
        return True, f"Подписка продлена до {new_expiry.strftime('%Y-%m-%d')}"
    
    def get_expired_subscribers(self) -> list[str]:
        """Get list of expired subscribers."""
        expired = []
        for chat_id in self.subscribers:
            settings = self.get_user_settings(chat_id)
            if settings.is_expired():
                expired.append(chat_id)
        return expired
    
    def remove_expired_subscribers(self) -> list[str]:
        """Remove expired subscribers and return their chat_ids."""
        expired = self.get_expired_subscribers()
        for chat_id in expired:
            self.subscribers.remove(chat_id)
            if chat_id in self.user_settings:
                del self.user_settings[chat_id]
            logger.info(f"Removed expired subscriber: {chat_id}")
        
        if expired:
            self._save_subscribers()
        return expired
    
    async def check_expiring_subscriptions(self, days_before: int = 3) -> tuple[list[str], list[str]]:
        """Check for subscriptions expiring soon and already expired.
        
        Returns:
            Tuple of (expiring_soon_list, just_expired_list)
        """
        from datetime import timedelta
        
        expiring_soon = []
        just_expired = []
        now = datetime.now()
        
        for chat_id in list(self.subscribers):
            settings = self.get_user_settings(chat_id)
            if not settings.subscription_expiry:
                continue
            
            try:
                expiry = datetime.fromisoformat(settings.subscription_expiry)
                days_until = (expiry - now).days
                
                # Just expired (within last 24 hours)
                if days_until < 0 and days_until >= -1:
                    just_expired.append(chat_id)
                # Expiring soon
                elif 0 <= days_until <= days_before:
                    expiring_soon.append((chat_id, days_until))
                    
            except Exception as e:
                logger.error(f"Error checking expiry for {chat_id}: {e}")
        
        return expiring_soon, just_expired
    
    async def notify_expiring_users(self, expiring_soon: list[tuple[str, int]]):
        """Send expiry warning notifications to users with payment options."""
        from config import config
        
        # Build payment message based on available payment methods
        payment_options = []
        
        if config.STRIPE_PAYMENT_LINK:
            payment_options.append(f"💳 <b>Карта:</b> {config.STRIPE_PAYMENT_LINK}")
        
        if config.CRYPTO_WALLET_USDT:
            payment_options.append(
                f"💎 <b>USDT {config.CRYPTO_NETWORK}:</b>\n"
                f"<code>{config.CRYPTO_WALLET_USDT}</code>"
            )
        
        payment_text = "\n".join(payment_options) if payment_options else "Свяжитесь с администратором для оплаты."
        
        # Build support contact text
        support_text = ""
        if config.SUPPORT_USERNAME:
            support_text = f"\n\n💬 <b>Поддержка:</b> @{config.SUPPORT_USERNAME}"
        
        for chat_id, days_left in expiring_soon:
            try:
                if days_left == 0:
                    message = f"""⏰ <b>Ваша подписка истекает СЕГОДНЯ!</b>

Не пропустите сигналы - продлите прямо сейчас:

{payment_text}

<b>📋 Периоды:</b>
• 1 месяц
• 3 месяца
• 6 месяцев

После оплаты отправьте скриншот администратору.{support_text}

Используйте /mysettings для проверки статуса."""
                else:
                    message = f"""⏰ <b>Ваша подписка истекает через {days_left} дн.!</b>

Продлите заранее, чтобы не прерывать получение сигналов:

{payment_text}

<b>📋 Периоды:</b>
• 1 месяц
• 3 месяца
• 6 месяцев

После оплаты отправьте скриншот администратору.{support_text}"""
                
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode=ParseMode.HTML
                )
                logger.info(f"Sent expiry warning with payment options to {chat_id} ({days_left} days left)")
            except Exception as e:
                logger.error(f"Failed to notify expiring user {chat_id}: {e}")
    
    async def notify_expired_users(self, expired: list[str]):
        """Send expiry notification to users whose subscription just expired with payment options."""
        from config import config
        
        # Build payment message based on available payment methods
        payment_options = []
        
        if config.STRIPE_PAYMENT_LINK:
            payment_options.append(f"💳 <b>Оплата картой:</b> {config.STRIPE_PAYMENT_LINK}")
        
        if config.CRYPTO_WALLET_USDT:
            payment_options.append(
                f"💎 <b>Криптовалюта (USDT {config.CRYPTO_NETWORK}):</b>\n"
                f"<code>{config.CRYPTO_WALLET_USDT}</code>\n"
                f"(нажмите чтобы скопировать)"
            )
        
        payment_text = "\n\n".join(payment_options) if payment_options else "Свяжитесь с администратором для оплаты."
        
        # Build support contact text
        support_text = ""
        if config.SUPPORT_USERNAME:
            support_text = f"\n\n💬 <b>Поддержка:</b> @{config.SUPPORT_USERNAME}"
        
        for chat_id in expired:
            try:
                message = f"""🔴 <b>Ваша подписка истекла</b>

Вы больше не будете получать сигналы.

<b>💎 Продлите подписку:</b>

{payment_text}

<b>📋 Доступные периоды:</b>
• 1 месяц
• 3 месяца
• 6 месяцев

После оплаты отправьте скриншот администратору для активации.{support_text}

Спасибо за использование нашего бота! 🙏"""
                
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode=ParseMode.HTML
                )
                logger.info(f"Sent expiry notification with payment options to {chat_id}")
            except Exception as e:
                logger.error(f"Failed to notify expired user {chat_id}: {e}")
    
    async def notify_admin_about_expired(self, expired: list[str], expiring_soon: list[tuple[str, int]]):
        """Notify admin about expired and expiring subscriptions."""
        if not self.chat_id:
            return
        
        messages = []
        
        if expired:
            expired_list = "\n".join([f"• {uid}" for uid in expired])
            messages.append(f"🔴 <b>Истекли подписки ({len(expired)}):</b>\n{expired_list}")
        
        if expiring_soon:
            expiring_list = "\n".join([f"• {uid} ({days} дн.)" for uid, days in expiring_soon])
            messages.append(f"🟡 <b>Истекают скоро ({len(expiring_soon)}):</b>\n{expiring_list}")
        
        if messages:
            try:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text="📊 <b>Отчет по подпискам:</b>\n\n" + "\n\n".join(messages),
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Failed to notify admin about expirations: {e}")
    
    def remove_subscriber(self, chat_id: str) -> bool:
        """Remove a subscriber."""
        chat_id_str = str(chat_id)
        if chat_id_str in self.subscribers:
            self.subscribers.remove(chat_id_str)
            if chat_id_str in self.user_settings:
                del self.user_settings[chat_id_str]
            self._save_subscribers()
            logger.info(f"Subscriber removed: {chat_id_str}")
            return True
        return False
    
    def get_subscribers_count(self) -> int:
        """Get number of subscribers."""
        return len(self.subscribers)
    
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
        Send a trading signal to all subscribers respecting their individual settings.
        
        Args:
            signal: SignalMessage object
            
        Returns:
            True if sent successfully to at least one subscriber
        """
        try:
            message = self._format_signal_message(signal)
            
            # Send to main admin chat (always, no filtering)
            success_count = 0
            
            if self.chat_id:
                try:
                    await self.bot.send_message(
                        chat_id=self.chat_id,
                        text=message,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True
                    )
                    success_count += 1
                except Exception as e:
                    logger.error(f"Failed to send to main chat: {e}")
            
            # Send to subscribers respecting their individual settings
            for chat_id in self.subscribers:
                try:
                    # Get user settings
                    settings = self.get_user_settings(chat_id)
                    
                    # Check if signals are enabled for this user
                    if not settings.signals_enabled:
                        continue
                    
                    # Check confidence threshold
                    if signal.confidence < settings.min_confidence:
                        continue
                    
                    # Check schedule
                    if not settings.is_signals_allowed_now():
                        continue
                    
                    # Send signal to this user
                    await self.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True
                    )
                    success_count += 1
                    
                except Exception as e:
                    logger.error(f"Failed to send to subscriber {chat_id}: {e}")
            
            logger.info(f"Signal sent to {success_count} recipients for {signal.pair} {signal.direction}")
            return success_count > 0
            
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
• /op - Показать меню управления
• /status - Текущий статус
• /on - Включить сигналы
• /off - Выключить сигналы
• /schedule_day - День (09:00-21:00)
• /schedule_night - Ночь (21:00-09:00)
• /schedule_always - 24/7
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
