"""
Web admin panel for Crypto Signal Bot.
Provides user management interface via Flask.
"""

import os
import json
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session


def create_app(telegram_service=None, config=None):
    """Create and configure Flask app."""
    app = Flask(__name__, 
                template_folder='templates',
                static_folder='static')
    
    # Secret key for sessions
    app.secret_key = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key-change-in-production')
    
    # Admin password from environment
    admin_password = os.getenv('ADMIN_PASSWORD', 'admin123')
    
    # Reference to telegram service for operations
    app.telegram_service = telegram_service
    app.config_obj = config
    
    def login_required(f):
        """Decorator to require login."""
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'logged_in' not in session:
                return redirect(url_for('login'))
            return f(*args, **kwargs)
        return decorated_function
    
    @app.route('/')
    def index():
        """Redirect to admin panel."""
        return redirect(url_for('admin'))
    
    @app.route('/login', methods=['GET', 'POST'])
    def login():
        """Login page."""
        if request.method == 'POST':
            password = request.form.get('password', '')
            if password == admin_password:
                session['logged_in'] = True
                return redirect(url_for('admin'))
            else:
                flash('Неверный пароль', 'error')
        return render_template('login.html')
    
    @app.route('/logout')
    def logout():
        """Logout."""
        session.pop('logged_in', None)
        return redirect(url_for('login'))
    
    @app.route('/admin')
    @login_required
    def admin():
        """Main admin panel."""
        users = get_users_data()
        stats = calculate_stats(users)
        return render_template('admin.html', users=users, stats=stats)
    
    @app.route('/admin/add_user', methods=['POST'])
    @login_required
    def add_user():
        """Add new user."""
        user_id = request.form.get('user_id', '').strip()
        days = int(request.form.get('days', 2))
        
        if not user_id:
            flash('ID пользователя обязателен', 'error')
            return redirect(url_for('admin'))
        
        try:
            if app.telegram_service:
                success, message = app.telegram_service.add_subscriber(user_id, days)
                if success:
                    flash(f'Пользователь {user_id} добавлен на {days} дн.', 'success')
                else:
                    flash(message, 'error')
            else:
                flash('Сервис недоступен', 'error')
        except Exception as e:
            flash(f'Ошибка: {str(e)}', 'error')
        
        return redirect(url_for('admin'))
    
    @app.route('/admin/extend', methods=['POST'])
    @login_required
    def extend_subscription():
        """Extend user subscription."""
        user_id = request.form.get('user_id', '').strip()
        days = int(request.form.get('days', 30))
        
        if not user_id:
            flash('ID пользователя обязателен', 'error')
            return redirect(url_for('admin'))
        
        try:
            if app.telegram_service:
                success, message = app.telegram_service.extend_subscription(user_id, days)
                if success:
                    flash(f'Подписка продлена на {days} дн.', 'success')
                else:
                    flash(message, 'error')
            else:
                flash('Сервис недоступен', 'error')
        except Exception as e:
            flash(f'Ошибка: {str(e)}', 'error')
        
        return redirect(url_for('admin'))
    
    @app.route('/admin/remove', methods=['POST'])
    @login_required
    def remove_user():
        """Remove user."""
        user_id = request.form.get('user_id', '').strip()
        
        if not user_id:
            flash('ID пользователя обязателен', 'error')
            return redirect(url_for('admin'))
        
        try:
            if app.telegram_service:
                success = app.telegram_service.remove_subscriber(user_id)
                if success:
                    flash(f'Пользователь {user_id} удален', 'success')
                else:
                    flash('Пользователь не найден', 'error')
            else:
                flash('Сервис недоступен', 'error')
        except Exception as e:
            flash(f'Ошибка: {str(e)}', 'error')
        
        return redirect(url_for('admin'))
    
    @app.route('/admin/user/<user_id>')
    @login_required
    def user_detail(user_id):
        """User detail page."""
        user = get_user_data(user_id)
        if not user:
            flash('Пользователь не найден', 'error')
            return redirect(url_for('admin'))
        return render_template('user_detail.html', user=user)
    
    def get_users_data():
        """Get all users data from telegram service."""
        users = []

        if not app.telegram_service:
            return users

        try:
            for chat_id in app.telegram_service.subscribers:
                settings = app.telegram_service.user_settings.get(chat_id)
                if not settings:
                    continue

                expiry = settings.subscription_expiry

                # Calculate days left
                days_left = None
                status = 'active'
                if expiry:
                    expiry_date = datetime.fromisoformat(expiry)
                    days_left = (expiry_date - datetime.now()).days
                    if days_left < 0:
                        status = 'expired'
                    elif days_left <= 3:
                        status = 'expiring'

                users.append({
                    'chat_id': chat_id,
                    'username': getattr(settings, 'username', 'N/A'),
                    'added_date': settings.added_date or 'N/A',
                    'expiry': expiry or 'N/A',
                    'days_left': days_left,
                    'status': status,
                    'signals_enabled': settings.signals_enabled,
                    'min_confidence': settings.min_confidence
                })
        except Exception as e:
            print(f"Error loading users: {e}")

        # Sort by status (expired first, then expiring, then active)
        status_order = {'expired': 0, 'expiring': 1, 'active': 2}
        users.sort(key=lambda x: (status_order.get(x['status'], 3), x.get('days_left') or 999))

        return users
    
    def get_user_data(user_id):
        """Get single user data."""
        if not app.telegram_service:
            return None

        try:
            settings = app.telegram_service.user_settings.get(user_id)
            if not settings:
                return None

            expiry = settings.subscription_expiry
            days_left = None
            if expiry:
                expiry_date = datetime.fromisoformat(expiry)
                days_left = (expiry_date - datetime.now()).days

            return {
                'chat_id': user_id,
                'username': getattr(settings, 'username', 'N/A'),
                'added_date': settings.added_date or 'N/A',
                'expiry': expiry or 'N/A',
                'days_left': days_left,
                'signals_enabled': settings.signals_enabled,
                'min_confidence': settings.min_confidence,
                'schedule_start': settings.schedule_start,
                'schedule_end': settings.schedule_end
            }
        except Exception as e:
            print(f"Error loading user: {e}")
            return None
    
    def calculate_stats(users):
        """Calculate statistics."""
        total = len(users)
        active = sum(1 for u in users if u['status'] == 'active')
        expiring = sum(1 for u in users if u['status'] == 'expiring')
        expired = sum(1 for u in users if u['status'] == 'expired')
        
        return {
            'total': total,
            'active': active,
            'expiring': expiring,
            'expired': expired
        }
    
    return app
