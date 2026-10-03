import os
import asyncio
import sqlite3
import random
from urllib.parse import urlparse
import logging
import time
import aiohttp
import urllib.parse
import json
import re
import socket
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Tuple, Dict, Any

from aiogram import Bot, Dispatcher, F, BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message, Dice, BufferedInputFile, ErrorEvent
)

# ==============================================================================
# 1. BOT CONFIGURATION & CONSTANTS
# ==============================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8316636621:AAEhVz2E_O3DTWfRZVGvmd1HClfZvlK8eMU")
BOT_USERNAME = os.getenv("BOT_USERNAME", "@OBITOx_STORE_bot")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8259869459") or 0)
SECOND_ADMIN_ID = int(os.getenv("SECOND_ADMIN_ID", "8259869459") or 0)
ADMIN_CONTACT = os.getenv("ADMIN_CONTACT", "")

VIP_DISCOUNT_PERCENTAGE = 15.0
VIP_PRICE_INR = 299.0

WELCOME_STICKER_ID = "CAACAgIAAxkBAAEU-WZmH_..."  # Replace with your sticker ID
SPIN_DELAY_SECONDS = 2.5

# Per-user purchase gate: rejects rapid duplicate taps instead of queueing them.
_PURCHASE_LOCKS = defaultdict(asyncio.Lock)
_API_HTTP_SESSION = None
_PURCHASE_COOLDOWN_UNTIL = {}

async def _purchase_gate(user_id: int):
    """Acquire a non-queueing per-user purchase lock. Returns None when busy."""
    user_id = int(user_id)
    now = time.monotonic()
    if _PURCHASE_COOLDOWN_UNTIL.get(user_id, 0.0) > now:
        return None
    lock = _PURCHASE_LOCKS[user_id]
    if lock.locked():
        return None
    await lock.acquire()
    # Re-check after acquisition in case another task changed the cooldown.
    if _PURCHASE_COOLDOWN_UNTIL.get(user_id, 0.0) > time.monotonic():
        lock.release()
        return None
    return lock

def _set_purchase_cooldown(user_id: int, seconds: float = 4.0) -> None:
    _PURCHASE_COOLDOWN_UNTIL[int(user_id)] = time.monotonic() + seconds

async def _get_api_http_session():
    global _API_HTTP_SESSION
    if _API_HTTP_SESSION is None or _API_HTTP_SESSION.closed:
        connector = aiohttp.TCPConnector(
            family=socket.AF_INET, limit=30, limit_per_host=10,
            ttl_dns_cache=300, keepalive_timeout=30, ssl=True
        )
        _API_HTTP_SESSION = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=20, connect=6, sock_connect=6, sock_read=12),
            headers={"Accept": "application/json, text/plain, */*"},
        )
    return _API_HTTP_SESSION

FIXED_CATEGORIES = [
    "ANDROID NON ROOT PANEL",
    "ANDROID ROOT PANEL",
    "IPHONE PANEL",
    "PC PANEL",
    "GUILD CALORY CREDIT",
    "CARROM PANEL"
]

# ==============================================================================
# YOUR PREMIUM EMOJIS – all required emoji IDs
# ==============================================================================
DEFAULT_EMOJIS = {
    'product_store': '6163205892834598715',
    'profile': '6035084557378654059',
    'add_balance': '5278467510604160626',
    'history': '6160968017304888311',
    'referral': '6032609071373226027',
    'support': '6161112036148255813',
    'ludo_spin': '6147764669361692707',
    'back': '6039539366177541657',
    'upi': '5807750375033278838',
    'reseller': '6120436698695338614',
    'tutorial': '5368653135101310687',
    'download': '6161336001512874965',
    'telegram': '6161096071754818473',
    'whatsapp': '6118193823823698862',
    'welcome': '5312361253610475399',
    'vip': '6086672466132865380',
    'category_android_non_root': '6161172706856282588',
    'category_android_root': '6161449831031118974',
    'category_iphone': '6161399700172840408',
    'category_pc': '5350554349074391003',
    'grid_id': '5474625972751837256',
    'name': '5215399540814781035',
    'account_level': '6129584162992034014',
    'regular_user': '5904630315946611415',
    'wallet': '6210859306602995217',
    'current_balance': '5316711376876485361',
    'global_stats': '6161437856662298090',
    'total_orders': '6160968017304888311',
    'total_spent': '5197503331215361533',
    'total_referrals': '5938196735200333756',
    'joined_grid': '5433614043006903194',
    'info_icon': '6037421444789440735',
    'check_icon': '6161241250239356403',
    'checkbox_icon': '6161437856662298090',
    'shield_icon': '6086672466132865380',
    'money_icon': '5890848474563352982',
    'redeem_icon': '5377624166436445368',
    'wallet_left': '6210859306602995217',
    'wallet_right': '5305699699204837855',
    'point_down': '6161302621027049305',
}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot_activity.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

DB_FILE = os.getenv("SHOP_DB_PATH", "yp_shop.db")
LICENSE_DB = os.getenv("LICENSE_DB", "licenses.db")
LICENSE_ENFORCED = False  # Admin license protection is mandatory; owner trial is the only free admin access.
INSTANCE_ID = os.getenv("INSTANCE_ID", "default")

# Paid admin-license settings. The payment API key is intentionally separate
# from the shop's post-login FamGateway setting so an admin can buy a license
# before gaining access to /admin.
LICENSE_PRICE_INR = float(os.getenv("LICENSE_PRICE_INR", "100"))
LICENSE_ORDER_TTL = int(os.getenv("LICENSE_ORDER_TTL", "300"))
LICENSE_PAYMENT_API_KEY = (
    os.getenv("LICENSE_PAYMENT_API_KEY", "").strip()
    or os.getenv("FAMPAY_API_KEY", "").strip()
)
LICENSE_PAYMENT_BASE_URL = os.getenv("LICENSE_PAYMENT_BASE_URL", "https://famgateway.in").strip().rstrip("/") or "https://famgateway.in"
try:
    LICENSE_PLANS = json.loads(os.getenv("LICENSE_PLANS_JSON", "{}"))
except Exception:
    LICENSE_PLANS = {}
if not LICENSE_PLANS:
    LICENSE_PLANS = {"1":{"days":1,"price":20},"7":{"days":7,"price":35},"15":{"days":15,"price":50},"30":{"days":30,"price":90},"2m":{"days":60,"price":150},"6m":{"days":180,"price":350},"1y":{"days":365,"price":600}}

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

@dp.error()
async def shop_error_handler(event: ErrorEvent):
    """Prevent a single bad Telegram update from killing the Shop Bot."""
    exc = event.exception
    if isinstance(exc, TelegramBadRequest) and "message is not modified" in str(exc).lower():
        logger.warning("Ignored harmless TelegramBadRequest: message is not modified")
        return True
    logger.exception("Ignored update exception: %s: %s", type(exc).__name__, exc)
    return True

# Two-admin authorization and notifications
# Short-lived in-memory license cache keeps rapid Admin button clicks responsive.
# Expiry is checked against the cached expiry timestamp, so a naturally expired
# license is never treated as active after its exact expiry time. Explicit
# activation/revocation paths invalidate this cache immediately.
_LICENSE_CACHE_TTL = 0.75
_LICENSE_CACHE = {}

def _invalidate_license_cache(user_id: int = 0) -> None:
    if user_id:
        _LICENSE_CACHE.pop((INSTANCE_ID, int(user_id)), None)
    else:
        _LICENSE_CACHE.clear()

def _license_active(user_id: int) -> bool:
    if not LICENSE_ENFORCED:
        return user_id in {ADMIN_ID, SECOND_ADMIN_ID}
    if user_id not in {ADMIN_ID, SECOND_ADMIN_ID}:
        return False
    now_mono = time.monotonic()
    cache_key = (INSTANCE_ID, int(user_id))
    cached = _LICENSE_CACHE.get(cache_key)
    if cached:
        cached_at, cached_expiry, cached_active = cached
        # Never extend a license beyond its real expiry.
        if cached_expiry and datetime.now(timezone.utc).replace(tzinfo=None) >= cached_expiry:
            _LICENSE_CACHE.pop(cache_key, None)
            return False
        if now_mono - cached_at < _LICENSE_CACHE_TTL:
            return bool(cached_active)
    try:
        conn = sqlite3.connect(LICENSE_DB, timeout=3, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout=3000")
        row = conn.execute("SELECT expires_at FROM activations WHERE instance_id=? AND user_id=? AND expires_at > ? AND revoked=0 LIMIT 1", (INSTANCE_ID, user_id, datetime.now(timezone.utc).replace(tzinfo=None).isoformat())).fetchone()
        conn.close()
        expiry = None
        active = False
        if row and row[0]:
            try:
                expiry = datetime.fromisoformat(row[0])
                active = expiry > datetime.now(timezone.utc).replace(tzinfo=None)
            except Exception:
                active = False
        _LICENSE_CACHE[cache_key] = (now_mono, expiry, active)
        return active
    except Exception:
        _LICENSE_CACHE.pop(cache_key, None)
        return False

def is_configured_admin(user_id: int) -> bool:
    return user_id in {ADMIN_ID, SECOND_ADMIN_ID} and user_id > 0

def is_admin(user_id: int) -> bool:
    return _license_active(user_id)

def is_bot_owner(user_id: int) -> bool:
    """The primary configured admin is the shop-bot owner for license controls."""
    return user_id == ADMIN_ID and user_id > 0


TRIAL_DURATIONS = {
    "5m": ("5 Minutes", timedelta(minutes=5)),
    "10m": ("10 Minutes", timedelta(minutes=10)),
    "1h": ("1 Hour", timedelta(hours=1)),
    "2h": ("2 Hours", timedelta(hours=2)),
}


def _grant_trial_license(user_id: int, duration_key: str) -> bool:
    """Create a short-lived active admin license for this bot's configured owner/admin."""
    if duration_key not in TRIAL_DURATIONS:
        return False
    _license_db_init()
    label, delta = TRIAL_DURATIONS[duration_key]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    exp = now + delta
    conn = sqlite3.connect(LICENSE_DB)
    try:
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute("SELECT 1 FROM activations WHERE instance_id=? AND user_id=? AND revoked=0 AND expires_at>? LIMIT 1", (INSTANCE_ID, user_id, now.isoformat())).fetchone()
        if active:
            conn.rollback()
            return False
        key = _new_license_key() if "_new_license_key" in globals() else "TRIAL-" + __import__("secrets").token_urlsafe(12)
        h = _license_hash(key)
        conn.execute("INSERT INTO issued_keys(key_hash,key_plain,instance_id,user_id,issued_at,expires_at,revoked,activated_at) VALUES(?,?,?,?,?,?,0,?)", (h,key,INSTANCE_ID,user_id,now.isoformat(),exp.isoformat(),now.isoformat()))
        conn.execute("INSERT INTO activations(instance_id,user_id,key_hash,activated_at,expires_at,revoked) VALUES(?,?,?,?,?,0)", (INSTANCE_ID,user_id,h,now.isoformat(),exp.isoformat()))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"Trial license grant failed: {e}")
        return False
    finally:
        conn.close()


def _expire_license(user_id: int) -> bool:
    """Immediately revoke every active license for this bot/admin."""
    _license_db_init()
    conn = sqlite3.connect(LICENSE_DB)
    try:
        rows = conn.execute("SELECT key_hash FROM activations WHERE instance_id=? AND user_id=? AND revoked=0", (INSTANCE_ID, user_id)).fetchall()
        cur = conn.execute("UPDATE activations SET revoked=1 WHERE instance_id=? AND user_id=? AND revoked=0", (INSTANCE_ID, user_id))
        for (key_hash,) in rows:
            conn.execute("UPDATE issued_keys SET revoked=1 WHERE key_hash=?", (key_hash,))
        conn.commit()
        _invalidate_license_cache(user_id)
        return cur.rowcount > 0
    finally:
        conn.close()

async def notify_admins(text: str, **kwargs):
    """Send an admin notification to both configured admins."""
    for admin_id in (ADMIN_ID, SECOND_ADMIN_ID):
        try:
            await bot.send_message(admin_id, text, **kwargs)
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")

def fmt_curr(amount: float) -> str:
    return f"₹{amount:,.2f}"

def natural_sort_key(value: Any) -> List[Any]:
    """Sort names naturally: A, B, C... and 1, 2, 10 instead of 1, 10, 2."""
    text = str(value or "").strip()
    return [int(part) if part.isdigit() else part.casefold()
            for part in re.split(r"(\d+)", text)]

# ==============================================================================
# 2. DATABASE FUNCTIONS
# ==============================================================================
def db_query(query: str, params: tuple = (), fetchone: bool = False, fetchall: bool = False, commit: bool = True) -> Any:
    conn = sqlite3.connect(DB_FILE, timeout=5, check_same_thread=False)
    conn.execute("PRAGMA busy_timeout=5000")
    c = conn.cursor()
    try:
        c.execute(query, params)
        if fetchone:
            res = c.fetchone()
        elif fetchall:
            res = c.fetchall()
        else:
            res = None
        # SELECTs never need a commit; avoiding needless commit/fsync work keeps UI callbacks snappy.
        if commit and not query.lstrip().upper().startswith("SELECT"):
            conn.commit()
        return res
    except Exception as e:
        logger.error(f"DB Error: {e} | Query: {query} | Params: {params}")
        if commit: conn.rollback()
        return None
    finally:
        conn.close()

def get_setting(key: str, default: str = "") -> str:
    val = db_query("SELECT value FROM settings WHERE key=?", (key,), fetchone=True)
    return val[0] if val and val[0] else default

def set_setting(key: str, value: str) -> None:
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))

def log_activity(user_id: int, action: str, details: str = "") -> None:
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db_query(
            "INSERT INTO activity_logs (user_id, action, details, timestamp) VALUES (?, ?, ?, ?)",
            (user_id, action, details, timestamp)
        )
    except Exception as e:
        logger.error(f"Failed to log activity: {e}")

def get_emoji(slot: str, default_id: str = None) -> str:
    stored = get_setting(f"emoji_{slot}", "")
    emoji_id = stored if stored and stored.isdigit() else (default_id or DEFAULT_EMOJIS.get(slot, ""))
    if emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">✨</tg-emoji>'
    return "✨"

def get_emoji_icon(slot: str, default_id: str = None) -> str:
    stored = get_setting(f"emoji_{slot}", "")
    emoji_id = stored if stored and stored.isdigit() else (default_id or DEFAULT_EMOJIS.get(slot, ""))
    return emoji_id

# ==============================================================================
# 3. STRING RESOURCES – using placeholders for premium emojis
# ==============================================================================
UI_TEXTS = {
    "start_menu": (
        "✨ <b>WELCOME TO THE STORE</b>\n\n"
        "{product_store} Product Store : all key purchase & instantly delivery\n"
        "{profile} My Profile : check your account information\n"
        "{add_balance} Add Balance : deposit balance & secure service\n"
        "{history} All History : check all key purchase history\n"
        "{referral} Referral : invite friends & earn rewards\n"
        "{tutorial} Tutorial : view tutorial and work this bot\n"
        "{support} Support : bot problem fixed for support admin\n"
        "{ludo_spin} Ludo Spin : play game and win balance\n"
        "{download} Download Files : download latest apk for safety."
    ),
    "download_files": (
        "🗂 <b><u>DOWNLOAD PREMIUM APK & FILES 📊</u></b>\n\n"
        "🌐 All our highly secured, premium, and updated files\n"
        "are securely hosted on our private channel! ⚠️⛔️\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📱 <b>WHAT YOU GET:</b> 📌\n\n"
        "✔️ Latest APK Updates 🔔\n"
        "✔️ 100% Virus Free & Secure ‼️\n"
        "✔️ All Configs & Scripts 🌸\n"
        "✔️ Complete Installation Guides 🔺\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⌨️ Tap the button below to access the Download Channel! 📝"
    ),
    "lucky_dice_result": (
        "{ludo_spin} <b><u>LUCKY DICE RESULT 🔨 💯</u></b>\n\n"
        "🎲 <b>Dice Value:</b> {dice_value}\n\n"
        "💸 <b>You Won:</b> {won_amount}\n"
        "💰 <b>Total Balance:</b> {new_balance}\n\n"
        "Congratulations! Come back after 24 hours."
    ),
    "vip_menu": (
        "🌟 <b><u>VIP MEMBERSHIP CLUB</u></b> 🌟\n\n"
        "Unlock premium benefits and permanent discounts!\n\n"
        "💎 <b>VIP Benefits:</b>\n"
        "• Flat 15% off on ALL products (Stacks with Reseller!)\n"
        "• Priority Support\n"
        "• Exclusive VIP-only giveaways\n\n"
        "💳 <b>VIP Price:</b> ₹299.00 (Lifetime)\n"
        "👤 <b>Your Status:</b> {vip_status}"
    ),
    "add_balance_menu": (
        "{add_balance} <b>ADD BALANCE</b> {info_icon}\n\n"
        "{info_icon} Select your preferred payment method. {check_icon}\n\n"
        "┣ {upi} FamPay / UPI — Fast Indian payments {checkbox_icon}\n"
        "{shield_icon} Payments are verified securely. {check_icon}"
    )
}

def get_ui_text(key: str, **kwargs) -> str:
    val = db_query("SELECT value FROM settings WHERE key=?", (f"ui_{key}",), fetchone=True)
    template = val[0] if val and val[0] else UI_TEXTS.get(key, "")

    emoji_map = {
        '{product_store}': get_emoji('product_store'),
        '{profile}': get_emoji('profile'),
        '{add_balance}': get_emoji('add_balance'),
        '{history}': get_emoji('history'),
        '{referral}': get_emoji('referral'),
        '{tutorial}': get_emoji('tutorial'),
        '{support}': get_emoji('support'),
        '{ludo_spin}': get_emoji('ludo_spin'),
        '{download}': get_emoji('download'),
        '{telegram}': get_emoji('telegram'),
        '{whatsapp}': get_emoji('whatsapp'),
        '{upi}': get_emoji('upi'),
        '{info_icon}': get_emoji('info_icon'),
        '{check_icon}': get_emoji('check_icon'),
        '{checkbox_icon}': get_emoji('checkbox_icon'),
        '{shield_icon}': get_emoji('shield_icon'),
        '{money_icon}': get_emoji('money_icon'),
        '{redeem_icon}': get_emoji('redeem_icon'),
        '{wallet_left}': get_emoji('wallet_left'),
        '{wallet_right}': get_emoji('wallet_right'),
        '{point_down}': get_emoji('point_down'),
    }
    for placeholder, emoji_tag in emoji_map.items():
        template = template.replace(placeholder, emoji_tag)

    if kwargs:
        try:
            return template.format(**kwargs)
        except KeyError as e:
            logger.warning(f"Missing formatting key for template {key}: {e}")
    return template

# ==============================================================================
# 4. DATABASE INITIALISATION & MIGRATION
# ==============================================================================
def init_db() -> None:
    conn = sqlite3.connect(DB_FILE, timeout=8, check_same_thread=False)
    conn.execute("PRAGMA busy_timeout=8000")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, 
            phone TEXT, 
            education TEXT,
            first_name TEXT, 
            username TEXT,
            balance REAL DEFAULT 0.0, 
            account_type TEXT DEFAULT 'Regular', 
            orders_count INTEGER DEFAULT 0, 
            spent REAL DEFAULT 0.0, 
            referrals_count INTEGER DEFAULT 0, 
            referral_earned REAL DEFAULT 0.0, 
            referred_by INTEGER, 
            last_spin TEXT, 
            joined_date TEXT,
            is_reseller INTEGER DEFAULT 0,
            reseller_since TEXT,
            total_saved REAL DEFAULT 0.0,
            is_banned INTEGER DEFAULT 0,
            warnings INTEGER DEFAULT 0,
            is_vip INTEGER DEFAULT 0,
            vip_since TEXT
        )
    ''')
    
    try: c.execute("ALTER TABLE users ADD COLUMN education TEXT DEFAULT ''")
    except sqlite3.OperationalError: pass

    c.execute('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            category TEXT, 
            panel_name TEXT DEFAULT '',
            name TEXT, 
            product_group TEXT DEFAULT '',
            price_inr REAL, 
            reseller_price REAL DEFAULT 0.0,
            stock INTEGER, 
            apk_link TEXT, 
            validity TEXT DEFAULT 'Lifetime', 
            device_limit TEXT DEFAULT '1 Device',
            is_active INTEGER DEFAULT 1
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS api_providers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            url TEXT NOT NULL,
            api_key TEXT DEFAULT '',
            master_key TEXT DEFAULT '',
            http_method TEXT DEFAULT 'POST',
            api_key_field TEXT DEFAULT 'api_key',
            master_key_header TEXT DEFAULT 'x-master-key',
            action_field TEXT DEFAULT 'action',
            product_id_field TEXT DEFAULT 'product_id',
            duration_field TEXT DEFAULT 'duration',
            android_id_field TEXT DEFAULT 'android_id',
            response_key_field TEXT DEFAULT 'key',
            success_field TEXT DEFAULT 'status',
            success_value TEXT DEFAULT 'success',
            active INTEGER DEFAULT 1,
            requires_android_id INTEGER DEFAULT 0
        )
    ''')

    c.execute('''
        CREATE TABLE IF NOT EXISTS product_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            product_id INTEGER, 
            key_text TEXT, 
            is_used INTEGER DEFAULT 0
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            user_id INTEGER, 
            product_name TEXT, 
            price_paid REAL, 
            delivered_key TEXT, 
            purchase_date TEXT
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            user_id INTEGER, 
            message TEXT, 
            status TEXT DEFAULT 'Open',
            created_at TEXT
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, 
            value TEXT
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS coupons (
            code TEXT PRIMARY KEY, 
            amount REAL, 
            uses_left INTEGER
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS redeemed (
            user_id INTEGER, 
            code TEXT
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            order_id TEXT PRIMARY KEY, 
            user_id INTEGER, 
            amount_inr REAL, 
            gateway_amount REAL DEFAULT 0.0,
            status TEXT, 
            timestamp INTEGER
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS crypto_txns (
            txid TEXT PRIMARY KEY, 
            user_id INTEGER, 
            amount_usdt REAL, 
            timestamp INTEGER
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS spin_rewards (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            amount REAL
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            details TEXT,
            timestamp TEXT
        )
    ''')

    migrations = [
        "ALTER TABLE users ADD COLUMN is_vip INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN vip_since TEXT",
        "ALTER TABLE products ADD COLUMN is_active INTEGER DEFAULT 1",
        "ALTER TABLE tickets ADD COLUMN created_at TEXT",
        "ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN warnings INTEGER DEFAULT 0",
        "ALTER TABLE products ADD COLUMN panel_name TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN external_enabled INTEGER DEFAULT 0",
        "ALTER TABLE products ADD COLUMN external_product_id TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN requires_android_id INTEGER DEFAULT 0",
        "ALTER TABLE products ADD COLUMN external_duration TEXT DEFAULT ''",
        "ALTER TABLE products ADD COLUMN api_provider_id INTEGER DEFAULT NULL",
        "ALTER TABLE products ADD COLUMN product_group TEXT DEFAULT ''",
        "ALTER TABLE api_providers ADD COLUMN raw_code TEXT DEFAULT ''",
        "ALTER TABLE transactions ADD COLUMN gateway_amount REAL DEFAULT 0.0",
        "ALTER TABLE transactions ADD COLUMN purpose TEXT DEFAULT 'deposit'",
        "ALTER TABLE transactions ADD COLUMN product_id INTEGER DEFAULT NULL",
        "ALTER TABLE transactions ADD COLUMN android_id TEXT DEFAULT ''",
    ]
    for mig in migrations:
        try: c.execute(mig)
        except sqlite3.OperationalError: pass

    try:
        c.execute("UPDATE products SET product_group = name WHERE COALESCE(product_group, '') = ''")
    except sqlite3.OperationalError:
        pass

    # Backfill the product group and API duration for existing products. The purchase code still
    # has additional fallbacks, so old databases remain compatible.
    try:
        c.execute("UPDATE products SET external_duration = validity WHERE COALESCE(external_duration, '') = ''")
    except sqlite3.OperationalError:
        pass
    
    c.execute("SELECT COUNT(*) FROM spin_rewards")
    if c.fetchone()[0] == 0:
        c.executemany("INSERT INTO spin_rewards (amount) VALUES (?)", [(0.0,), (1.0,), (2.0,), (5.0,), (10.0,)])

    default_settings = [
        ('spin_status', 'ON'),
        ('daily_spin_limit', '50.0'),
        ('reseller_system_status', 'ON'),
        ('bot_status', 'ON'),
        ('how_to_video', 'None'),
        ('all_files_link', 'None'),
        ('fampay_api_key', ''),
        ('fampay_base_url', 'https://famgateway.in'),
        ('fampay_gmail', ''),
        ('fampay_upi', ''),
        ('vip_status', 'OFF'),
        ('reseller_setup_fee', '200.0'),
        ('reseller_min_balance', '500.0'),
        ('migration_done', '0'),
        ('support_telegram', 'https://t.me/YOUR_SUPPORT'),
        ('support_whatsapp', 'https://wa.me/YOUR_NUMBER'),
        ('ui_start_menu', UI_TEXTS['start_menu']),
        ('ui_download_files', UI_TEXTS['download_files']),
        ('ui_lucky_dice_result', UI_TEXTS['lucky_dice_result']),
        ('ui_vip_menu', UI_TEXTS['vip_menu']),
        ('ui_add_balance_menu', UI_TEXTS['add_balance_menu']),
        ('external_api_url', 'https://ffpanelshop.onrender.com/api/reseller_v1.php'),
        ('external_api_key', ''),
        ('external_master_key', ''),
    ]
    for slot, emoji_id in DEFAULT_EMOJIS.items():
        default_settings.append((f"emoji_{slot}", emoji_id))
    
    for key, val in default_settings:
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, val))

    # Keep the existing/legacy API available inside Multi-API Manager under a stable name.
    # Credentials come from the existing external API settings/environment; nothing new is invented.
    legacy_url = os.getenv("BUNTI_BHAIYA_API_URL", "").strip() or "https://ffpanelshop.onrender.com/api/reseller_v1.php"
    _legacy_key_row = c.execute("SELECT value FROM settings WHERE key='external_api_key'").fetchone()
    _legacy_master_row = c.execute("SELECT value FROM settings WHERE key='external_master_key'").fetchone()
    legacy_key = os.getenv("BUNTI_BHAIYA_API_KEY", "").strip() or ((_legacy_key_row[0] if _legacy_key_row else "") or "")
    legacy_master = os.getenv("BUNTI_BHAIYA_MASTER_KEY", "").strip() or ((_legacy_master_row[0] if _legacy_master_row else "") or "")
    c.execute("""INSERT OR IGNORE INTO api_providers
        (name,url,api_key,master_key,http_method,api_key_field,master_key_header,action_field,product_id_field,duration_field,android_id_field,response_key_field,success_field,success_value,active,requires_android_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,1)""",
        ("BUNTI BHAIYA", legacy_url, legacy_key, legacy_master, "POST", "api_key", "x-master-key", "action", "product_id", "duration", "android_id", "key", "status", "success"))

    try: c.execute("ALTER TABLE transactions ADD COLUMN quantity INTEGER DEFAULT 1")
    except sqlite3.OperationalError: pass
    conn.commit()
    conn.close()

def migrate_categories() -> None:
    done = get_setting("migration_done", "0")
    
    # ALWAYS force update emojis and UI texts regardless of migration status
    logger.info("Forcing emoji and UI text updates...")
    
    # Update all emoji settings
    for slot, emoji_id in DEFAULT_EMOJIS.items():
        set_setting(f"emoji_{slot}", emoji_id)
    
    # Force update UI texts
    set_setting("ui_start_menu", UI_TEXTS['start_menu'])
    set_setting("ui_add_balance_menu", UI_TEXTS['add_balance_menu'])
    set_setting("ui_download_files", UI_TEXTS['download_files'])
    set_setting("ui_lucky_dice_result", UI_TEXTS['lucky_dice_result'])
    set_setting("ui_vip_menu", UI_TEXTS['vip_menu'])
    logger.info("UI texts and emojis updated with new placeholders and IDs.")
    
    if done == "1":
        return
    
    logger.info("Running category migration...")
    
    mapping = {
        "android non root panel": "ANDROID NON ROOT PANEL",
        "android root panel": "ANDROID ROOT PANEL",
        "iphone panel": "IPHONE PANEL",
        "pc panel": "PC PANEL",
        "guild calory credit": "GUILD CALORY CREDIT",
        "carrom panel": "CARROM PANEL",
    }
    for old, new in mapping.items():
        db_query("UPDATE products SET category = ? WHERE LOWER(category) = ?", (new, old))
    
    set_setting("migration_done", "1")
    logger.info("Category migration complete.")

# ==============================================================================
# 5. MIDDLEWARES & SECURITY
# ==============================================================================
async def safe_call_answer(call: CallbackQuery, *args, **kwargs):
    """Answer a callback without ever breaking the actual handler if Telegram already answered it."""
    try:
        return await call.answer(*args, **kwargs)
    except Exception:
        return None


async def hacker_loading(message: Message, text: str = "Decrypting Data") -> Message:
    """Fast compatibility wrapper; the old animated 0.9s loader is removed for responsiveness."""
    return await message.answer(f"⚡ {text}...")

# Callback prefixes/data that belong exclusively to the Shop Bot Admin Panel.
# Keeping this gate in middleware is important because an old/stale admin menu can
# remain visible in Telegram even after the license has expired or the bot code was updated.
_ADMIN_CALLBACK_PREFIXES = (
    "admin_", "api_", "edit_p_", "edit_emoji_", "edit_reseller_", "edit_ui_",
    "set_cat_emoji_", "set_panel_emoji_", "setprodapi_", "toggle_p_", "delete_p_",
    "delkey_p_", "newprodcat_", "durpreset_", "variantprov_", "variant_android_",
    "usrctrl_", "confirm_ban_", "close_ticket_", "reply_ticket_", "spin_add",
    "spin_del", "spin_limit", "spin_toggle", "spin_view", "reseller_make",
    "reseller_remove", "reseller_view",
)
_ADMIN_CALLBACK_EXACT = {"admin_panel_back"}

def _is_admin_callback(data: str) -> bool:
    if not data:
        return False
    return data in _ADMIN_CALLBACK_EXACT or data.startswith(_ADMIN_CALLBACK_PREFIXES)

class GlobalSecurityMiddleware(BaseMiddleware):
    def __init__(self):
        super().__init__()
        self.last_action_times = {}

    async def __call__(self, handler, event, data):
        user_id = event.from_user.id

        # If an Admin FSM conversation is still open from an older/stale panel,
        # an expired/no-key admin must not be able to continue that workflow.
        if isinstance(event, Message) and is_configured_admin(user_id):
            fsm = data.get("state")
            try:
                current_state = await fsm.get_state() if fsm else None
            except Exception:
                current_state = None
            if current_state and current_state.startswith("AdminStates:") and not is_admin(user_id):
                try: await fsm.clear()
                except Exception: pass
                return await event.answer(
                    "🔒 <b>Admin license required.</b>\n\nUse <code>/activate YOUR-KEY</code> or buy/renew your license.",
                    reply_markup=license_buy_kb(user_id), parse_mode="HTML"
                )

        # HARD ADMIN LICENSE GATE: this runs before every admin callback.
        # This prevents stale inline keyboards from opening any Admin Panel action
        # after expiry, even if the /admin screen was sent earlier.
        if isinstance(event, CallbackQuery) and _is_admin_callback(event.data or ""):
            if not is_admin(user_id):
                msg = "🔒 Admin license required.\n\nUse /activate YOUR-KEY or choose Buy / Renew License."
                # Always answer the callback first so stale buttons never leave
                # Telegram's loading spinner hanging. Then replace the stale
                # admin menu with the locked license screen.
                await safe_call_answer(event)
                try:
                    await event.message.edit_text(
                        msg, reply_markup=license_buy_kb(user_id) if is_configured_admin(user_id) else None, parse_mode="HTML"
                    )
                except Exception:
                    pass
                return

        # License protects Admin Panel access, not normal customer shopping.
        if not is_configured_admin(user_id):
            user_info = db_query("SELECT is_banned FROM users WHERE user_id=?", (user_id,), fetchone=True, commit=False)
            if user_info and user_info[0] == 1:
                msg = "🚫 <b>ACCESS DENIED</b>\nYou have been banned from using this bot.\nContact support if you think this is a mistake."
                if isinstance(event, Message): await event.answer(msg)
                elif isinstance(event, CallbackQuery): await safe_call_answer(event, msg, show_alert=True)
                return

            status_check = db_query("SELECT value FROM settings WHERE key='bot_status'", fetchone=True, commit=False)
            status = status_check[0] if status_check else 'ON'
            if status == 'OFF':
                msg = "⚠️ <b>Store Maintenance</b>\n\nThe store is currently offline for updates. Please check back later!"
                if isinstance(event, Message): await event.answer(msg)
                elif isinstance(event, CallbackQuery): await safe_call_answer(event, "⚠️ Bot is currently OFF for Maintenance.", show_alert=True)
                return

        # One safety net for the whole callback layer: no button should leave the
        # Telegram spinner hanging forever, and one handler exception must not kill polling.
        try:
            result = await handler(event, data)
            if isinstance(event, CallbackQuery):
                await safe_call_answer(event)
            return result
        except Exception as exc:
            logger.exception("Callback handler failed: %s | data=%r | user=%s", exc, getattr(event, "data", None), user_id)
            if isinstance(event, CallbackQuery):
                await safe_call_answer(event, "⚠️ This button hit an internal error. Please try again.", show_alert=True)
            elif isinstance(event, Message):
                try: await event.answer("⚠️ An internal error occurred. Please try again.")
                except Exception: pass
            return

dp.message.middleware(GlobalSecurityMiddleware())
dp.callback_query.middleware(GlobalSecurityMiddleware())

# ==============================================================================
# 6. FSM STATES
# ==============================================================================
class UserStates(StatesGroup):
    wait_for_ticket = State()
    wait_for_redeem = State()
    custom_amount_input = State()
    purchase_android_id = State()
    onboarding_education = State()

class AdminStates(StatesGroup):
    add_prod_category = State()
    add_prod_panel_name = State()
    add_prod_name = State()
    add_prod_validity = State()
    add_prod_device_limit = State()
    add_prod_price = State()
    add_prod_reseller_price = State()
    add_prod_apk = State()
    add_prod_keys = State()
    
    edit_prod_field = State()
    wait_for_new_value = State()
    wait_for_add_keys = State()
    wait_for_delete_key = State()
    
    broadcast_msg = State()
    add_coupon_code = State()
    add_coupon_amount = State()
    add_coupon_uses = State()
    
    wait_for_fampay_api = State()
    
    ticket_reply_msg = State()
    reseller_manage_id = State()
    manage_target_user = State()
    wait_for_add_money = State()
    wait_for_minus_money = State()
    wait_for_warning = State()
    
    spin_add_reward = State()
    spin_set_limit = State()
    wait_for_howto_video = State()
    wait_for_all_files_link = State()
    
    edit_ui_text = State()
    edit_reseller_price = State()
    wait_for_reseller_setup_fee = State()
    wait_for_reseller_min_balance = State()
    confirm_ban = State()
    
    wait_for_support_telegram = State()
    wait_for_support_whatsapp = State()
    wait_for_category_emoji = State()
    wait_for_panel_emoji_id = State()
    wait_for_emoji_slot = State()
    wait_for_ext_url = State()
    wait_for_ext_key = State()
    wait_for_ext_master = State()
    api_add_name = State()
    api_add_url = State()
    api_add_key = State()
    api_add_master = State()
    api_add_code = State()
    api_edit_value = State()
    api_edit_name = State()
    add_prod_external = State()
    add_prod_api_provider = State()
    add_prod_external_product_id = State()
    add_prod_external_duration = State()
    add_prod_external_android = State()
    add_prod_variant_count = State()
    add_prod_variant_duration = State()
    add_prod_variant_price = State()
    add_prod_variant_reseller = State()
    add_prod_variant_device = State()
    add_prod_variant_apk = State()
    add_prod_variant_provider = State()
    add_prod_variant_pid = State()
    add_prod_variant_api_duration = State()
    add_prod_variant_android = State()
    add_prod_variant_keys = State()
    spin_limit = State()

# ==============================================================================
# 7. KEYBOARDS
# ==============================================================================
def get_category_emoji(category: str) -> str:
    """Return only the custom-emoji ID for use in Telegram's button icon field.

    IMPORTANT: never put the numeric custom-emoji ID into button text. If Telegram
    cannot render the premium/custom icon, the normal category name remains visible.
    """
    slot_map = {
        "ANDROID NON ROOT PANEL": "category_android_non_root",
        "ANDROID ROOT PANEL": "category_android_root",
        "IPHONE PANEL": "category_iphone",
        "PC PANEL": "category_pc",
        "GUILD CALORY CREDIT": "product_store",
        "CARROM PANEL": "product_store",
    }
    slot = slot_map.get(str(category or "").strip().upper())
    if slot:
        emoji_id = get_emoji_icon(slot, DEFAULT_EMOJIS.get(slot, ""))
        return emoji_id if str(emoji_id).isdigit() else ""
    return ""

def category_display_name(category: str) -> str:
    """Human-facing category name; internal DB names never leak into UI."""
    labels = {
        "ANDROID NON ROOT PANEL": "Android Non Root",
        "ANDROID ROOT PANEL": "Android Root",
        "IPHONE PANEL": "iPhone",
        "PC PANEL": "PC",
        "GUILD CALORY CREDIT": "Guild Gallery / Credit",
        "CARROM PANEL": "Carrom",
    }
    value = str(category or "").strip()
    return labels.get(value.upper(), value)

def get_panel_emoji(panel_name: str) -> str:
    stored = get_setting(f"panel_emoji_{panel_name}", "")
    if stored and stored.isdigit():
        return stored
    return get_emoji_icon("product_store")

def contact_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Verify Contact", request_contact=True)]], 
        resize_keyboard=True, 
        one_time_keyboard=True
    )

def main_menu_kb(user_id: Optional[int] = None) -> InlineKeyboardMarkup:
    status_check = db_query("SELECT value FROM settings WHERE key='reseller_system_status'", fetchone=True)
    sys_status = status_check[0] if status_check else 'ON'
    vip_sys_check = db_query("SELECT value FROM settings WHERE key='vip_status'", fetchone=True)
    vip_system = vip_sys_check[0] if vip_sys_check else 'OFF'
    
    is_reseller = False
    if user_id:
        user_check = db_query("SELECT is_reseller FROM users WHERE user_id=?", (user_id,), fetchone=True)
        if user_check:
            is_reseller = bool(user_check[0])

    kb = InlineKeyboardMarkup(inline_keyboard=[])
    
    kb.inline_keyboard.append([
        InlineKeyboardButton(
            text="Product Store", callback_data="menu_shop",
            icon_custom_emoji_id=get_emoji_icon("product_store"),
            style="primary"
        )
    ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(
            text="My Profile", callback_data="menu_profile",
            icon_custom_emoji_id=get_emoji_icon("profile"),
            style="success"
        ),
        InlineKeyboardButton(
            text="Add Balance", callback_data="menu_add_balance",
            icon_custom_emoji_id=get_emoji_icon("add_balance"),
            style="success"
        )
    ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(
            text="All History", callback_data="menu_orders",
            icon_custom_emoji_id=get_emoji_icon("history"),
            style="success"
        ),
        InlineKeyboardButton(
            text="Referral", callback_data="menu_referral",
            icon_custom_emoji_id=get_emoji_icon("referral"),
            style="success"
        )
    ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(
            text="Tutorials", callback_data="menu_how_to",
            icon_custom_emoji_id=get_emoji_icon("tutorial"),
            style="success"
        ),
        InlineKeyboardButton(
            text="Support", callback_data="menu_support",
            icon_custom_emoji_id=get_emoji_icon("support"),
            style="success"
        )
    ])
    kb.inline_keyboard.append([
        InlineKeyboardButton(
            text="Ludo Spin", callback_data="menu_spin_landing",
            icon_custom_emoji_id=get_emoji_icon("ludo_spin"),
            style="success"
        ),
        InlineKeyboardButton(
            text="Download Files", callback_data="menu_all_files",
            icon_custom_emoji_id=get_emoji_icon("download"),
            style="success"
        )
    ])
    
    extras_row = []
    if sys_status == 'ON' or is_reseller:
        extras_row.append(InlineKeyboardButton(
            text="Reseller Panel", callback_data="menu_reseller_dash",
            icon_custom_emoji_id=get_emoji_icon("reseller"),
            style="success"
        ))
    if vip_system == 'ON':
        extras_row.append(InlineKeyboardButton(
            text="VIP Club", callback_data="menu_vip_dash",
            icon_custom_emoji_id=get_emoji_icon("vip"),
            style="success"
        ))
    if extras_row:
        kb.inline_keyboard.append(extras_row)
        
    return kb

def back_kb(callback: str = "back_main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="BACK", callback_data=callback,
                icon_custom_emoji_id=get_emoji_icon("back"),
                style="danger"
            )
        ]]
    )

def admin_kb() -> InlineKeyboardMarkup:
    status = db_query("SELECT value FROM settings WHERE key='bot_status'", fetchone=True, commit=False)
    status_val = status[0] if status else 'ON'
    vip_status = db_query("SELECT value FROM settings WHERE key='vip_status'", fetchone=True)
    vip_val = vip_status[0] if vip_status else 'OFF'
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Bot Statistics", callback_data="admin_view_stats", icon_custom_emoji_id=get_emoji_icon("global_stats"), style="success")],
        [InlineKeyboardButton(text="👥 User Control Panel", callback_data="admin_user_control_start", icon_custom_emoji_id=get_emoji_icon("profile"), style="success")],
        [
            InlineKeyboardButton(text="➕ Add Product", callback_data="admin_add_prod", icon_custom_emoji_id=get_emoji_icon("product_store"), style="success"),
            InlineKeyboardButton(text="📦 Manage Products", callback_data="admin_manage_prods", icon_custom_emoji_id=get_emoji_icon("product_store"), style="success")
        ],
        [
            InlineKeyboardButton(text="👑 Reseller Mgmt", callback_data="admin_reseller_menu", icon_custom_emoji_id=get_emoji_icon("reseller"), style="success"),
            InlineKeyboardButton(text="🎰 Spin Settings", callback_data="admin_spin_menu", icon_custom_emoji_id=get_emoji_icon("ludo_spin"), style="success")
        ],
        [
            InlineKeyboardButton(text="🎟 Create Coupon", callback_data="admin_create_coupon", icon_custom_emoji_id=get_emoji_icon("redeem_icon"), style="success"),
            InlineKeyboardButton(text="📢 Broadcast", callback_data="admin_broadcast_btn", icon_custom_emoji_id=get_emoji_icon("telegram"), style="success")
        ],
        [
            InlineKeyboardButton(text="🎫 View Tickets", callback_data="admin_view_tickets", icon_custom_emoji_id=get_emoji_icon("support"), style="success"),
            InlineKeyboardButton(text="📹 Tutorial Video", callback_data="admin_set_video", icon_custom_emoji_id=get_emoji_icon("tutorial"), style="success")
        ],
        [
            InlineKeyboardButton(text="🔗 All Files Link", callback_data="admin_set_all_files", icon_custom_emoji_id=get_emoji_icon("download"), style="success"),
            InlineKeyboardButton(text="🎨 Edit All Emojis", callback_data="admin_edit_emojis", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="success")
        ],
        [
            InlineKeyboardButton(text="💳 FamPay Gateway Setup", callback_data="admin_setup_fampay", icon_custom_emoji_id=get_emoji_icon("upi"), style="success")
        ],
        [
            InlineKeyboardButton(text="🔌 API Manager (Create / Manage APIs)", callback_data="admin_api_manager", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="success")
        ],
        [
            InlineKeyboardButton(text="✏️ Edit UI Texts", callback_data="admin_edit_ui_menu", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="success"),
            InlineKeyboardButton(text="📝 Edit Reseller Price", callback_data="admin_edit_reseller_price", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="success")
        ],
        [
            InlineKeyboardButton(text="💰 Reseller Fee", callback_data="admin_set_reseller_fee", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="success"),
            InlineKeyboardButton(text="💳 Min Balance", callback_data="admin_set_reseller_min", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="success")
        ],
        [
            InlineKeyboardButton(text="📞 Set Support Links", callback_data="admin_set_support_links", icon_custom_emoji_id=get_emoji_icon("support"), style="success"),
            InlineKeyboardButton(text="🎨 Set Category Emojis", callback_data="admin_set_category_emojis", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="success")
        ],
        [
            InlineKeyboardButton(text="🖼 Set Panel Emojis", callback_data="admin_set_panel_emojis", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="success")
        ],
        [
            InlineKeyboardButton(
                text=f"Bot Status: {status_val} {'🟢' if status_val == 'ON' else '🔴'}",
                callback_data="admin_toggle_bot",
                icon_custom_emoji_id=get_emoji_icon("check_icon"),
                style="success" if status_val == 'ON' else "danger"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"VIP System: {vip_val} {'🟢' if vip_val == 'ON' else '🔴'}",
                callback_data="admin_toggle_vip_sys",
                icon_custom_emoji_id=get_emoji_icon("vip"),
                style="success" if vip_val == 'ON' else "danger"
            )
        ]
    ])
    return kb

def admin_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Back to Admin", callback_data="admin_panel_back",
            icon_custom_emoji_id=get_emoji_icon("back"),
            style="danger"
        )
    ]])

# ==============================================================================
# 8. NOTIFICATIONS
# ==============================================================================
async def send_advanced_notification(user_id: int, notif_type: str, amount: float, product: str = None, key: str = None, gateway: str = "FamPay") -> None:
    user_info = db_query("SELECT first_name, phone, username, is_reseller, is_vip FROM users WHERE user_id=?", (user_id,), fetchone=True)
    
    name = user_info[0] if user_info else "Unknown"
    phone = user_info[1] if user_info and user_info[1] else "Not Provided"
    username = f"@{user_info[2]}" if user_info and user_info[2] else "None"
    
    tags = []
    if user_info and user_info[3]: tags.append("👑 Reseller")
    if user_info and user_info[4]: tags.append("🌟 VIP")
    tag_str = " | ".join(tags) if tags else "👤 Regular"
        
    time_now = datetime.now().strftime("%d-%m-%Y %I:%M %p")
    
    if notif_type == "ORDER":
        title = "🛒 <b>NEW ORDER PROCESSED!</b> 🛒"
        details = (f"📦 <b>Product:</b> {product}\n🔑 <b>Key:</b> <code>{key}</code>\n💰 <b>Amount Paid:</b> ₹{amount:.2f}\n📅 <b>Time:</b> {time_now}")
    else:
        title = "💰 <b>NEW WALLET DEPOSIT!</b> 💰"
        details = (f"💵 <b>Amount Added:</b> ₹{amount:.2f}\n🧾 <b>Gateway:</b> {gateway}\n🆔 <b>Reference:</b> <code>{product}</code>\n📅 <b>Time:</b> {time_now}")

    msg = f"{title}\n━━━━━━━━━━━━━━━━━━\n👤 <b>Name:</b> {name}\n🆔 <b>User ID:</b> <code>{user_id}</code>\n📱 <b>Phone:</b> {phone}\n🔗 <b>Username:</b> {username}\n🏷 <b>Status:</b> {tag_str}\n━━━━━━━━━━━━━━━━━━\n{details}"
    try: 
        await notify_admins( msg, parse_mode='HTML')
    except Exception as e: 
        logger.error(f"Failed to send admin notification: {e}")

# ==============================================================================
# 9. FAMPAY PAYMENT VERIFIER
# ==============================================================================
FAMPAY_ORDER_TTL = 300

def get_fampay_base_url() -> str:
    base = get_setting("fampay_base_url", "https://famgateway.in").strip().rstrip("/")
    return base or "https://famgateway.in"

def get_fampay_create_url() -> str:
    return f"{get_fampay_base_url()}/api/qr.php"

def get_fampay_verify_url() -> str:
    return f"{get_fampay_base_url()}/api/verify-order.php"

def mark_transaction_paid_once(order_id: str, user_id: int, amount: float) -> bool:
    """Atomically mark a pending transaction paid and credit the wallet once."""
    conn = sqlite3.connect(DB_FILE)
    try:
        c = conn.cursor()
        c.execute("BEGIN IMMEDIATE")
        c.execute("UPDATE transactions SET status='paid' WHERE order_id=? AND user_id=? AND status='pending'", (order_id, user_id))
        if c.rowcount != 1:
            conn.rollback()
            return False
        c.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"FamPay atomic credit failed for {order_id}: {e}")
        return False
    finally:
        conn.close()

async def run_payment_verification(user_id: int, order_id: str, reply_target: Any) -> None:
    txn = db_query("SELECT amount_inr, status, timestamp, COALESCE(gateway_amount, 0) FROM transactions WHERE order_id=? AND user_id=?", (order_id, user_id), fetchone=True)
    if not txn:
        err = "❌ Invalid or fake payment order ID."
        if isinstance(reply_target, CallbackQuery): await reply_target.answer(err, show_alert=True)
        else: await reply_target.answer(err)
        return

    base_amount, status, created_ts, gateway_amount = txn
    if status == 'paid':
        msg = "✅ This payment has already been securely credited to your wallet."
        if isinstance(reply_target, CallbackQuery): await reply_target.answer(msg, show_alert=True)
        else: await reply_target.answer(msg)
        return
    if status in ('expired', 'failed') or time.time() - created_ts > FAMPAY_ORDER_TTL:
        db_query("UPDATE transactions SET status='expired' WHERE order_id=? AND status='pending'", (order_id,))
        msg = "⏳ <b>FamPay Order Expired</b>\nPlease create a new payment request."
        if isinstance(reply_target, CallbackQuery): await reply_target.message.edit_text(msg, reply_markup=back_kb(), parse_mode='HTML')
        else: await reply_target.answer(msg, reply_markup=back_kb(), parse_mode='HTML')
        return

    api_key = get_setting("fampay_api_key", "").strip()
    if not api_key:
        msg = "⚠️ FamPay Gateway API key is not configured. Ask an admin to set it up."
        if isinstance(reply_target, CallbackQuery): await reply_target.answer(msg, show_alert=True)
        else: await reply_target.answer(msg)
        return

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(get_fampay_verify_url(), params={"api_key": api_key, "order_id": order_id}, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                try: res_json = await resp.json(content_type=None)
                except Exception: res_json = {}

                if resp.status == 200 and res_json.get("status") == "success":
                    data = res_json.get("data") or {}
                    paid_amount = float(data.get("amount") or 0)
                    if gateway_amount and paid_amount and abs(paid_amount - float(gateway_amount)) > 0.011:
                        msg = "⚠️ Payment amount mismatch detected. The wallet was NOT credited."
                        if isinstance(reply_target, CallbackQuery): await reply_target.answer(msg, show_alert=True)
                        else: await reply_target.answer(msg)
                        return
                    if mark_transaction_paid_once(order_id, user_id, float(base_amount)):
                        success_msg = (
                            f"🎉 <b>FAMPAY PAYMENT VERIFIED!</b>\n\n"
                            f"✅ {fmt_curr(base_amount)} has been added to your wallet.\n"
                            f"🧾 Order: <code>{order_id}</code>\n"
                            f"🔖 UTR: <code>{data.get('utr', 'N/A')}</code>"
                        )
                        if isinstance(reply_target, CallbackQuery): await reply_target.message.edit_text(success_msg, reply_markup=back_kb(), parse_mode='HTML')
                        else: await reply_target.answer(success_msg, reply_markup=back_kb(), parse_mode='HTML')
                        await send_advanced_notification(user_id, "DEPOSIT", float(base_amount), product=order_id, gateway="FamPay")
                        log_activity(user_id, "DEPOSIT_SUCCESS", f"Amount: {base_amount}, Gateway: FamPay, Order: {order_id}, UTR: {data.get('utr', '')}")
                    else:
                        msg = "✅ Payment already processed. Your wallet was not credited twice."
                        if isinstance(reply_target, CallbackQuery): await reply_target.answer(msg, show_alert=True)
                        else: await reply_target.answer(msg)
                    return

                if resp.status == 408 or res_json.get("status") == "expired":
                    db_query("UPDATE transactions SET status='expired' WHERE order_id=? AND status='pending'", (order_id,))
                    msg = "⏳ <b>FamPay Order Expired</b>\nPlease create a new payment request."
                elif resp.status == 429:
                    msg = "⏳ FamPay rate limit reached. Please wait a few seconds before verifying again."
                elif res_json.get("status") in {"pending", "processing"}:
                    msg = "⏳ Payment is still pending at FamPay. Please wait and verify again."
                else:
                    msg = f"⚠️ FamPay Gateway: {res_json.get('message', 'Payment not confirmed yet.')}"
                if isinstance(reply_target, CallbackQuery): await reply_target.answer(msg, show_alert=True)
                else: await reply_target.answer(msg)
        except Exception as e:
            logger.error(f"FamPay API Error: {e}")
            msg = "⚠️ Unable to connect to FamPay Gateway right now. Please try again shortly."
            if isinstance(reply_target, CallbackQuery): await reply_target.answer(msg, show_alert=True)
            else: await reply_target.answer(msg)

async def auto_verify_task() -> None:
    while True:
        await asyncio.sleep(20)
        api_key = get_setting("fampay_api_key", "").strip()
        if not api_key:
            continue
        pending_txns = db_query("SELECT order_id, user_id, amount_inr, timestamp, COALESCE(gateway_amount, 0) FROM transactions WHERE status='pending' AND COALESCE(purpose,'deposit')='deposit' ORDER BY timestamp ASC LIMIT 5", fetchall=True) or []
        if not pending_txns:
            continue
        async with aiohttp.ClientSession() as session:
            for order_id, user_id, amount, ts, gateway_amount in pending_txns:
                if time.time() - ts > FAMPAY_ORDER_TTL:
                    db_query("UPDATE transactions SET status='expired' WHERE order_id=? AND status='pending'", (order_id,))
                    try: await bot.send_message(user_id, f"⏳ <b>FamPay Order Expired!</b>\nOrder <code>{order_id}</code> was not paid within 5 minutes.", parse_mode='HTML')
                    except Exception: pass
                    continue
                try:
                    async with session.get(get_fampay_verify_url(), params={"api_key": api_key, "order_id": order_id}, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                        try: res_json = await resp.json(content_type=None)
                        except Exception: res_json = {}
                        if resp.status == 200 and res_json.get("status") == "success":
                            data = res_json.get("data") or {}
                            paid_amount = float(data.get("amount") or 0)
                            if gateway_amount and paid_amount and abs(paid_amount - float(gateway_amount)) > 0.011:
                                logger.warning(f"FamPay amount mismatch for {order_id}: expected {gateway_amount}, got {paid_amount}")
                                continue
                            if mark_transaction_paid_once(order_id, user_id, float(amount)):
                                try:
                                    await bot.send_message(user_id, f"✨ <b>FAMPAY AUTO-VERIFIED!</b>\n\n✅ {fmt_curr(amount)} has been added to your balance.\n🧾 Order: <code>{order_id}</code>", parse_mode='HTML')
                                except Exception: pass
                                await send_advanced_notification(user_id, "DEPOSIT", float(amount), product=order_id, gateway="FamPay Auto")
                                log_activity(user_id, "DEPOSIT_AUTO_SUCCESS", f"Amount: {amount}, Gateway: FamPay Auto, Order: {order_id}, UTR: {data.get('utr', '')}")
                        elif resp.status == 408 or res_json.get("status") == "expired":
                            db_query("UPDATE transactions SET status='expired' WHERE order_id=? AND status='pending'", (order_id,))
                except Exception as e:
                    logger.debug(f"FamPay auto-verify exception for {order_id}: {e}")

# ==============================================================================
# 10. ONBOARDING & START
# ==============================================================================
def _shop_license_locked_for_user(user_id: int) -> bool:
    """True when this generated shop has no active admin license.
    The bot keeps polling so the configured admin can renew/activate a license,
    but regular users are shown a maintenance/locked message until renewal.
    """
    if not LICENSE_ENFORCED:
        return False
    if not is_configured_admin(user_id):
        return not _license_active(ADMIN_ID)
    return not _license_active(user_id)

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    # Admin license gates only the Admin Panel. Customers can use the shop.
    if LICENSE_ENFORCED and is_configured_admin(message.from_user.id) and not _license_active(message.from_user.id):
        return await message.answer(
            "🔒 <b>ADMIN PANEL LICENSE REQUIRED</b>\n\n"
            "Customers can use the Shop normally. Your Admin Panel is locked until a valid OBITO ADMIN license key is activated.\n\n"
            "🔑 If you already have a key: <code>/activate YOUR-KEY</code>.\n"
            "💳 Otherwise use the Maker Bot <b>Buy Key</b> flow to pay and receive a key.",
            reply_markup=license_buy_kb(), parse_mode='HTML')
    try: await message.answer_sticker(WELCOME_STICKER_ID)
    except: pass 
    
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("v_"):
        order_id = args[1].split("v_")[1]
        msg = await message.answer("🔄 <b>Verifying your payment securely...</b>\n<i>Connecting to gateway...</i>", parse_mode='HTML')
        await run_payment_verification(message.from_user.id, order_id, msg)
        return

    if len(args) > 1 and args[1].startswith("lic_"):
        order_id = args[1].split("lic_",1)[1]
        if is_configured_admin(message.from_user.id):
            msg = await message.answer("🔄 <b>Verifying your license payment...</b>\n<i>Connecting to gateway...</i>", parse_mode='HTML')
            await _verify_license_payment(message.from_user.id, order_id, msg)
            return

    referred_by = None
    if len(args) > 1 and args[1].startswith("ref_"):
        try: referred_by = int(args[1].split("_")[1])
        except: pass

    user = db_query("SELECT phone, education FROM users WHERE user_id=?", (message.from_user.id,), fetchone=True)
    current_username = message.from_user.username or ""
    db_query("UPDATE users SET username=? WHERE user_id=?", (current_username, message.from_user.id))

    if not user or not user[0]:
        db_query("INSERT OR IGNORE INTO users (user_id, first_name, username, referred_by, joined_date) VALUES (?, ?, ?, ?, ?)", 
                 (message.from_user.id, message.from_user.first_name, current_username, referred_by, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        log_activity(message.from_user.id, "ACCOUNT_CREATED")
        await message.answer("<b>🛡 VERIFICATION REQUIRED</b>\n\nTo safeguard your orders and account, we need to verify you.\n👇 <b>Tap the button below:</b>", parse_mode='HTML', reply_markup=contact_kb())
    else:
        log_activity(message.from_user.id, "CMD_START")
        if not user[1]:
            await state.set_state(UserStates.onboarding_education)
            await message.answer("🎓 <b>Profile Verification — Step 2/2</b>\n\nPlease send your <b>Education / Class / Work</b>.\nExample: <code>12th</code>, <code>College</code>, <code>Job</code>.", parse_mode="HTML")
            return
        await send_main_menu(message)

@dp.message(F.contact)
async def handle_contact(message: Message, state: FSMContext):
    if message.contact.user_id == message.from_user.id:
        db_query("UPDATE users SET phone=? WHERE user_id=?", (message.contact.phone_number, message.from_user.id))
        referrer = db_query("SELECT referred_by FROM users WHERE user_id=?", (message.from_user.id,), fetchone=True)
        if referrer and referrer[0]:
            db_query("UPDATE users SET referrals_count = referrals_count + 1 WHERE user_id=?", (referrer[0],))
            try: await bot.send_message(referrer[0], f"🎉 <b>Referral Success!</b>\nUser <b>{message.from_user.first_name}</b> joined using your link!", parse_mode='HTML')
            except: pass
        log_activity(message.from_user.id, "CONTACT_VERIFIED")
        await state.set_state(UserStates.onboarding_education)
        await message.answer("✅ Phone verified.\n\n🎓 <b>Step 2/2:</b> Send your <b>Education / Class / Work</b>.\nExample: <code>12th</code>, <code>College</code>, <code>Job</code>.", reply_markup=ReplyKeyboardRemove(), parse_mode="HTML")
    else:
        await message.answer("❌ Security Alert: Please share your OWN contact using the provided button.")

@dp.message(UserStates.onboarding_education)
async def handle_education(message: Message, state: FSMContext):
    value=(message.text or "").strip()
    if value.lower() == "/cancel":
        await state.clear(); return await message.answer("Cancelled.")
    if len(value) < 2 or len(value) > 80:
        return await message.answer("❌ Please send a valid Education / Class / Work value (2–80 characters).")
    db_query("UPDATE users SET education=? WHERE user_id=?", (value, message.from_user.id))
    await state.clear()
    log_activity(message.from_user.id, "EDUCATION_VERIFIED", value)
    await message.answer("✅ <b>Verification complete!</b> Welcome to the store.", parse_mode="HTML")
    await send_main_menu(message)

async def send_main_menu(ctx: Any):
    text = get_ui_text("start_menu")
    kb = main_menu_kb(ctx.from_user.id)
    if isinstance(ctx, Message): 
        await ctx.answer(text, reply_markup=kb, parse_mode='HTML')
    else: 
        await ctx.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "back_main")
async def back_main(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    await state.clear()
    log_activity(call.from_user.id, "RETURN_MAIN_MENU")
    await send_main_menu(call)

# ==============================================================================
# 11. ADD BALANCE
# ==============================================================================
@dp.callback_query(F.data == "menu_add_balance")
async def select_gateway_menu(call: CallbackQuery):
    await safe_call_answer(call)
    log_activity(call.from_user.id, "VIEW_ADD_BALANCE")
    text = get_ui_text("add_balance_menu")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 FAMPAY PAY", callback_data="gateway_fampay", icon_custom_emoji_id=get_emoji_icon("upi"), style="primary")],
        [InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

# ==============================================================================
# 12. FAMPAY PAYMENT FLOW
# ==============================================================================
@dp.callback_query(F.data == "gateway_fampay")
async def add_balance_fampay(call: CallbackQuery):
    await safe_call_answer(call)
    text = "💳 <b>— FAMPAY PAYMENT —</b> 💳\n\nSelect amount to add to your wallet:"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="₹50", callback_data="pay_50", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="primary"), InlineKeyboardButton(text="₹100", callback_data="pay_100", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="primary")],
        [InlineKeyboardButton(text="₹200", callback_data="pay_200", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="primary"), InlineKeyboardButton(text="₹500", callback_data="pay_500", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="primary")],
        [InlineKeyboardButton(text="✏️ Custom Amount", callback_data="custom_deposit_keypad", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
        [InlineKeyboardButton(text="Back", callback_data="menu_add_balance", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "custom_deposit_keypad")
async def show_custom_keypad(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    await state.set_state(UserStates.custom_amount_input)
    await state.update_data(amount_str="0")
    await show_keypad(call.message)

async def show_keypad(message: Message, amount_str: str = "0"):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="      1      ", callback_data="kp_1", style="primary"), InlineKeyboardButton(text="      2      ", callback_data="kp_2", style="primary"), InlineKeyboardButton(text="      3      ", callback_data="kp_3", style="primary")],
        [InlineKeyboardButton(text="      4      ", callback_data="kp_4", style="primary"), InlineKeyboardButton(text="      5      ", callback_data="kp_5", style="primary"), InlineKeyboardButton(text="      6      ", callback_data="kp_6", style="primary")],
        [InlineKeyboardButton(text="      7      ", callback_data="kp_7", style="primary"), InlineKeyboardButton(text="      8      ", callback_data="kp_8", style="primary"), InlineKeyboardButton(text="      9      ", callback_data="kp_9", style="primary")],
        [InlineKeyboardButton(text="    ⌫    ", callback_data="kp_backspace", style="danger"), InlineKeyboardButton(text="      0      ", callback_data="kp_0", style="primary"), InlineKeyboardButton(text="    C    ", callback_data="kp_clear", style="danger")],
        [InlineKeyboardButton(text=f"✅ Confirm (₹{amount_str})", callback_data="kp_confirm", icon_custom_emoji_id=get_emoji_icon("check_icon"), style="success")],
        [InlineKeyboardButton(text="Cancel", callback_data="gateway_fampay", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await message.edit_text(f"💳 <b>Enter FamPay Amount (₹):</b>\n\nCurrent: ₹{amount_str}", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("kp_"), UserStates.custom_amount_input)
async def keypad_handler(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    amount_str = data.get("amount_str", "0")
    action = call.data.split("_")[1]
    if action == "confirm":
        if amount_str == "0":
            await call.answer("Amount cannot be zero.", show_alert=True)
            return
        try:
            amount = float(amount_str)
            if amount < 10:
                await call.answer("Minimum deposit is ₹10.", show_alert=True)
                return
            await state.clear()
            await call.message.edit_text("⏳ <b>Creating secure FamPay payment...</b>", parse_mode='HTML')
            await generate_fampay_order(call.from_user.id, amount, call.message)
        except ValueError:
            await call.answer("Invalid amount.", show_alert=True)
        return
    if action == "backspace":
        amount_str = amount_str[:-1] if len(amount_str) > 1 else "0"
    elif action == "clear":
        amount_str = "0"
    else:
        amount_str = action if amount_str == "0" else amount_str + action
        amount_str = amount_str[:6]
    await state.update_data(amount_str=amount_str)
    await show_keypad(call.message, amount_str)
    await call.answer()

@dp.callback_query(F.data.startswith("pay_"))
async def process_fampay_payment_callback(call: CallbackQuery):
    try: inr_amount = float(call.data.split("_")[1])
    except (ValueError, IndexError):
        await call.answer("Invalid amount.", show_alert=True)
        return
    await call.message.edit_text("⏳ <b>Creating secure FamPay payment...</b>", parse_mode='HTML')
    await generate_fampay_order(call.from_user.id, inr_amount, call.message)

async def generate_fampay_order(user_id: int, inr_amount: float, message_obj: Message) -> None:
    api_key = get_setting("fampay_api_key", "").strip()
    if not api_key:
        return await message_obj.edit_text("⚠️ <b>FamPay Gateway is not configured.</b>\nAdmin must set the FamPay API key first.", reply_markup=back_kb("gateway_fampay"), parse_mode='HTML')

    current_time = int(time.time())
    order_id = f"FAM{user_id}{current_time}{random.randint(1000, 9999)}"
    bot_deep_link = f"https://t.me/{BOT_USERNAME}?start=v_{order_id}"
    db_query("INSERT INTO transactions (order_id, user_id, amount_inr, gateway_amount, status, timestamp, purpose, product_id) VALUES (?, ?, ?, ?, 'pending', ?, 'deposit', NULL)", (order_id, user_id, inr_amount, inr_amount, current_time))

    try:
        async with aiohttp.ClientSession() as session:
            params = {"api_key": api_key, "amount": f"{inr_amount:.2f}", "redirect_url": bot_deep_link}
            async with session.get(get_fampay_create_url(), params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                try: res_data = await resp.json(content_type=None)
                except Exception: res_data = {}
                if resp.status != 200 or res_data.get("status") != "success":
                    db_query("UPDATE transactions SET status='failed' WHERE order_id=? AND status='pending'", (order_id,))
                    return await message_obj.edit_text(f"❌ <b>FamPay Gateway Error:</b> {res_data.get('message', f'HTTP {resp.status}')}", reply_markup=back_kb("gateway_fampay"), parse_mode='HTML')
                data = res_data.get("data") or {}
                gateway_order_id = data.get("order_id") or order_id
                qr_url = data.get("qr_url") or ""
                checkout_url = data.get("checkout_url") or qr_url
                payable_amount = float(data.get("payable_amount") or inr_amount)
                expires_at = data.get("expires_at_ist") or "5 minutes"
                if gateway_order_id != order_id:
                    db_query("UPDATE transactions SET order_id=?, gateway_amount=? WHERE order_id=?", (gateway_order_id, payable_amount, order_id))
                    order_id = gateway_order_id
                else:
                    db_query("UPDATE transactions SET gateway_amount=? WHERE order_id=?", (payable_amount, order_id))
                if not checkout_url:
                    db_query("UPDATE transactions SET status='failed' WHERE order_id=? AND status='pending'", (order_id,))
                    return await message_obj.edit_text("❌ FamPay did not return a payment URL. Please try again.", reply_markup=back_kb("gateway_fampay"), parse_mode='HTML')
                rows = [[InlineKeyboardButton(text="💳 Pay with FamPay", url=checkout_url, style="success")]]
                if qr_url and qr_url != checkout_url:
                    rows.append([InlineKeyboardButton(text="📷 Open QR", url=qr_url, style="primary")])
                rows += [[InlineKeyboardButton(text="🔄 Verify Payment", callback_data=f"verify_{order_id}", icon_custom_emoji_id=get_emoji_icon("check_icon"), style="primary")], [InlineKeyboardButton(text="Cancel Transaction", callback_data="menu_add_balance", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]]
                text = (f"🧾 <b>FAMPAY PAYMENT CREATED</b>\n\n💰 Wallet Credit: <b>{fmt_curr(inr_amount)}</b>\n💳 Exact Payable Amount: <b>₹{payable_amount:.2f}</b>\n🆔 Order ID: <code>{order_id}</code>\n⏳ Expires: <b>{expires_at}</b>\n\n"
                        "1️⃣ Tap <b>Pay with FamPay</b>.\n2️⃣ Complete the payment for the exact amount shown.\n3️⃣ Return here and tap <b>Verify Payment</b>.\n\n"
                        "🔒 Duplicate verification is blocked; one successful payment can credit the wallet only once.")
                log_activity(user_id, "GENERATE_FAMPAY_INVOICE", f"Amount: {inr_amount}, Payable: {payable_amount}, Order: {order_id}")
                await message_obj.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode='HTML')
    except Exception as e:
        db_query("UPDATE transactions SET status='failed' WHERE order_id=? AND status='pending'", (order_id,))
        logger.error(f"FamPay create-order error: {e}")
        await message_obj.edit_text("❌ <b>FamPay connection error.</b> Please try again later.", reply_markup=back_kb("gateway_fampay"), parse_mode='HTML')

@dp.callback_query(F.data.startswith("verify_"))
async def manual_verify_callback(call: CallbackQuery):
    await safe_call_answer(call)
    await run_payment_verification(call.from_user.id, call.data.split("_", 1)[1], call)

# ==============================================================================
# 13. SHOP – with uppercase categories and new point_down emoji
# ==============================================================================
@dp.callback_query(F.data == "menu_shop")
async def view_shop_products(call: CallbackQuery):
    try:
        await call.answer()
    except Exception:
        pass
    """Customer store: category-first navigation, then product group, then duration."""
    log_activity(call.from_user.id, "VIEW_SHOP")
    cats = db_query("""
        SELECT DISTINCT category FROM products
        WHERE is_active=1 AND COALESCE(category,'')<>''
        ORDER BY category COLLATE NOCASE
    """, fetchall=True) or []
    # Always show the six standard categories. Empty categories remain visible so
    # admins/customers can see the intended store structure before products are added.
    ordered=list(FIXED_CATEGORIES)
    for (cat,) in cats:
        cat=(cat or '').strip()
        if cat and cat.upper() not in ordered: ordered.append(cat.upper())
    if not ordered:
        return await call.message.edit_text(
            f"{get_emoji('product_store')} <b><u>PRODUCT STORE</u></b>\n━━━━━━━━━━━━━━━━━━\n\n⚠️ <b>No categories/products are available right now.</b>",
            reply_markup=back_kb("back_main"), parse_mode='HTML')
    kb=InlineKeyboardMarkup(inline_keyboard=[])
    for cat in ordered:
        count=db_query("SELECT COUNT(*) FROM products WHERE is_active=1 AND UPPER(category)=?",(cat,),fetchone=True)[0]
        emoji_id = get_category_emoji(cat)
        kb.inline_keyboard.append([InlineKeyboardButton(
            text=category_display_name(cat),
            callback_data=f"cat_{urllib.parse.quote(cat, safe='')}",
            icon_custom_emoji_id=emoji_id or None,
            style='success')])
    kb.inline_keyboard.append([InlineKeyboardButton(text="🔙 BACK",callback_data="back_main",icon_custom_emoji_id=get_emoji_icon('back'),style='danger')])
    await call.message.edit_text(
        f"{get_emoji('product_store')} <b><u>PRODUCT STORE</u></b>\n━━━━━━━━━━━━━━━━━━\n\n📂 <b>Select Category:</b>\n\n• Android Non Root\n• Android Root\n• iPhone\n• PC\n• Guild Gallery / Credit\n• Carrom",
        reply_markup=kb,parse_mode='HTML')

@dp.callback_query(F.data.startswith("cat_"))
async def view_products_for_category(call: CallbackQuery):
    try:
        await call.answer()
    except Exception:
        pass
    try:
        category=urllib.parse.unquote(call.data.split("_",1)[1])
    except Exception:
        return await call.answer("❌ Invalid category.",show_alert=True)
    groups=db_query("""
        SELECT MIN(id), COALESCE(NULLIF(product_group,''),name)
        FROM products WHERE is_active=1 AND UPPER(category)=?
        GROUP BY COALESCE(NULLIF(product_group,''),name)
        ORDER BY 2 COLLATE NOCASE
    """,(category.upper(),),fetchall=True) or []
    if not groups: return await call.answer("❌ No products in this category.",show_alert=True)
    kb=InlineKeyboardMarkup(inline_keyboard=[])
    for rep_id,group_name in sorted(groups,key=lambda r:natural_sort_key(r[1])):
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"📦 {group_name}",callback_data=f"product_group_{rep_id}",style='success')])
    kb.inline_keyboard.append([InlineKeyboardButton(text="🔙 Back to Categories",callback_data="menu_shop",icon_custom_emoji_id=get_emoji_icon('back'),style='danger')])
    await call.message.edit_text(f"📂 <b>{category}</b>\n━━━━━━━━━━━━━━━━━━\n\n📦 <b>Select Product:</b>",reply_markup=kb,parse_mode='HTML')

@dp.callback_query(F.data.startswith("pnl_"))
async def legacy_panel_callback(call: CallbackQuery):
    await safe_call_answer(call)
    # Older buttons remain compatible by opening the category list.
    await view_shop_products(call)

@dp.callback_query(F.data.startswith("product_group_"))
async def view_product_durations(call: CallbackQuery):
    try:
        await call.answer()
    except Exception:
        pass
    try: rep_id=int(call.data.split("_",2)[2])
    except (ValueError,IndexError): return await call.answer("❌ Invalid product.", show_alert=True)
    group_row=db_query("SELECT COALESCE(NULLIF(product_group,''),name), category FROM products WHERE id=?",(rep_id,),fetchone=True)
    if not group_row: return await call.answer("❌ Product not found.",show_alert=True)
    group_name,category=group_row
    prods=db_query("""SELECT id,name,price_inr,stock,reseller_price,validity,device_limit,external_enabled
                     FROM products WHERE is_active=1 AND COALESCE(NULLIF(product_group,''),name)=? AND UPPER(category)=?
                     """,(group_name,(category or '').upper()),fetchall=True) or []
    if not prods: return await call.answer("❌ No durations available.",show_alert=True)
    user=db_query("SELECT is_reseller,is_vip FROM users WHERE user_id=?",(call.from_user.id,),fetchone=True)
    is_reseller=bool(user[0]) if user else False; is_vip=bool(user[1]) if user else False
    kb=InlineKeyboardMarkup(inline_keyboard=[])
    for p in sorted(prods,key=lambda r:(natural_sort_key(r[5] or r[1]),natural_sort_key(r[1]))):
        prod_id,pname,normal_price,stock,reseller_price,validity,device,external=p
        base=float(reseller_price or 0) if is_reseller and float(reseller_price or 0)>0 else float(normal_price or 0)
        price=base-(base*VIP_DISCOUNT_PERCENTAGE/100) if is_vip else base
        available=bool(external) or (stock is not None and stock>0)
        label=validity or pname
        if available: kb.inline_keyboard.append([InlineKeyboardButton(text=f"⏱ {label} • {fmt_curr(price)}",callback_data=f"choosebuy_{prod_id}",style='success')])
        else: kb.inline_keyboard.append([InlineKeyboardButton(text=f"❌ {label} • Out of Stock",callback_data="ignore_stock_click",style='danger')])
    kb.inline_keyboard.append([InlineKeyboardButton(text="🔙 Back to Products",callback_data=f"cat_{urllib.parse.quote(str(category), safe='')}",icon_custom_emoji_id=get_emoji_icon('back'),style='danger')])
    await call.message.edit_text(f"📦 <b>{group_name}</b>\n━━━━━━━━━━━━━━━━━━\n\n⏱ <b>Select validity / duration:</b>",reply_markup=kb,parse_mode='HTML')

@dp.callback_query(F.data.startswith("choosebuy_"))
async def choose_product_payment(call: CallbackQuery):
    try:
        await call.answer()
    except Exception:
        pass
    try: prod_id=int(call.data.split("_",1)[1])
    except: return await call.answer("❌ Invalid product.",show_alert=True)
    prod=db_query("SELECT name,price_inr,stock,external_enabled FROM products WHERE id=? AND is_active=1",(prod_id,),fetchone=True)
    if not prod: return await call.answer("❌ Product not found.",show_alert=True)
    if not bool(prod[3]) and (prod[2] is None or prod[2] <= 0): return await call.answer("❌ This product is out of stock.",show_alert=True)
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Buy 1 Key",callback_data=f"buyqty_{prod_id}_1",style='success'), InlineKeyboardButton(text="🛒 Buy 2 Keys",callback_data=f"buyqty_{prod_id}_2",style='success')],
        [InlineKeyboardButton(text="🛒 Buy 3 Keys",callback_data=f"buyqty_{prod_id}_3",style='success'), InlineKeyboardButton(text="🛒 Buy 4 Keys",callback_data=f"buyqty_{prod_id}_4",style='success')],
        [InlineKeyboardButton(text="🛒 Buy 5 Keys",callback_data=f"buyqty_{prod_id}_5",style='success')],
        [InlineKeyboardButton(text="🔙 Back",callback_data="menu_shop",style='danger')]
    ])
    await call.message.edit_text(f"🛒 <b>{prod[0]}</b>\n\nSelect quantity: <b>1–5 Keys</b>.",reply_markup=kb,parse_mode='HTML')

@dp.callback_query(F.data.startswith("buyqty_"))
async def select_product_quantity(call: CallbackQuery):
    try:
        await call.answer()
    except Exception:
        pass
    try: _,pid,qty=call.data.split('_'); prod_id=int(pid); qty=int(qty)
    except: return await call.answer("❌ Invalid quantity.",show_alert=True)
    if qty not in (1,2,3,4,5): return await call.answer("❌ Invalid quantity.",show_alert=True)
    prod=db_query("SELECT name,price_inr,stock,reseller_price,external_enabled,requires_android_id FROM products WHERE id=? AND is_active=1",(prod_id,),fetchone=True)
    user=db_query("SELECT balance,is_reseller,is_vip FROM users WHERE user_id=?",(call.from_user.id,),fetchone=True)
    if not prod or not user: return await call.answer("❌ Product/account not found.",show_alert=True)
    if not bool(prod[4]) and (prod[2] is None or prod[2] < qty): return await call.answer("❌ Not enough stock.",show_alert=True)
    base=float(prod[3] or 0) if bool(user[1]) and float(prod[3] or 0)>0 else float(prod[1] or 0)
    unit=base-(base*VIP_DISCOUNT_PERCENTAGE/100) if bool(user[2]) else base; total=unit*qty
    rows=[]
    if float(user[0] or 0)>=total: rows.append([InlineKeyboardButton(text=f"💰 Wallet • {fmt_curr(total)}",callback_data=f"buywallet_{prod_id}_{qty}",style='success')])
    rows.append([InlineKeyboardButton(text=f"💳 Direct Payment • {fmt_curr(total)}",callback_data=f"buydirect_{prod_id}_{qty}",style='primary')])
    rows.append([InlineKeyboardButton(text="🔙 Change Quantity",callback_data=f"choosebuy_{prod_id}",style='danger')])
    await call.message.edit_text(f"🛒 <b>{prod[0]}</b>\n\n🔢 Quantity: <b>{qty}</b>\n💵 Total: <b>{fmt_curr(total)}</b>\n💰 Wallet: <b>{fmt_curr(float(user[0] or 0))}</b>\n\nChoose payment method:",reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),parse_mode='HTML')

async def _product_requires_android(prod_id:int)->bool:
    row=db_query("SELECT external_enabled,requires_android_id FROM products WHERE id=? AND is_active=1",(prod_id,),fetchone=True)
    return bool(row and row[0] and row[1])

async def _ask_purchase_android(call: CallbackQuery, state: FSMContext, prod_id:int, method:str, quantity:int=1):
    await state.update_data(purchase_product_id=prod_id,purchase_method=method,purchase_quantity=quantity)
    await call.message.edit_text("📱 <b>Android ID Required</b>\n\nSend the customer's Android ID. It will only be used for this product purchase.",reply_markup=admin_back_kb(),parse_mode='HTML')
    await state.set_state(UserStates.purchase_android_id)

@dp.message(UserStates.purchase_android_id)
async def purchase_android_id_received(m: Message,state: FSMContext):
    android_id=m.text.strip()
    if len(android_id)<4 or len(android_id)>128 or any(ch.isspace() for ch in android_id):
        return await m.answer("❌ Invalid Android ID. Send the exact Android ID without spaces.")
    data=await state.get_data(); prod_id=int(data.get('purchase_product_id',0)); method=data.get('purchase_method'); quantity=int(data.get('purchase_quantity',1) or 1)
    await state.clear()
    if method=='wallet':
        await execute_wallet_purchase(m,prod_id,android_id,quantity)
    else:
        await create_direct_product_payment(m,prod_id,android_id,quantity)

@dp.callback_query(F.data.startswith("buywallet_"))
async def wallet_product_purchase(call: CallbackQuery,state: FSMContext):
    try:
        parts=call.data.split('_'); prod_id=int(parts[1]); quantity=int(parts[2]) if len(parts)>2 else 1
        if quantity not in (1,2,3,4,5): raise ValueError
    except: return await call.answer("❌ Invalid purchase quantity.",show_alert=True)
    if await _product_requires_android(prod_id): return await _ask_purchase_android(call,state,prod_id,'wallet',quantity)
    await execute_wallet_purchase(call,prod_id,'',quantity)

async def create_direct_product_payment(call_or_message, prod_id:int, android_id:str='', quantity:int=1):
    user_id=call_or_message.from_user.id
    _purchase_lock = await _purchase_gate(user_id)
    if _purchase_lock is None:
        if isinstance(call_or_message, CallbackQuery):
            return await call_or_message.answer("⏳ Your previous purchase action is still processing. Please wait.", show_alert=True)
        return await call_or_message.answer("⏳ Your previous purchase action is still processing. Please wait.")
    try:
        return await _create_direct_product_payment_locked(call_or_message, prod_id, android_id, quantity)
    finally:
        _purchase_lock.release()

async def _create_direct_product_payment_locked(call_or_message, prod_id:int, android_id:str='', quantity:int=1):
    user_id=call_or_message.from_user.id
    prod=db_query("SELECT name, price_inr, reseller_price, is_active FROM products WHERE id=?",(prod_id,),fetchone=True)
    user=db_query("SELECT is_reseller,is_vip FROM users WHERE user_id=?",(user_id,),fetchone=True)
    if not prod or not user or not prod[3]:
        if isinstance(call_or_message,CallbackQuery): return await call_or_message.answer("❌ Product not found.",show_alert=True)
        return await call_or_message.answer("❌ Product not found.")
    normal=float(prod[1] or 0); reseller=float(prod[2] or 0); base=reseller if bool(user[0]) and reseller>0 else normal
    price=base-(base*VIP_DISCOUNT_PERCENTAGE/100) if bool(user[1]) else base
    quantity=max(1,min(5,int(quantity or 1)))
    price*=quantity
    api_key=get_setting("fampay_api_key","").strip()
    if not api_key:
        if isinstance(call_or_message,CallbackQuery): return await call_or_message.answer("⚠️ Payment gateway is not configured by the owner.",show_alert=True)
        return await call_or_message.answer("⚠️ Payment gateway is not configured by the owner.")
    target=call_or_message.message if isinstance(call_or_message,CallbackQuery) else call_or_message
    if isinstance(call_or_message,CallbackQuery): await call_or_message.answer()
    await target.edit_text("⏳ <b>Creating secure payment...</b>",parse_mode='HTML')
    current_time=int(time.time()); order_id=f"PRD{user_id}{current_time}{random.randint(1000,9999)}"
    db_query("INSERT INTO transactions (order_id,user_id,amount_inr,gateway_amount,status,timestamp,purpose,product_id,android_id,quantity) VALUES (?,?,?,?,?,?,?,?,?,?)",(order_id,user_id,price,price,'pending',current_time,'product',prod_id,android_id,quantity))
    try:
        async with aiohttp.ClientSession() as session:
            params={"api_key":api_key,"amount":f"{price:.2f}","redirect_url":f"https://t.me/{BOT_USERNAME}?start=pv_{order_id}"}
            async with session.get(get_fampay_create_url(),params=params,timeout=aiohttp.ClientTimeout(total=15)) as resp:
                try: data_json=await resp.json(content_type=None)
                except Exception: data_json={}
                if resp.status!=200 or data_json.get('status')!='success':
                    db_query("UPDATE transactions SET status='failed' WHERE order_id=? AND status='pending'",(order_id,))
                    return await target.edit_text(f"❌ <b>Gateway Error:</b> {data_json.get('message',f'HTTP {resp.status}')}",reply_markup=back_kb("menu_shop"),parse_mode='HTML')
                data=data_json.get('data') or {}; gateway_order=data.get('order_id') or order_id; payable=float(data.get('payable_amount') or price); checkout=data.get('checkout_url') or data.get('qr_url') or ''; qr=data.get('qr_url') or ''
                if gateway_order!=order_id:
                    db_query("UPDATE transactions SET order_id=?,gateway_amount=? WHERE order_id=?",(gateway_order,payable,order_id)); order_id=gateway_order
                else: db_query("UPDATE transactions SET gateway_amount=? WHERE order_id=?",(payable,order_id))
                if not checkout:
                    db_query("UPDATE transactions SET status='failed' WHERE order_id=? AND status='pending'",(order_id,)); return await target.edit_text("❌ Gateway did not return a payment link.",reply_markup=back_kb("menu_shop"),parse_mode='HTML')
                rows=[[InlineKeyboardButton(text="💳 PAY NOW",url=checkout,style="success")]]
                if qr and qr!=checkout: rows.append([InlineKeyboardButton(text="📷 OPEN QR",url=qr,style="primary")])
                rows.append([InlineKeyboardButton(text="🔄 VERIFY PAYMENT",callback_data=f"verify_product_{order_id}",style="primary")]); rows.append([InlineKeyboardButton(text="❌ CANCEL",callback_data="menu_shop",style="danger")])
                text=f"🧾 <b>DIRECT PAYMENT CREATED</b>\n\n📦 <b>{prod[0]}</b>\n💳 Payable: <b>₹{payable:.2f}</b>\n🆔 Order: <code>{order_id}</code>\n⏳ Expires in about 5 minutes.\n\n1️⃣ Tap PAY NOW\n2️⃣ Complete payment\n3️⃣ Tap VERIFY PAYMENT"
                await target.edit_text(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),parse_mode='HTML')
    except Exception as e:
        db_query("UPDATE transactions SET status='failed' WHERE order_id=? AND status='pending'",(order_id,)); logger.exception("Direct product payment error: %s",e)
        await target.edit_text("❌ Payment gateway connection error. Please try again.",reply_markup=back_kb("menu_shop"),parse_mode='HTML')

@dp.callback_query(F.data.startswith("buydirect_"))
async def direct_product_payment(call: CallbackQuery,state: FSMContext):
    try:
        parts=call.data.split('_'); prod_id=int(parts[1]); quantity=int(parts[2]) if len(parts)>2 else 1
        if quantity not in (1,2,3,4,5): raise ValueError
    except: return await call.answer("❌ Invalid purchase quantity.",show_alert=True)
    if await _product_requires_android(prod_id): return await _ask_purchase_android(call,state,prod_id,'direct',quantity)
    await create_direct_product_payment(call,prod_id,'',quantity)

@dp.callback_query(F.data.startswith("verify_product_"))
async def verify_direct_product_payment(call: CallbackQuery):
    order_id = call.data.split("verify_product_",1)[1]
    txn = db_query("SELECT amount_inr,status,timestamp,COALESCE(gateway_amount,0),product_id,purpose,COALESCE(android_id,''),COALESCE(quantity,1) FROM transactions WHERE order_id=? AND user_id=?", (order_id,call.from_user.id), fetchone=True)
    if not txn or txn[5] != 'product' or not txn[4]:
        return await call.answer("❌ Invalid product payment order.", show_alert=True)
    amount,status,created,gateway_amount,prod_id,_,android_id,quantity = txn
    if status == 'paid':
        return await call.answer("✅ This payment was already processed.", show_alert=True)
    if status in ('failed','expired') or time.time()-created > FAMPAY_ORDER_TTL:
        db_query("UPDATE transactions SET status='expired' WHERE order_id=? AND status='pending'", (order_id,))
        return await call.answer("⏳ Payment order expired. Create a new payment.", show_alert=True)
    api_key=get_setting("fampay_api_key","").strip()
    if not api_key: return await call.answer("⚠️ Payment gateway is not configured.", show_alert=True)
    await call.answer("🔄 Verifying payment...", show_alert=False)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(get_fampay_verify_url(), params={"api_key":api_key,"order_id":order_id}, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                try: res=await resp.json(content_type=None)
                except Exception: res={}
        if resp.status != 200 or res.get("status") != "success":
            if resp.status == 408 or res.get("status") == "expired":
                db_query("UPDATE transactions SET status='expired' WHERE order_id=? AND status='pending'",(order_id,))
                return await call.message.edit_text("⏳ <b>Payment Expired</b>\nPlease create a new payment.",reply_markup=back_kb("menu_shop"),parse_mode='HTML')
            return await call.answer(res.get("message","Payment not confirmed yet. Please try again."), show_alert=True)
        data=res.get("data") or {}
        paid=float(data.get("amount") or 0)
        if gateway_amount and paid and abs(paid-float(gateway_amount))>0.011:
            return await call.answer("⚠️ Payment amount mismatch. Product was NOT delivered.", show_alert=True)
        # Atomically claim the gateway transaction so repeated taps cannot double-deliver.
        conn=sqlite3.connect(DB_FILE)
        try:
            c=conn.cursor(); c.execute('BEGIN IMMEDIATE')
            c.execute("UPDATE transactions SET status='paid' WHERE order_id=? AND user_id=? AND status='pending' AND purpose='product'",(order_id,call.from_user.id))
            if c.rowcount != 1:
                conn.rollback(); return await call.answer("✅ Payment already processed.",show_alert=True)
            conn.commit()
        finally: conn.close()
        # Reuse the fully tested wallet purchase engine: gateway payment is first
        # converted into an internal balance credit, then immediately consumed by the product.
        db_query("UPDATE users SET balance=balance+? WHERE user_id=?",(float(amount),call.from_user.id))
        await call.message.edit_text("✅ <b>Payment verified.</b>\n⏳ Delivering your product...",parse_mode='HTML')
        await execute_wallet_purchase(call, int(prod_id), android_id, int(quantity or 1))
        log_activity(call.from_user.id,"DIRECT_PRODUCT_PAYMENT_SUCCESS",f"Product ID: {prod_id}, Order: {order_id}, Amount: {amount}, UTR: {data.get('utr','')}")
    except Exception as e:
        logger.exception("Direct product payment verification error: %s", e)
        # The delivery engine owns refund handling. Do not credit the paid amount a second time.
        await call.message.edit_text("⚠️ Payment was verified but delivery hit an unexpected error.\n\n💰 Your payment is protected; please retry from Support if the product was not delivered.", reply_markup=back_kb("menu_shop"), parse_mode='HTML')

async def execute_wallet_purchase(call_or_message, prod_id: int, android_id: str = "", quantity: int = 1):
    user_id = call_or_message.from_user.id
    _purchase_lock = await _purchase_gate(user_id)
    if _purchase_lock is None:
        if isinstance(call_or_message, CallbackQuery):
            return await call_or_message.answer("⏳ Your previous purchase is still processing. Please wait.", show_alert=True)
        return await call_or_message.answer("⏳ Your previous purchase is still processing. Please wait.")
    try:
        return await _execute_wallet_purchase_locked(call_or_message, prod_id, android_id, quantity)
    finally:
        _purchase_lock.release()

async def _execute_wallet_purchase_locked(call_or_message, prod_id: int, android_id: str = "", quantity: int = 1):
    call = call_or_message if isinstance(call_or_message, CallbackQuery) else None
    target = call.message if call else call_or_message
    prod = db_query("""
        SELECT name, price_inr, stock, apk_link, validity, device_limit,
               category, reseller_price, panel_name, external_enabled,
               external_product_id, requires_android_id, external_duration, api_provider_id
        FROM products WHERE id=?
    """, (prod_id,), fetchone=True)
    user_id = call_or_message.from_user.id
    quantity=max(1,min(5,int(quantity or 1)))
    user = db_query("SELECT balance, referred_by, is_reseller, total_saved, is_vip FROM users WHERE user_id=?", (user_id,), fetchone=True)
    if not prod:
        return await (call.answer("❌ Critical Error: Item not found in DB!", show_alert=True) if call else call_or_message.answer("❌ Critical Error: Item not found in DB!"))
    if not user:
        return await (call.answer("❌ User account not found!", show_alert=True) if call else call_or_message.answer("❌ User account not found!"))

    normal_price = float(prod[1] or 0.0)
    reseller_price = float(prod[7] or 0.0)
    is_reseller = bool(user[2]); is_vip = bool(user[4])
    base_price = reseller_price if is_reseller else normal_price
    final_price = base_price - (base_price * (VIP_DISCOUNT_PERCENTAGE / 100)) if is_vip else base_price
    savings = normal_price - final_price
    final_price *= quantity
    savings *= quantity

    # External/API products use the remote API as the real inventory source.
    # Their local stock number is only a display buffer and must not block API purchases.
    external_enabled = bool(prod[9])
    external_product_id = (prod[10] or "").strip()
    api_provider_id = prod[13] if len(prod) > 13 else None
    if not external_enabled and (prod[2] is None or prod[2] < quantity):
        return await (call.answer("❌ This product is out of stock!", show_alert=True) if call else call_or_message.answer("❌ This product is out of stock!"))
    if user[0] < final_price:
        return await (call.answer(f"❌ Insufficient Balance! You need {fmt_curr(final_price)}.", show_alert=True) if call else call_or_message.answer(f"❌ Insufficient Balance! You need {fmt_curr(final_price)}."))

    # Prevent duplicate purchase taps: concurrent clicks are rejected and successful delivery gets a short stale-button cooldown.
    await (call.answer("⏳ Processing your purchase...", show_alert=False) if call else call_or_message.answer("⏳ Processing your purchase..."))
    delivered_key = None
    api_response=None; delivered_keys=[]
    db_query("UPDATE users SET balance=?, spent=spent+?, orders_count=orders_count+?, total_saved=total_saved+? WHERE user_id=?",(user[0]-final_price,final_price,quantity,savings,user_id))
    if external_enabled:
        if not external_product_id:
            db_query("UPDATE users SET balance=balance+? WHERE user_id=?",(final_price,user_id)); return await target.edit_text("❌ API Product ID missing.\n\n💰 Full amount refunded.",reply_markup=back_kb("menu_shop"),parse_mode='HTML')
        candidates=[]
        for candidate in ((prod[12] or '').strip(),prod[4],prod[0]):
            candidate=normalize_api_duration(candidate)
            if candidate and candidate not in candidates: candidates.append(candidate)
        for _ in range(quantity):
            api_response={"status":"error","msg":"No API duration configured"}
            for api_duration in candidates:
                try: api_response=await fetch_external_key(external_product_id,api_duration,android_id,api_provider_id)
                except Exception as e: api_response={"status":"error","msg":f"API call failed: {e}"}
                if api_response.get('status')=='success': break
                blob=json.dumps(api_response,ensure_ascii=False).lower()
                if 'price not found' not in blob and 'price_not_found' not in blob: break
            if api_response.get('status')!='success':
                db_query("UPDATE users SET balance=balance+? WHERE user_id=?",(final_price,user_id)); return await target.edit_text(f"❌ <b>API Error:</b> {api_response.get('msg','Unknown API error')}\n\n💰 Full amount refunded. No partial order was delivered.",reply_markup=back_kb("menu_shop"),parse_mode='HTML')
            key=api_response.get('key'); key='\n'.join(map(str,key)) if isinstance(key,list) else key
            if not key or str(key).strip()=='KEY_NOT_FOUND':
                db_query("UPDATE users SET balance=balance+? WHERE user_id=?",(final_price,user_id)); return await target.edit_text("❌ API returned no key.\n\n💰 Full amount refunded.",reply_markup=back_kb("menu_shop"),parse_mode='HTML')
            delivered_keys.append(str(key))
    else:
        # Reserve manual keys atomically so two simultaneous buyers can never receive the same key.
        conn = sqlite3.connect(DB_FILE, timeout=5, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT id,key_text FROM product_keys WHERE product_id=? AND is_used=0 LIMIT ?", (prod_id, quantity)).fetchall()
            if len(rows) < quantity:
                conn.rollback()
                db_query("UPDATE users SET balance=balance+? WHERE user_id=?",(final_price,user_id))
                return await target.edit_text("❌ Not enough manual keys.\n\n💰 Full amount refunded.",reply_markup=back_kb("menu_shop"),parse_mode='HTML')
            for kid,ktext in rows:
                delivered_keys.append(str(ktext))
                conn.execute("UPDATE product_keys SET is_used=1 WHERE id=? AND is_used=0", (kid,))
            conn.execute("UPDATE products SET stock=CASE WHEN stock>=? THEN stock-? ELSE 0 END WHERE id=?", (quantity,quantity,prod_id))
            conn.commit()
        except Exception:
            conn.rollback()
            db_query("UPDATE users SET balance=balance+? WHERE user_id=?",(final_price,user_id))
            logger.exception("Manual key reservation failed")
            return await target.edit_text("❌ Could not reserve the manual keys.\n\n💰 Full amount refunded.",reply_markup=back_kb("menu_shop"),parse_mode='HTML')
        finally:
            conn.close()
    delivered_key="\n\n".join(delivered_keys)
    if user[1]:
        commission = final_price * 0.15
        db_query("UPDATE users SET balance=balance+?, referral_earned=referral_earned+? WHERE user_id=?", (commission, commission, user[1]))
        try:
            await bot.send_message(user[1], f"🎁 <b>Referral Bonus Added!</b>\nYou earned {fmt_curr(commission)} from a successful purchase.", parse_mode='HTML')
        except Exception:
            pass

    product_full_name = str(prod[0])
    db_query("INSERT INTO orders (user_id, product_name, price_paid, delivered_key, purchase_date) VALUES (?, ?, ?, ?, ?)",
             (user_id, product_full_name, final_price, delivered_key, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    log_activity(user_id, "PURCHASE_SUCCESS", f"Product: {product_full_name}, Paid: {final_price}, External API: {external_enabled}")
    await send_advanced_notification(user_id, "ORDER", final_price, product=product_full_name, key=delivered_key)

    msg = (f"✅ <b>PURCHASE SUCCESSFUL!</b>\n━━━━━━━━━━━━━━━━━━\n"
           f"📦 <b>Product:</b> {prod[0]}\n"
           f"⏱ <b>Validity:</b> {prod[4]}\n💰 <b>Amount Deducted:</b> {fmt_curr(final_price)}\n"
           f"📱 <b>Device Limit:</b> {prod[5]}\n━━━━━━━━━━━━━━━━━━\n")
    if prod[3] and prod[3].startswith("http"):
        msg += f"📥 <b>APK Link:</b> <a href='{prod[3]}'>Click Here to Download</a>\n\n"
    msg += f"🔑 <b>Your Exclusive Key:</b>\n<code>{delivered_key}</code>\n\n"
    _set_purchase_cooldown(user_id, 4.0)
    if external_enabled and api_response:
        if api_response.get("product"):
            msg += f"📌 <b>Product:</b> {api_response['product']}\n"
        if api_response.get("duration"):
            msg += f"⏳ <b>Duration:</b> {api_response['duration']}\n"
    msg += f"\n<i>For any issues, tap Support or contact: {ADMIN_CONTACT}</i>"
    await target.edit_text(msg, reply_markup=back_kb("menu_shop"), disable_web_page_preview=True, parse_mode='HTML')

# ==============================================================================
# 14. USER DASHBOARD, FILES, VIP, RESELLER, ORDERS, PROFILE, REFERRAL
# ==============================================================================
@dp.callback_query(F.data == "menu_all_files")
async def all_files_handler(call: CallbackQuery):
    try:
        await call.answer()
    except Exception:
        pass
    link_q = db_query("SELECT value FROM settings WHERE key='all_files_link'", fetchone=True)
    link = link_q[0] if link_q and link_q[0] != 'None' else None
    if link:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Access Download Channel ↗️", url=link, icon_custom_emoji_id=get_emoji_icon("download"), style="success")],
            [InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
        ])
        text = get_ui_text("download_files")
        await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')
    else:
        await call.answer("⚠️ Admin has not configured the private download channel link yet.", show_alert=True)

@dp.callback_query(F.data == "menu_vip_dash")
async def vip_dashboard(call: CallbackQuery):
    try:
        await call.answer()
    except Exception:
        pass
    u = db_query("SELECT balance, is_vip, vip_since FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    is_vip = bool(u[1])
    status_str = "🟢 Active (Lifetime)" if is_vip else "🔴 Not Subscribed"
    text = get_ui_text("vip_menu", vip_status=status_str)
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    if is_vip:
        text += f"\n📅 <b>Member Since:</b> {u[2]}\n\nEnjoy your permanent 15% discount!"
    else:
        text += f"\n\n💳 <b>Your Current Balance:</b> {fmt_curr(u[0])}\n"
        if u[0] >= VIP_PRICE_INR: 
            kb.inline_keyboard.append([InlineKeyboardButton(text=f"✅ Purchase VIP for {fmt_curr(VIP_PRICE_INR)}", callback_data="execute_vip_upgrade", icon_custom_emoji_id=get_emoji_icon("vip"), style="success")])
        else:
            kb.inline_keyboard.append([InlineKeyboardButton(text=f"❌ Need {fmt_curr(VIP_PRICE_INR)} to Upgrade", callback_data="ignore_stock_click", style="danger")])
            kb.inline_keyboard.append([InlineKeyboardButton(text="💳 Add Balance Now", callback_data="menu_add_balance", icon_custom_emoji_id=get_emoji_icon("add_balance"), style="success")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "execute_vip_upgrade")
async def execute_vip_upgrade(call: CallbackQuery):
    u = db_query("SELECT balance, is_vip FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    if u[1]: return await call.answer("⚠️ You are already a VIP Member!", show_alert=True)
    if u[0] < VIP_PRICE_INR: return await call.answer(f"❌ Your balance dropped below {VIP_PRICE_INR}.", show_alert=True)
    new_balance = u[0] - VIP_PRICE_INR
    now_date = datetime.now().strftime("%Y-%m-%d")
    db_query("UPDATE users SET balance=?, is_vip=1, vip_since=? WHERE user_id=?", (new_balance, now_date, call.from_user.id))
    log_activity(call.from_user.id, "UPGRADED_VIP")
    try: await notify_admins( f"🌟 <b>NEW VIP UPGRADE</b>\n👤 User ID: <code>{call.from_user.id}</code>", parse_mode='HTML')
    except: pass
    await call.answer("🎉 Upgrade Successful! You are now a VIP Member.", show_alert=True)
    await vip_dashboard(call)

@dp.callback_query(F.data == "menu_reseller_dash")
async def reseller_dashboard(call: CallbackQuery):
    try:
        await call.answer()
    except Exception:
        pass
    u = db_query("SELECT balance, is_reseller, reseller_since, total_saved FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    status_check = db_query("SELECT value FROM settings WHERE key='reseller_system_status'", fetchone=True)
    system_status = status_check[0] if status_check else "ON"
    setup_fee = float(get_setting("reseller_setup_fee", "200.0"))
    min_balance = float(get_setting("reseller_min_balance", "500.0"))
    if u[1]: 
        text = (f"{get_emoji('shield_icon')} <b><u>— RESELLER DASHBOARD —</u></b> {get_emoji('shield_icon')}\n\n🟢 <b>Status:</b> Active\n📅 <b>Since:</b> {u[2]}\n{get_emoji('money_icon')} <b>Total Saved:</b> {fmt_curr(u[3])}\n\n🎉 You are enjoying exclusive wholesale prices on all products!")
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]])
        await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')
        return
    if system_status == "OFF": return await call.answer("⚠️ Wholesale / Reseller registrations are currently closed by Admin.", show_alert=True)
    text = (f"⚡ <b><u>— BECOME A RESELLER —</u></b> ⚡\n\nUpgrade your account to access wholesale <b>Reseller Prices</b>!\n\n📋 <b>Requirements to Upgrade:</b>\n1️⃣ Must have a minimum balance of <b>{fmt_curr(min_balance)}</b>.\n2️⃣ A one-time setup fee of <b>{fmt_curr(setup_fee)}</b> will be deducted.\n\n💳 <b>Your Current Balance:</b> {fmt_curr(u[0])}\n")
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    if u[0] >= min_balance: 
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"✅ Pay {fmt_curr(setup_fee)} & Become Reseller", callback_data="execute_reseller_upgrade", icon_custom_emoji_id=get_emoji_icon("reseller"), style="success")])
    else:
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"❌ Insufficient Balance (Need {fmt_curr(min_balance)})", callback_data="ignore_stock_click", style="danger")])
        kb.inline_keyboard.append([InlineKeyboardButton(text="💳 Add Balance", callback_data="menu_add_balance", icon_custom_emoji_id=get_emoji_icon("add_balance"), style="success")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "execute_reseller_upgrade")
async def execute_reseller_upgrade(call: CallbackQuery):
    setup_fee = float(get_setting("reseller_setup_fee", "200.0"))
    min_balance = float(get_setting("reseller_min_balance", "500.0"))
    u = db_query("SELECT balance, is_reseller FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    if u[1]: return await call.answer("⚠️ You are already a Reseller!", show_alert=True)
    if u[0] < min_balance: return await call.answer(f"❌ Your balance dropped below {fmt_curr(min_balance)}. Please top up.", show_alert=True)
    new_balance = u[0] - setup_fee
    db_query("UPDATE users SET balance=?, is_reseller=1, reseller_since=?, account_type='Reseller' WHERE user_id=?", (new_balance, datetime.now().strftime("%Y-%m-%d"), call.from_user.id))
    log_activity(call.from_user.id, "UPGRADED_RESELLER")
    try: await notify_admins( f"👑 <b>NEW RESELLER UPGRADE</b>\n👤 User ID: <code>{call.from_user.id}</code>", parse_mode='HTML')
    except: pass
    await call.answer("🎉 Upgrade Successful! Welcome to the Reseller tier.", show_alert=True)
    await reseller_dashboard(call)

@dp.callback_query(F.data == "menu_orders")
async def my_orders(call: CallbackQuery):
    try:
        await call.answer()
    except Exception:
        pass
    orders = db_query("SELECT product_name, delivered_key, purchase_date, price_paid FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10", (call.from_user.id,), fetchall=True)
    if not orders: return await call.message.edit_text("🧾 You haven't made any purchases yet. Your vault is empty.", reply_markup=back_kb(), parse_mode='HTML')
    text = "🧾 <b><u>— YOUR RECENT ORDERS (LAST 10) —</u></b> 🧾\n\n"
    for o in orders: text += f"📦 <b>{o[0]}</b> ({fmt_curr(o[3])})\n🔑 <code>{o[1]}</code>\n📅 <i>{o[2]}</i>\n━━━━━━━━━━━━━━━━\n"
    await call.message.edit_text(text, reply_markup=back_kb(), parse_mode='HTML')

@dp.callback_query(F.data == "menu_profile")
async def show_profile(call: CallbackQuery):
    try:
        await call.answer()
    except Exception:
        pass
    u = db_query("SELECT user_id, first_name, account_type, balance, orders_count, spent, referrals_count, joined_date, is_reseller, reseller_since, total_saved, is_vip FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    acc_type_display = []
    if u[8]: acc_type_display.append(f"{get_emoji('reseller')} Reseller")
    if u[11]: acc_type_display.append(f"{get_emoji('vip')} VIP")
    type_str = " | ".join(acc_type_display) if acc_type_display else f"{get_emoji('regular_user')} Regular User"
    text = (
        f"{get_emoji('grid_id')} <b><u>— YOUR SECURE PROFILE —</u></b> {get_emoji('grid_id')}\n\n"
        f"{get_emoji('grid_id')} <b>Grid ID:</b> <code>{u[0]}</code>\n"
        f"{get_emoji('name')} <b>Name:</b> {u[1]}\n"
        f"{get_emoji('account_level')} <b>Account Level:</b> {type_str}\n\n"
        f"{get_emoji('wallet_left')} <b>— Wallet —</b> {get_emoji('wallet_right')}\n"
        f"{get_emoji('wallet_left')} <b>Current Balance:</b> {fmt_curr(u[3])} {get_emoji('wallet_right')}\n\n"
        f"{get_emoji('global_stats')} <b>— Global Statistics —</b>\n"
        f"{get_emoji('total_orders')} <b>Total Orders:</b> {u[4]}\n"
        f"{get_emoji('total_spent')} <b>Total Spent:</b> {fmt_curr(u[5])}\n"
        f"{get_emoji('total_referrals')} <b>Total Referrals:</b> {u[6]}\n\n"
    )
    if u[8]:
        text += f"{get_emoji('shield_icon')} <b>— RESELLER METRICS —</b> {get_emoji('shield_icon')}\n{get_emoji('money_icon')} <b>Total Saved via Reseller:</b> {fmt_curr(u[10])}\n\n"
    text += f"{get_emoji('joined_grid')} <b>Joined Grid:</b> {u[7]}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Redeem Promo Code",
            callback_data="redeem_coupon",
            icon_custom_emoji_id=get_emoji_icon('redeem_icon'),
            style="success"
        )],
        [InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "redeem_coupon")
async def redeem_coupon_start(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    await call.message.edit_text("🎟 <b>Please enter your VIP / Promo redeem code below:</b>", reply_markup=back_kb("menu_profile"), parse_mode='HTML')
    await state.set_state(UserStates.wait_for_redeem)

@dp.message(UserStates.wait_for_redeem)
async def process_redeem(m: Message, state: FSMContext):
    code = m.text.strip().upper()
    user_id = m.from_user.id
    if db_query("SELECT * FROM redeemed WHERE user_id=? AND code=?", (user_id, code), fetchone=True):
        await m.answer("❌ Anti-Fraud Alert: You already redeemed this unique code!", reply_markup=main_menu_kb(m.from_user.id), parse_mode='HTML')
        await state.clear()
        return
    coupon = db_query("SELECT amount, uses_left FROM coupons WHERE code=?", (code,), fetchone=True)
    if not coupon: await m.answer("❌ Invalid or Expired Code!", reply_markup=main_menu_kb(m.from_user.id), parse_mode='HTML')
    elif coupon[1] <= 0: await m.answer("❌ This code's usage limit has been fully claimed by other users.", reply_markup=main_menu_kb(m.from_user.id), parse_mode='HTML')
    else:
        db_query("UPDATE users SET balance = balance + ? WHERE user_id=?", (coupon[0], user_id))
        db_query("UPDATE coupons SET uses_left = uses_left - 1 WHERE code=?", (code,))
        db_query("INSERT INTO redeemed (user_id, code) VALUES (?, ?)", (user_id, code))
        log_activity(user_id, "PROMO_REDEEMED", f"Code: {code}, Amount: {coupon[0]}")
        await m.answer(f"🎉 <b>Success!</b>\nSafely added {fmt_curr(coupon[0])} to your balance!", reply_markup=main_menu_kb(m.from_user.id), parse_mode='HTML')
        try:
            user_info = db_query("SELECT first_name FROM users WHERE user_id=?", (user_id,), fetchone=True)
            uname = user_info[0] if user_info else "Unknown User"
            await notify_admins( f"🎟 <b>PROMO CODE REDEEMED!</b>\n👤 User: {uname} (<code>{user_id}</code>)\n🔖 Code: <b>{code}</b>\n💵 Amount: {fmt_curr(coupon[0])}", parse_mode='HTML')
        except Exception: pass
    await state.clear()

@dp.callback_query(F.data == "menu_referral")
async def show_referral(call: CallbackQuery):
    try:
        await call.answer()
    except Exception:
        pass
    u = db_query("SELECT referrals_count, referral_earned FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{call.from_user.id}"
    text = (f"{get_emoji('referral')} <b><u>AFFILIATE PROGRAM</u></b> {get_emoji('referral')}\n\n✅ <b>Status:</b> ACTIVE\n💰 Earn <b>2% flat commission</b> on every successful purchase made by your referred friends!\n\n📊 <b>YOUR STATS:</b>\n👥 Total Invited: {u[0]}\n💵 Life-time Earned: {fmt_curr(u[1])}\n\n🔗 <b>Your Invite Link:</b>\n<code>{ref_link}</code>\n\n<i>Simply copy and share this link to start earning!</i>")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

# ==============================================================================
# 15. LUDO / DICE SPIN
# ==============================================================================
@dp.callback_query(F.data == "menu_spin_landing")
async def lucky_spin_landing(call: CallbackQuery):
    status_check = db_query("SELECT value FROM settings WHERE key='spin_status'", fetchone=True)
    spin_status = status_check[0] if status_check else "ON"
    if spin_status == "OFF": return await call.answer("⚠️ Lucky Ludo Spin is currently disabled by Admin.", show_alert=True)
    await call.message.edit_text(f"{get_emoji('ludo_spin')} <b><u>— LUDO SPIN —</u></b> {get_emoji('ludo_spin')}\n\nTest your luck! You can spin once every 24 hours.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎲 Spin Dice Now!", callback_data="execute_spin", icon_custom_emoji_id=get_emoji_icon("ludo_spin"), style="success")], 
        [InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ]), parse_mode='HTML')

@dp.callback_query(F.data == "execute_spin")
async def execute_spin(call: CallbackQuery):
    status_check = db_query("SELECT value FROM settings WHERE key='spin_status'", fetchone=True)
    if status_check and status_check[0] == "OFF": return await call.answer("⚠️ Lucky Spin is disabled.", show_alert=True)
    u = db_query("SELECT last_spin, balance, is_vip FROM users WHERE user_id=?", (call.from_user.id,), fetchone=True)
    now = datetime.now()
    if u[0] and now < datetime.strptime(u[0], "%Y-%m-%d %H:%M:%S") + timedelta(hours=24):
        return await call.message.edit_text("❌ <b>Cooldown Active!</b>\nYou already played today. Come back tomorrow.", reply_markup=back_kb(), parse_mode='HTML')
    await call.message.delete()
    dice_msg = await bot.send_dice(chat_id=call.message.chat.id, emoji="🎲")
    await asyncio.sleep(SPIN_DELAY_SECONDS) 
    dice_val = dice_msg.dice.value
    limit_check = db_query("SELECT value FROM settings WHERE key='daily_spin_limit'", fetchone=True)
    limit = float(limit_check[0]) if limit_check else 50.0
    rewards_db = db_query("SELECT amount FROM spin_rewards WHERE amount <= ?", (limit,), fetchall=True)
    rewards_list = [r[0] for r in rewards_db] if rewards_db else [0.0]
    reward = random.choice(rewards_list)
    if bool(u[2]) and reward > 0: reward = reward * 2.0
    new_bal = u[1] + reward
    db_query("UPDATE users SET balance=?, last_spin=? WHERE user_id=?", (new_bal, now.strftime("%Y-%m-%d %H:%M:%S"), call.from_user.id))
    log_activity(call.from_user.id, "PLAYED_SPIN", f"Reward: {reward}, Dice: {dice_val}")
    msg = get_ui_text("lucky_dice_result", dice_value=dice_val, won_amount=fmt_curr(reward), new_balance=fmt_curr(new_bal))
    if bool(u[2]) and reward > 0: msg += "\n\n<i>🌟 VIP Bonus: 2x Multiplier Applied!</i>"
    await dice_msg.reply(msg, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📚 BACK TO MENU", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="success")]]), parse_mode='HTML')

# ==============================================================================
# 16. TUTORIALS & SUPPORT
# ==============================================================================
@dp.callback_query(F.data == "menu_how_to")
async def tutorial_system(call: CallbackQuery):
    await safe_call_answer(call)
    video_link_query = db_query("SELECT value FROM settings WHERE key='how_to_video'", fetchone=True)
    video_link = video_link_query[0] if video_link_query and video_link_query[0] != 'None' else None
    text = (f"{get_emoji('tutorial')} <b><u>— TUTORIALS & GUIDE —</u></b> {get_emoji('tutorial')}\n\n1️⃣ Add funds via <b>Add Balance</b>\n2️⃣ Navigate to <b>Product Store</b>\n3️⃣ Choose your desired Panel and Package validity.\n4️⃣ The Key and Installation APK link will be instantly provided.")
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    if video_link: kb.inline_keyboard.append([InlineKeyboardButton(text="Watch Full Video Tutorial", url=video_link, icon_custom_emoji_id=get_emoji_icon("tutorial"), style="success")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

def _valid_support_url(url: str, kind: str) -> bool:
    """Validate support URLs so one bad/empty setting cannot break the Support menu."""
    if not url:
        return False
    url = url.strip()
    if not (url.startswith("https://") or url.startswith("http://")):
        return False
    if kind == "telegram":
        return "t.me/" in url or "telegram.me/" in url
    if kind == "whatsapp":
        return "wa.me/" in url or "whatsapp.com/" in url
    return True

@dp.callback_query(F.data == "menu_support")
async def support_center(call: CallbackQuery):
    await call.answer()
    telegram_link = get_setting("support_telegram", "").strip()
    whatsapp_link = get_setting("support_whatsapp", "").strip()
    rows = []
    if _valid_support_url(telegram_link, "telegram"):
        rows.append([InlineKeyboardButton(text="Contact on Telegram", url=telegram_link, icon_custom_emoji_id=get_emoji_icon("telegram"), style="primary")])
    if _valid_support_url(whatsapp_link, "whatsapp"):
        rows.append([InlineKeyboardButton(text="Contact on WhatsApp", url=whatsapp_link, icon_custom_emoji_id=get_emoji_icon("whatsapp"), style="primary")])
    rows.append([
        InlineKeyboardButton(text="🎫 Open New Ticket", callback_data="open_ticket", icon_custom_emoji_id=get_emoji_icon("support"), style="success"),
        InlineKeyboardButton(text="📋 My Open Tickets", callback_data="my_tickets", icon_custom_emoji_id=get_emoji_icon("history"), style="success")
    ])
    rows.append([InlineKeyboardButton(text="BACK", callback_data="back_main", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text(
        f"{get_emoji('telegram')}{get_emoji('whatsapp')} <b><u>— PREMIUM SUPPORT CENTER —</u></b>\n\n"
        "Contact us via Telegram or WhatsApp for instant help, or open a support ticket for admin assistance.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode='HTML'
    )

@dp.callback_query(F.data == "my_tickets")
async def view_my_tickets(call: CallbackQuery):
    await safe_call_answer(call)
    tickets = db_query("SELECT id, message, status, created_at FROM tickets WHERE user_id=? ORDER BY id DESC LIMIT 5", (call.from_user.id,), fetchall=True)
    if not tickets: return await call.message.edit_text("📋 You do not have any active or previous support tickets.", reply_markup=back_kb("menu_support"), parse_mode='HTML')
    text = "📋 <b><u>— Your Recent Tickets —</u></b> 📋\n\n"
    for t in tickets:
        status_icon = "🟢" if t[2] == 'Open' else "🔴"
        text += f"🎫 <b>Ticket #{t[0]}</b> | Status: {status_icon} <b>{t[2]}</b>\n📅 <i>{t[3]}</i>\n📝 <i>{t[1][:80]}...</i>\n\n"
    await call.message.edit_text(text, reply_markup=back_kb("menu_support"), parse_mode='HTML')

@dp.callback_query(F.data == "open_ticket")
async def open_ticket_start(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    await call.message.edit_text("📝 <b>Please type your issue/message below in detail:</b>", reply_markup=back_kb("menu_support"), parse_mode='HTML')
    await state.set_state(UserStates.wait_for_ticket)

@dp.message(UserStates.wait_for_ticket)
async def process_ticket(m: Message, state: FSMContext):
    db_query("INSERT INTO tickets (user_id, message, created_at) VALUES (?, ?, ?)", (m.from_user.id, m.text, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    await m.answer("✅ <b>Ticket Submitted Successfully!</b> Admins will reply soon.", reply_markup=main_menu_kb(m.from_user.id), parse_mode='HTML')
    try: await notify_admins( f"🚨 <b>NEW SUPPORT TICKET</b>\nFrom: <code>{m.from_user.id}</code>\nMsg: {m.text}", parse_mode='HTML')
    except: pass
    log_activity(m.from_user.id, "OPENED_TICKET")
    await state.clear()

# ==============================================================================
# LICENSE ACTIVATION
# ==============================================================================
def _license_db_init():
    conn = sqlite3.connect(LICENSE_DB)
    conn.execute("CREATE TABLE IF NOT EXISTS issued_keys (id INTEGER PRIMARY KEY AUTOINCREMENT, key_hash TEXT UNIQUE NOT NULL, key_plain TEXT DEFAULT '', instance_id TEXT DEFAULT '', user_id INTEGER DEFAULT 0, issued_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked INTEGER DEFAULT 0, activated_at TEXT DEFAULT '', duration_days INTEGER DEFAULT 30)")
    # Backward-compatible migrations for older license databases.
    try:
        conn.execute("ALTER TABLE issued_keys ADD COLUMN key_plain TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE issued_keys ADD COLUMN duration_days INTEGER DEFAULT 30")
    except sqlite3.OperationalError:
        pass
    conn.execute("CREATE TABLE IF NOT EXISTS activations (id INTEGER PRIMARY KEY AUTOINCREMENT, instance_id TEXT NOT NULL, user_id INTEGER NOT NULL, key_hash TEXT NOT NULL, activated_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked INTEGER DEFAULT 0, UNIQUE(instance_id,user_id), UNIQUE(key_hash))")
    conn.execute("CREATE TABLE IF NOT EXISTS license_payments (order_id TEXT PRIMARY KEY, instance_id TEXT NOT NULL, user_id INTEGER NOT NULL, amount_inr REAL NOT NULL, gateway_amount REAL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending', created_at REAL NOT NULL, utr TEXT DEFAULT '', duration_days INTEGER DEFAULT 30)")
    try: conn.execute("ALTER TABLE license_payments ADD COLUMN duration_days INTEGER DEFAULT 30")
    except sqlite3.OperationalError: pass
    conn.commit(); conn.close()

def _license_hash(value: str) -> str:
    import hashlib
    return hashlib.sha256(value.strip().encode()).hexdigest()

@dp.message(Command("activate"))
async def activate_license(message: Message, state: FSMContext):
    if not LICENSE_ENFORCED:
        return await message.answer("ℹ️ License protection is disabled.")
    if not is_configured_admin(message.from_user.id):
        return await message.answer("❌ You are not an authorized admin for this bot.")
    parts=(message.text or "").split(maxsplit=1)
    if len(parts)!=2:
        return await message.answer("🔐 Use: <code>/activate OBITO_ADMIN_1-...</code>",parse_mode='HTML')
    if not parts[1].strip().upper().startswith("OBITO_ADMIN_1-"):
        return await message.answer("❌ Invalid key format. Your key must start with <code>OBITO_ADMIN_1-</code>.", parse_mode='HTML')
    _license_db_init(); key=parts[1].strip(); h=_license_hash(key)
    conn=sqlite3.connect(LICENSE_DB)
    row=conn.execute("SELECT id,instance_id,user_id,expires_at,revoked,COALESCE(duration_days,30),activated_at FROM issued_keys WHERE key_hash=?",(h,)).fetchone()
    if not row:
        conn.close(); return await message.answer("❌ Invalid license key.")
    if row[4]:
        conn.close(); return await message.answer("❌ This key has been revoked.")
    # NEW LICENSE RULE: validity starts when the admin activates the key, not when
    # the key is purchased/issued. This prevents losing days while a key is unused.
    already_activated = bool(row[6])
    if already_activated:
        try:
            if not row[3] or datetime.fromisoformat(row[3]) <= datetime.now(timezone.utc).replace(tzinfo=None):
                conn.close(); return await message.answer("❌ This key has expired. Please buy/obtain a new key.")
        except Exception:
            conn.close(); return await message.answer("❌ This key has an invalid expiry record.")
    if row[1] and row[1] != INSTANCE_ID:
        conn.close(); return await message.answer("❌ This key belongs to another bot instance.")
    if row[2] and row[2] != message.from_user.id:
        conn.close(); return await message.answer("❌ This key is already bound to another Telegram account.")
    existing=conn.execute("SELECT 1 FROM activations WHERE instance_id=? AND user_id=? AND revoked=0 AND expires_at>?",(INSTANCE_ID,message.from_user.id,datetime.now(timezone.utc).replace(tzinfo=None).isoformat())).fetchone()
    if existing:
        conn.close(); return await message.answer("✅ You already have an active admin license.\n\nNo new key is needed until this license expires.")
    now_dt=datetime.now(timezone.utc).replace(tzinfo=None)
    duration_days=max(1, int(row[5] or 30))
    new_exp=now_dt + timedelta(days=duration_days)
    now=now_dt.isoformat(); exp=new_exp.isoformat()
    conn.execute("UPDATE issued_keys SET instance_id=?,user_id=?,activated_at=?,expires_at=?,duration_days=? WHERE id=?",(INSTANCE_ID,message.from_user.id,now,exp,duration_days,row[0]))
    conn.execute("INSERT OR REPLACE INTO activations(instance_id,user_id,key_hash,activated_at,expires_at,revoked) VALUES(?,?,?,?,?,0)",(INSTANCE_ID,message.from_user.id,h,now,exp))
    conn.commit(); conn.close()
    _invalidate_license_cache(message.from_user.id)
    await message.answer(f"✅ <b>Admin license activated.</b>\n\n⏳ Valid for: <b>{duration_days} day(s)</b> from this activation.\nValid until: <code>{exp}</code>\n\nNo key will be requested again while this license remains active. After expiry, Admin Panel locks and you can activate a new key.",parse_mode='HTML')


def _license_payment_create_url() -> str:
    return f"{LICENSE_PAYMENT_BASE_URL}/api/qr.php"


def _license_payment_verify_url() -> str:
    return f"{LICENSE_PAYMENT_BASE_URL}/api/verify-order.php"


def _new_license_key() -> str:
    return "OBITO_ADMIN_1-" + __import__("secrets").token_urlsafe(18).replace("-", "X").replace("_", "Y")


def _activate_paid_license(order_id: str, user_id: int, paid_amount: float, utr: str = "") -> Optional[str]:
    """Consume a paid license order and issue a selected-duration key. Admin activates it with /activate."""
    _license_db_init()
    conn=sqlite3.connect(LICENSE_DB)
    try:
        conn.execute("BEGIN IMMEDIATE")
        txn=conn.execute("SELECT instance_id,user_id,amount_inr,status,COALESCE(duration_days,30) FROM license_payments WHERE order_id=?",(order_id,)).fetchone()
        if not txn or txn[1] != user_id or txn[0] != INSTANCE_ID:
            conn.rollback(); return None
        if txn[3] == 'paid':
            row=conn.execute("SELECT key_plain FROM issued_keys WHERE instance_id=? AND user_id=? ORDER BY id DESC LIMIT 1",(INSTANCE_ID,user_id)).fetchone()
            conn.rollback(); return row[0] if row and row[0] else None
        if txn[3] != 'pending' or abs(float(paid_amount)-float(txn[2])) > 0.011:
            conn.rollback(); return None
        now=datetime.now(timezone.utc).replace(tzinfo=None); duration_days=max(1, int(txn[4] or 30)); key=_new_license_key(); h=_license_hash(key)
        # Paid key validity starts on /activate, not at payment time.
        conn.execute("INSERT INTO issued_keys(key_hash,key_plain,instance_id,user_id,issued_at,expires_at,revoked,activated_at,duration_days) VALUES(?,?,?,?,?,?,0,'',?)",(h,key,INSTANCE_ID,user_id,now.isoformat(),'',duration_days))
        conn.execute("UPDATE license_payments SET status='paid', utr=? WHERE order_id=? AND status='pending'",(utr,order_id))
        conn.commit(); return key
    except Exception as e:
        conn.rollback(); logger.error(f"Paid license key issue failed: {e}"); return None
    finally:
        conn.close()


async def _verify_license_payment(user_id: int, order_id: str, reply_target: Any) -> None:
    _license_db_init()
    conn=sqlite3.connect(LICENSE_DB)
    txn=conn.execute("SELECT amount_inr,gateway_amount,status,created_at,COALESCE(duration_days,30) FROM license_payments WHERE order_id=? AND instance_id=? AND user_id=?",(order_id,INSTANCE_ID,user_id)).fetchone()
    conn.close()
    if not txn:
        msg="❌ Invalid or fake license payment order."
        if isinstance(reply_target, CallbackQuery): return await reply_target.answer(msg,show_alert=True)
        return await reply_target.answer(msg)
    amount,gateway_amount,status,created_at,duration_days=txn
    if status == 'paid':
        msg="✅ This license payment was already processed. Your OBITO ADMIN admin key was already issued. Use <code>/activate YOUR-KEY</code> if you have not activated it yet."
        if isinstance(reply_target, CallbackQuery): return await reply_target.answer(msg,show_alert=True)
        return await reply_target.answer(msg)
    if status != 'pending' or time.time()-float(created_at) > LICENSE_ORDER_TTL:
        conn=sqlite3.connect(LICENSE_DB); conn.execute("UPDATE license_payments SET status='expired' WHERE order_id=? AND status='pending'",(order_id,)); conn.commit(); conn.close()
        msg="⏳ <b>License payment expired.</b> Please create a new payment."
        if isinstance(reply_target, CallbackQuery): return await reply_target.message.edit_text(msg,reply_markup=license_buy_kb(user_id),parse_mode='HTML')
        return await reply_target.answer(msg,reply_markup=license_buy_kb(user_id),parse_mode='HTML')
    if not LICENSE_PAYMENT_API_KEY:
        msg="⚠️ License payment gateway is not configured. Please contact the bot owner."
        if isinstance(reply_target, CallbackQuery): return await reply_target.answer(msg,show_alert=True)
        return await reply_target.answer(msg)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(_license_payment_verify_url(),params={"api_key":LICENSE_PAYMENT_API_KEY,"order_id":order_id},timeout=aiohttp.ClientTimeout(total=15)) as resp:
                try: data=await resp.json(content_type=None)
                except Exception: data={}
                if resp.status==200 and data.get("status")=="success":
                    gd=data.get("data") or {}
                    paid=float(gd.get("amount") or 0)
                    expected=float(gateway_amount or amount)
                    if paid and abs(paid-expected)>0.011:
                        msg="⚠️ Payment amount mismatch detected. License was NOT activated."
                        if isinstance(reply_target, CallbackQuery): return await reply_target.answer(msg,show_alert=True)
                        return await reply_target.answer(msg)
                    key=_activate_paid_license(order_id,user_id,paid or expected,str(gd.get("utr") or ""))
                    if key:
                        success=(f"🎉 <b>PAYMENT VERIFIED</b>\n\n✅ ₹{float(amount):.2f} received.\n🔐 <b>Admin License Key Issued</b>\n\nYour OBITO ADMIN license key:\n<code>{key}</code>\n\nNow send <code>/activate YOUR-KEY</code> once, then use <code>/admin</code>.\n⏳ Key validity: {int(duration_days or 30)} days.")
                    else:
                        success="✅ Payment verified. Your admin license is already active. Use /admin."
                    if isinstance(reply_target, CallbackQuery): return await reply_target.message.edit_text(success,reply_markup=admin_access_kb(),parse_mode='HTML')
                    if isinstance(reply_target, Bot): return await reply_target.send_message(user_id,success,reply_markup=admin_access_kb(),parse_mode='HTML')
                    return await reply_target.answer(success,reply_markup=admin_access_kb(),parse_mode='HTML')
                msg=f"⏳ Payment is not confirmed yet. Please complete the exact ₹{float(gateway_amount or amount):.2f} payment and try Verify again."
                if isinstance(reply_target, CallbackQuery): return await reply_target.answer(msg,show_alert=True)
                return await reply_target.answer(msg)
    except Exception as e:
        logger.error(f"License payment verify error: {e}")
        msg="⚠️ Could not connect to the payment gateway. Please try again."
        if isinstance(reply_target, CallbackQuery): return await reply_target.answer(msg,show_alert=True)
        return await reply_target.answer(msg)


def license_buy_kb(user_id: int = 0) -> InlineKeyboardMarkup:
    rows=[]
    labels={"1":"1 Day","7":"7 Days","15":"15 Days","30":"30 Days","2m":"2 Months","6m":"6 Months","1y":"1 Year"}
    for key,label in labels.items():
        plan=LICENSE_PLANS.get(key) or {}
        try: price=float(plan.get("price",0))
        except: price=0
        if price>0: rows.append([InlineKeyboardButton(text=f"💳 {label} • ₹{price:.0f}",callback_data=f"license_plan_{key}",style="success")])
    rows.append([InlineKeyboardButton(text="🔐 I Already Have a Key",callback_data="license_activate_help",style="primary")])
    # Owner-only controls remain available while the primary owner's own
    # Admin Panel is locked. They are never exposed to normal admins.
    if user_id == ADMIN_ID and user_id > 0:
        rows.append([InlineKeyboardButton(text="👑 Owner License Control",callback_data="owner_license_control",style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_access_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚙️ Open Admin Panel",callback_data="admin_panel_back",style="success")]])

@dp.callback_query(F.data == "license_buy")
async def license_buy(call: CallbackQuery):
    if not is_configured_admin(call.from_user.id): return await call.answer("❌ You are not the configured admin.",show_alert=True)
    if not LICENSE_PAYMENT_API_KEY: return await call.message.edit_text("⚠️ <b>License payment is not configured.</b>",reply_markup=license_buy_kb(call.from_user.id),parse_mode='HTML')
    _license_db_init()
    conn=sqlite3.connect(LICENSE_DB)
    active=conn.execute("SELECT 1 FROM activations WHERE instance_id=? AND user_id=? AND revoked=0 AND expires_at>?",(INSTANCE_ID,call.from_user.id,datetime.now(timezone.utc).replace(tzinfo=None).isoformat())).fetchone()
    conn.close()
    if active: return await call.message.edit_text("✅ <b>Your admin license is already active.</b>\nUse /admin",reply_markup=admin_access_kb(),parse_mode='HTML')
    await call.message.edit_text("🔐 <b>SELECT ADMIN LICENSE</b>\n\nChoose how long your Admin Panel license should remain valid:",reply_markup=license_buy_kb(call.from_user.id),parse_mode='HTML')
    await call.answer()

@dp.callback_query(F.data.startswith("license_plan_"))
async def license_plan_buy(call: CallbackQuery):
    if not is_configured_admin(call.from_user.id): return await call.answer("❌ You are not the configured admin.",show_alert=True)
    key=call.data.split("license_plan_",1)[1]; plan=LICENSE_PLANS.get(key) or {}
    labels={"1":"1 Day","7":"7 Days","15":"15 Days","30":"30 Days","2m":"2 Months","6m":"6 Months","1y":"1 Year"}
    label=labels.get(key)
    if not label: return await call.answer("Invalid plan",show_alert=True)
    try: price=float(plan.get("price",0)); days=int(plan.get("days",0))
    except: price=0; days=0
    if price<1 or days<1: return await call.answer("This plan is not configured.",show_alert=True)
    _license_db_init(); conn=sqlite3.connect(LICENSE_DB)
    active=conn.execute("SELECT 1 FROM activations WHERE instance_id=? AND user_id=? AND revoked=0 AND expires_at>?",(INSTANCE_ID,call.from_user.id,datetime.now(timezone.utc).replace(tzinfo=None).isoformat())).fetchone()
    if active: conn.close(); return await call.message.edit_text("✅ <b>Your admin license is already active.</b>\nUse /admin",reply_markup=admin_access_kb(),parse_mode='HTML')
    order_id=f"LIC{INSTANCE_ID}{call.from_user.id}{int(time.time())}{random.randint(1000,9999)}"
    conn.execute("INSERT INTO license_payments(order_id,instance_id,user_id,amount_inr,gateway_amount,status,created_at,duration_days) VALUES(?,?,?,?,?,'pending',?,?)",(order_id,INSTANCE_ID,call.from_user.id,price,price,time.time(),days)); conn.commit(); conn.close()
    try:
        deep=f"https://t.me/{BOT_USERNAME.lstrip('@')}?start=lic_{order_id}"
        async with aiohttp.ClientSession() as session:
            params={"api_key":LICENSE_PAYMENT_API_KEY,"amount":f"{price:.2f}","redirect_url":deep}
            async with session.get(_license_payment_create_url(),params=params,timeout=aiohttp.ClientTimeout(total=15)) as resp:
                try: res=await resp.json(content_type=None)
                except Exception: res={}
                if resp.status!=200 or res.get("status")!="success":
                    conn=sqlite3.connect(LICENSE_DB); conn.execute("UPDATE license_payments SET status='failed' WHERE order_id=? AND status='pending'",(order_id,)); conn.commit(); conn.close()
                    return await call.message.edit_text(f"❌ <b>Payment gateway error:</b> {res.get('message',f'HTTP {resp.status}')}",reply_markup=license_buy_kb(call.from_user.id),parse_mode='HTML')
                data=res.get("data") or {}; gateway_order=data.get("order_id") or order_id; qr_url=data.get("qr_url") or ""; checkout=data.get("checkout_url") or qr_url; payable=float(data.get("payable_amount") or price)
                conn=sqlite3.connect(LICENSE_DB); conn.execute("UPDATE license_payments SET gateway_amount=? WHERE order_id=?",(payable,order_id)); conn.commit(); conn.close()
                if gateway_order!=order_id:
                    conn=sqlite3.connect(LICENSE_DB); conn.execute("UPDATE license_payments SET order_id=? WHERE order_id=?",(gateway_order,order_id)); conn.commit(); conn.close(); order_id=gateway_order
                if not checkout: return await call.message.edit_text("❌ Gateway did not return a payment/QR URL. Please try again.",reply_markup=license_buy_kb(call.from_user.id))
                rows=[[InlineKeyboardButton(text=f"💳 Pay ₹{payable:.2f}",url=checkout,style="success")]]
                if qr_url and qr_url!=checkout: rows.append([InlineKeyboardButton(text="📷 Open UPI QR",url=qr_url,style="primary")])
                rows += [[InlineKeyboardButton(text="🔄 Verify Payment",callback_data=f"license_verify_{order_id}",style="success")],[InlineKeyboardButton(text="❌ Cancel",callback_data="license_cancel",style="danger")]]
                text=f"💳 <b>{label.upper()} ADMIN LICENSE</b>\n\n⏱️ Validity: <b>{label}</b>\n💰 Price: <b>₹{payable:.2f}</b>\n🧾 Order: <code>{order_id}</code>\n⏳ Payment window: <b>5 minutes</b>\n\n1️⃣ Tap Pay / QR.\n2️⃣ Pay exact amount.\n3️⃣ Tap Verify Payment.\n\n🔐 Successful verification issues a {label} OBITO ADMIN admin key."
                await call.message.edit_text(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),parse_mode='HTML')
    except Exception:
        logger.exception("License payment create error")
        conn=sqlite3.connect(LICENSE_DB); conn.execute("UPDATE license_payments SET status='failed' WHERE order_id=? AND status='pending'",(order_id,)); conn.commit(); conn.close()
        await call.message.edit_text("❌ Payment gateway connection failed. Please try again later.",reply_markup=license_buy_kb(call.from_user.id),parse_mode='HTML')
    await call.answer()


@dp.callback_query(F.data.startswith("license_verify_"))
async def license_verify_callback(call: CallbackQuery):
    if not is_configured_admin(call.from_user.id):
        return await call.answer("❌ You are not the configured admin.",show_alert=True)
    await call.answer("🔄 Verifying payment...")
    await _verify_license_payment(call.from_user.id,call.data.split("license_verify_",1)[1],call)


@dp.callback_query(F.data == "license_cancel")
async def license_cancel(call: CallbackQuery):
    if not is_configured_admin(call.from_user.id): return await call.answer("❌ Not authorized.",show_alert=True)
    await call.message.edit_text("🔐 <b>Admin License</b>\n\nA valid admin license is required for /admin.",reply_markup=license_buy_kb(call.from_user.id),parse_mode='HTML')
    await call.answer()


@dp.callback_query(F.data == "license_activate_help")
async def license_activate_help(call: CallbackQuery):
    if not is_configured_admin(call.from_user.id): return await call.answer("❌ Not authorized.",show_alert=True)
    await call.message.edit_text("🔑 <b>Already have a license key?</b>\n\nUse:\n<code>/activate YOUR-KEY</code>\n\nIf you don't have a key, use the Buy button and choose 1/7/15/30-day, 2-month, 6-month or 1-year validity.",reply_markup=license_buy_kb(call.from_user.id),parse_mode='HTML')
    await call.answer()


@dp.message(Command("buykey"))
async def buykey_cmd(message: Message):
    if not is_configured_admin(message.from_user.id):
        return await message.answer("❌ You are not the configured admin for this bot.")
    await message.answer("🔐 <b>Admin License</b>\n\nChoose your validity from the buttons below.\nAvailable: 1 Day, 7 Days, 15 Days, 30 Days, 2 Months, 6 Months, 1 Year.",reply_markup=license_buy_kb(message.from_user.id),parse_mode='HTML')


@dp.message(Command("license"))
async def license_status(message: Message):
    if not is_configured_admin(message.from_user.id): return
    _license_db_init(); conn=sqlite3.connect(LICENSE_DB)
    row=conn.execute("SELECT expires_at FROM activations WHERE instance_id=? AND user_id=? AND revoked=0 ORDER BY id DESC LIMIT 1",(INSTANCE_ID,message.from_user.id)).fetchone(); conn.close()
    if row: return await message.answer(f"🔐 License active until <code>{row[0]}</code>",parse_mode='HTML')
    await message.answer(f"🔒 <b>No active license.</b>\n\nChoose a license plan below for 1/7/15/30 days, 2 months, 6 months or 1 year.",reply_markup=license_buy_kb(message.from_user.id),parse_mode='HTML')


def _owner_targets() -> List[int]:
    targets = []
    for uid in (ADMIN_ID, SECOND_ADMIN_ID):
        if uid > 0 and uid not in targets:
            targets.append(uid)
    return targets


def owner_license_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🎁 Give Trial", callback_data="owner_trial_menu", style="success")],
        [InlineKeyboardButton(text="⛔ Expire License", callback_data="owner_expire_license", style="danger")],
        [InlineKeyboardButton(text="🔑 Expire Any Key", callback_data="owner_expire_key_help", style="danger")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="owner_license_back", style="primary")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def owner_target_kb(action: str) -> InlineKeyboardMarkup:
    rows = []
    for uid in _owner_targets():
        label = "Owner" if uid == ADMIN_ID else "Second Admin"
        rows.append([InlineKeyboardButton(text=f"{label} • {uid}", callback_data=f"owner_{action}_target_{uid}", style="success")])
    rows.append([InlineKeyboardButton(text="🔙 Back", callback_data="owner_license_control", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.callback_query(F.data == "owner_license_control")
async def owner_license_control(call: CallbackQuery):
    if not is_bot_owner(call.from_user.id):
        return await call.answer("❌ Owner only.", show_alert=True)
    await safe_call_answer(call)
    await call.message.edit_text(
        "👑 <b>OWNER LICENSE CONTROL</b>\n\n"
        "Only the primary bot owner can use these controls.\n"
        "Trial access is free only when granted by the owner. Normal admins must have a paid/issued key.",
        reply_markup=owner_license_kb(), parse_mode="HTML"
    )


@dp.callback_query(F.data == "owner_trial_menu")
async def owner_trial_menu(call: CallbackQuery):
    if not is_bot_owner(call.from_user.id):
        return await call.answer("❌ Owner only.", show_alert=True)
    await safe_call_answer(call)
    await call.message.edit_text(
        "🎁 <b>Choose admin for trial</b>",
        reply_markup=owner_target_kb("trial"), parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("owner_trial_target_"))
async def owner_trial_target(call: CallbackQuery):
    if not is_bot_owner(call.from_user.id):
        return await call.answer("❌ Owner only.", show_alert=True)
    try:
        target = int(call.data.rsplit("_", 1)[1])
    except Exception:
        return await call.answer("❌ Invalid admin.", show_alert=True)
    if target not in _owner_targets():
        return await call.answer("❌ Target is not a configured admin.", show_alert=True)
    rows = [[InlineKeyboardButton(text=f"🎁 {label}", callback_data=f"owner_trial_duration_{target}_{key}", style="success")] for key, (label, _) in TRIAL_DURATIONS.items()]
    rows.append([InlineKeyboardButton(text="🔙 Back", callback_data="owner_trial_menu", style="primary")])
    await safe_call_answer(call)
    await call.message.edit_text("🎁 <b>Select Trial Duration</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")


@dp.callback_query(F.data.startswith("owner_trial_duration_"))
async def owner_trial_grant(call: CallbackQuery):
    if not is_bot_owner(call.from_user.id):
        return await call.answer("❌ Owner only.", show_alert=True)
    parts = call.data.split("_")
    if len(parts) != 5:
        return await call.answer("❌ Invalid trial request.", show_alert=True)
    try:
        target = int(parts[3]); key = parts[4]
    except Exception:
        return await call.answer("❌ Invalid trial request.", show_alert=True)
    if target not in _owner_targets() or key not in TRIAL_DURATIONS:
        return await call.answer("❌ Invalid trial request.", show_alert=True)
    if not _grant_trial_license(target, key):
        return await call.answer("❌ Trial could not be created. An active license may already exist.", show_alert=True)
    label = TRIAL_DURATIONS[key][0]
    await call.message.edit_text(
        f"✅ <b>{label} trial activated.</b>\n\n"
        f"Admin: <code>{target}</code>\n"
        "Admin Panel will auto-lock when the trial expires.",
        reply_markup=owner_license_kb(), parse_mode="HTML"
    )
    await call.answer("✅ Trial activated")


@dp.callback_query(F.data == "owner_expire_license")
async def owner_expire_license(call: CallbackQuery):
    if not is_bot_owner(call.from_user.id):
        return await call.answer("❌ Owner only.", show_alert=True)
    await safe_call_answer(call)
    await call.message.edit_text("⛔ <b>Select admin whose active license should be revoked:</b>", reply_markup=owner_target_kb("expire"), parse_mode="HTML")


@dp.callback_query(F.data.startswith("owner_expire_target_"))
async def owner_expire_target(call: CallbackQuery):
    if not is_bot_owner(call.from_user.id):
        return await call.answer("❌ Owner only.", show_alert=True)
    try:
        target = int(call.data.rsplit("_", 1)[1])
    except Exception:
        return await call.answer("❌ Invalid admin.", show_alert=True)
    if target not in _owner_targets():
        return await call.answer("❌ Target is not a configured admin.", show_alert=True)
    ok = _expire_license(target)
    text = (f"⛔ <b>License expired immediately.</b>\n\nAdmin: <code>{target}</code>\nAdmin Panel is locked now." if ok else f"ℹ️ <b>No active license found.</b>\n\nAdmin: <code>{target}</code>")
    await call.message.edit_text(text, reply_markup=owner_license_kb(), parse_mode="HTML")
    await call.answer("License expired" if ok else "No active license")


@dp.callback_query(F.data == "owner_expire_key_help")
async def owner_expire_key_help(call: CallbackQuery):
    if not is_bot_owner(call.from_user.id):
        return await call.answer("❌ Owner only.", show_alert=True)
    await safe_call_answer(call)
    await call.message.edit_text(
        "🔑 <b>Expire Any Issued Key</b>\n\n"
        "Send the key with this owner-only command:\n"
        "<code>/expirekey YOUR-KEY</code>\n\n"
        "This revokes the key and, if it is active, locks the linked Admin Panel immediately.",
        reply_markup=owner_license_kb(), parse_mode="HTML"
    )


@dp.callback_query(F.data == "owner_license_back")
async def owner_license_back(call: CallbackQuery):
    if not is_bot_owner(call.from_user.id):
        return await call.answer("❌ Owner only.", show_alert=True)
    await safe_call_answer(call)
    await call.message.edit_text("🔐 <b>Admin License</b>\n\nChoose a license plan below.", reply_markup=license_buy_kb(call.from_user.id), parse_mode="HTML")


@dp.message(Command("ownerlicense"))
async def owner_license_command(message: Message):
    if not is_bot_owner(message.from_user.id):
        return await message.answer("❌ Owner only.")
    await message.answer("👑 <b>Owner License Control</b>", reply_markup=owner_license_kb(), parse_mode="HTML")


@dp.message(Command("trial"))
async def owner_trial_command(message: Message):
    if not is_bot_owner(message.from_user.id):
        return await message.answer("❌ Owner only.")
    parts = (message.text or "").split()
    if len(parts) < 2:
        return await message.answer("🎁 <b>Owner Trial</b>\n\nUse /trial 5m, /trial 10m, /trial 1h or /trial 2h [ADMIN_ID]", parse_mode="HTML", reply_markup=owner_license_kb())
    key = parts[1].lower()
    target = ADMIN_ID
    if len(parts) >= 3:
        try:
            target = int(parts[2])
        except Exception:
            return await message.answer("❌ Invalid ADMIN_ID.")
    if key not in TRIAL_DURATIONS:
        return await message.answer("❌ Invalid duration. Use 5m, 10m, 1h or 2h.", reply_markup=owner_license_kb())
    if target not in _owner_targets():
        return await message.answer("❌ Target must be a configured admin.")
    if not _grant_trial_license(target, key):
        return await message.answer("❌ Trial could not be started. An active license may already exist.", reply_markup=owner_license_kb())
    label = TRIAL_DURATIONS[key][0]
    await message.answer(f"✅ <b>{label} trial activated.</b>\n\nAdmin: <code>{target}</code>\nAdmin Panel will auto-lock when the trial expires.", parse_mode="HTML", reply_markup=owner_license_kb())


@dp.message(Command("expire"))
async def owner_expire_command(message: Message):
    if not is_bot_owner(message.from_user.id):
        return await message.answer("❌ Owner only.")
    parts = (message.text or "").split()
    target = ADMIN_ID
    if len(parts) >= 2:
        try:
            target = int(parts[1])
        except Exception:
            return await message.answer("❌ Invalid ADMIN_ID.")
    if target not in _owner_targets():
        return await message.answer("❌ Target must be a configured admin.")
    ok = _expire_license(target)
    await message.answer((f"⛔ <b>License expired immediately.</b>\nAdmin: <code>{target}</code>" if ok else f"ℹ️ No active license found.\nAdmin: <code>{target}</code>"), parse_mode="HTML", reply_markup=owner_license_kb())


@dp.message(Command("expirekey"))
async def owner_expire_key_command(message: Message):
    if not is_bot_owner(message.from_user.id):
        return await message.answer("❌ Owner only.")
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return await message.answer("🔑 Use: <code>/expirekey YOUR-KEY</code>", parse_mode="HTML", reply_markup=owner_license_kb())
    key = parts[1].strip()
    _license_db_init()
    h = _license_hash(key)
    conn = sqlite3.connect(LICENSE_DB, timeout=5)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT instance_id,user_id FROM issued_keys WHERE key_hash=?", (h,)).fetchone()
        if not row:
            conn.rollback()
            return await message.answer("❌ Key not found.", reply_markup=owner_license_kb())
        conn.execute("UPDATE issued_keys SET revoked=1 WHERE key_hash=?", (h,))
        conn.execute("UPDATE activations SET revoked=1 WHERE instance_id=? AND key_hash=?", (INSTANCE_ID, h))
        conn.commit()
        await message.answer(f"⛔ <b>Key revoked successfully.</b>\n\nLinked admin: <code>{row[1] or 0}</code>\nAny active Admin Panel session using this key is now locked on the next access check.", parse_mode="HTML", reply_markup=owner_license_kb())
    except Exception as e:
        conn.rollback()
        logger.error(f"Owner expirekey failed: {e}")
        await message.answer("❌ Could not revoke that key.", reply_markup=owner_license_kb())
    finally:
        conn.close()


@dp.message(Command("admin"))
async def admin_panel(message: Message, state: FSMContext):
    if not is_configured_admin(message.from_user.id):
        return await message.answer("❌ You are not an authorized admin for this bot.")
    if LICENSE_ENFORCED and not is_admin(message.from_user.id):
        if is_bot_owner(message.from_user.id):
            return await message.answer(
                "🔒 <b>ADMIN LICENSE REQUIRED</b>\n\n"
                "Your Admin Panel is locked until a valid key is activated.\n\n"
                "🔑 Use <code>/activate YOUR-KEY</code> if you already have a key.\n"
                "💳 Otherwise choose Buy / Renew License below.\n\n"
                "👑 Owner Trial / Expire controls are available separately to the primary owner via <code>/ownerlicense</code>.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💳 Buy / Renew License", callback_data="license_buy", style="primary")]]),parse_mode='HTML')
        return await message.answer(
            f"🔒 <b>ADMIN LICENSE REQUIRED</b>\n\nYour Admin ID is authorized, but your admin license is not active.\n\n🔑 If you already received a key, use <code>/activate YOUR-KEY</code>.\n💳 Choose a license plan below.",
            reply_markup=license_buy_kb(message.from_user.id),parse_mode='HTML')
    await state.clear()
    await message.answer("⚙️ <b>Advanced Admin Terminal</b>\n<i>Authorized Access Granted. Use the buttons below.</i>", reply_markup=admin_kb(), parse_mode='HTML')
    
# 17. ADMIN PANEL
# ==============================================================================
@dp.callback_query(F.data == "admin_panel_back")
async def back_to_admin(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        if is_configured_admin(call.from_user.id):
            return await call.message.edit_text("🔒 <b>Admin license required.</b>\n\nUse the button below to choose your admin license duration.",reply_markup=license_buy_kb(call.from_user.id),parse_mode='HTML')
        return await call.answer("❌ Not authorized.",show_alert=True)
    await state.clear()
    await call.message.edit_text("⚙️ <b>Advanced Admin Terminal</b>\n<i>Authorized Access Granted. Use the buttons below.</i>", reply_markup=admin_kb(), parse_mode='HTML')

@dp.callback_query(F.data == "admin_toggle_vip_sys")
async def toggle_vip_sys(call: CallbackQuery):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    res = db_query("SELECT value FROM settings WHERE key='vip_status'", fetchone=True)
    current = res[0] if res else 'OFF'
    new_status = 'ON' if current == 'OFF' else 'OFF'
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('vip_status', ?)", (new_status,))
    await call.message.edit_reply_markup(reply_markup=admin_kb())

@dp.callback_query(F.data == "admin_user_control_start")
async def admin_user_control_start(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Download Full User List", callback_data="admin_download_userlist", icon_custom_emoji_id=get_emoji_icon("download"), style="success")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text("💻 <b>User Control Terminal</b>\n\n✏️ Enter the <b>User ID</b> or <b>@Username</b> you want to investigate or manage:\n\n👇 <b>OR</b> download the full user CSV format list:", reply_markup=kb, parse_mode='HTML')
    await state.set_state(AdminStates.manage_target_user)

@dp.callback_query(F.data == "admin_download_userlist")
async def admin_download_userlist(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    users = db_query("SELECT username, user_id, phone, balance, orders_count, is_vip, is_reseller FROM users", fetchall=True)
    if not users: return await call.answer("❌ No users found in the database.", show_alert=True)
    file_content = "FULL DATABASE DUMP\n" + "="*100 + "\n"
    for u in users:
        uname = u[0] if u[0] else "No_Username"
        uid = u[1]
        phone = u[2] if u[2] else "No_Phone"
        bal = u[3]
        orders = u[4]
        vip_status = "YES" if u[5] else "NO"
        res_status = "YES" if u[6] else "NO"
        file_content += f"UID: {uid} | UNAME: {uname} | PHONE: {phone} | BAL: ₹{bal:.2f} | BUY: {orders} | VIP: {vip_status} | RES: {res_status}\n"
    doc = BufferedInputFile(file_content.encode('utf-8'), filename=f"DB_{datetime.now().strftime('%Y%m%d')}.txt")
    await call.message.answer_document(document=doc, caption="📋 <b>Database export complete.</b>", parse_mode='HTML')
    await call.answer()

@dp.message(AdminStates.manage_target_user)
async def process_user_lookup(m: Message, state: FSMContext):
    target = m.text.strip()
    if target.startswith('@'): target = target[1:]
    loader_msg = await hacker_loading(m, "Querying User Database")
    user_q = db_query("SELECT user_id, first_name, username, balance, is_reseller, orders_count, spent, joined_date, is_banned, warnings, is_vip FROM users WHERE user_id=? OR username=? COLLATE NOCASE", (target, target), fetchone=True)
    if not user_q: return await loader_msg.edit_text("❌ Target not found in the grid. Check ID/Username syntax.", reply_markup=admin_back_kb(), parse_mode='HTML')
    u_id, u_name, u_user, bal, is_res, orders, spent, joined, is_banned, warnings, is_vip = user_q
    await state.update_data(target_u_id=u_id)
    status_emoji = "🔴 BANNED" if is_banned else "🟢 ACTIVE"
    tags = []
    if is_res: tags.append("👑 Reseller")
    if is_vip: tags.append("🌟 VIP")
    type_str = " | ".join(tags) if tags else "👤 Regular"
    text = (f"🛡 <b><u>USER CONTROL TERMINAL</u></b> 🛡\n━━━━━━━━━━━━━━━━━━\n📛 <b>Name:</b> {u_name} (@{u_user})\n🆔 <b>ID:</b> <code>{u_id}</code>\n📊 <b>Status:</b> {status_emoji}\n🔰 <b>Type:</b> {type_str}\n⚠️ <b>Warnings Issued:</b> {warnings}\n━━━━━━━━━━━━━━━━━━\n💰 <b>Wallet Balance:</b> {fmt_curr(bal)}\n📦 <b>Orders:</b> {orders} | 💸 <b>Total Spent:</b> {fmt_curr(spent)}\n📅 <b>Joined:</b> {joined}")
    ban_btn_text = "Unban ✅" if is_banned else "Ban 🚫"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Add Funds ➕", callback_data=f"usrctrl_add_{u_id}", icon_custom_emoji_id=get_emoji_icon("add_balance"), style="success"), 
         InlineKeyboardButton(text="Minus Funds ➖", callback_data=f"usrctrl_min_{u_id}", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="danger")],
        [InlineKeyboardButton(text=ban_btn_text, callback_data=f"usrctrl_ban_{u_id}", icon_custom_emoji_id=get_emoji_icon("shield_icon"), style="danger"), 
         InlineKeyboardButton(text="Warn User ⚠️", callback_data=f"usrctrl_warn_{u_id}", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="danger")],
        [InlineKeyboardButton(text="Give VIP 🌟" if not is_vip else "Remove VIP 🚫", callback_data=f"usrctrl_vip_{u_id}", icon_custom_emoji_id=get_emoji_icon("vip"), style="success")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await loader_msg.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("usrctrl_"))
async def handle_user_actions(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return
    action = call.data.split("_")[1]
    u_id = int(call.data.split("_")[2])
    await state.update_data(target_u_id=u_id)
    if action == "ban":
        current_status = db_query("SELECT is_banned FROM users WHERE user_id=?", (u_id,), fetchone=True)[0]
        if current_status == 0:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Yes, Ban", callback_data=f"confirm_ban_{u_id}", icon_custom_emoji_id=get_emoji_icon("check_icon"), style="danger"), 
                 InlineKeyboardButton(text="❌ Cancel", callback_data="admin_user_control_start", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
            ])
            await call.message.edit_text(f"⚠️ Are you sure you want to <b>BAN</b> user <code>{u_id}</code>?", reply_markup=kb, parse_mode='HTML')
            await state.set_state(AdminStates.confirm_ban)
        else:
            db_query("UPDATE users SET is_banned=0 WHERE user_id=?", (u_id,))
            await call.answer("✅ User unbanned successfully!", show_alert=True)
            m = call.message; m.text = str(u_id); await process_user_lookup(m, state)
    elif action == "vip":
        current_status = db_query("SELECT is_vip FROM users WHERE user_id=?", (u_id,), fetchone=True)[0]
        if current_status == 1:
            db_query("UPDATE users SET is_vip=0 WHERE user_id=?", (u_id,))
            await call.answer("✅ VIP Removed!", show_alert=True)
        else:
            db_query("UPDATE users SET is_vip=1, vip_since=? WHERE user_id=?", (datetime.now().strftime("%Y-%m-%d"), u_id))
            await call.answer("✅ VIP Granted!", show_alert=True)
        m = call.message; m.text = str(u_id); await process_user_lookup(m, state)
    elif action == "add":
        await call.message.edit_text("💰 Enter the amount to <b>ADD</b> to this user's wallet:", reply_markup=admin_back_kb(), parse_mode='HTML')
        await state.set_state(AdminStates.wait_for_add_money)
    elif action == "min":
        await call.message.edit_text("💸 Enter the amount to <b>DEDUCT</b> from this user's wallet:", reply_markup=admin_back_kb(), parse_mode='HTML')
        await state.set_state(AdminStates.wait_for_minus_money)
    elif action == "warn":
        await call.message.edit_text("⚠️ Type the strict warning message you want to send directly to this user:", reply_markup=admin_back_kb(), parse_mode='HTML')
        await state.set_state(AdminStates.wait_for_warning)

@dp.callback_query(F.data.startswith("confirm_ban_"))
async def confirm_ban(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id): return
    u_id = int(call.data.split("_")[2])
    db_query("UPDATE users SET is_banned=1 WHERE user_id=?", (u_id,))
    await call.answer("🔴 User has been banned!", show_alert=True)
    await state.clear()
    m = call.message; m.text = str(u_id); await process_user_lookup(m, state)

@dp.message(AdminStates.wait_for_add_money)
async def exec_add_money(m: Message, state: FSMContext):
    try:
        amt = float(m.text)
        data = await state.get_data()
        u_id = data['target_u_id']
        db_query("UPDATE users SET balance = balance + ? WHERE user_id=?", (amt, u_id))
        await m.answer(f"✅ Successfully added {fmt_curr(amt)} to target <code>{u_id}</code>.", reply_markup=admin_kb(), parse_mode='HTML')
        try: await bot.send_message(u_id, f"💰 <b>Wallet Top-up!</b>\nAdmin has manually added {fmt_curr(amt)} to your wallet.", parse_mode='HTML')
        except: pass
        await state.clear()
    except ValueError: await m.answer("❌ Critical Error: Input must be a valid number.")

@dp.message(AdminStates.wait_for_minus_money)
async def exec_minus_money(m: Message, state: FSMContext):
    try:
        amt = float(m.text)
        data = await state.get_data()
        u_id = data['target_u_id']
        db_query("UPDATE users SET balance = balance - ? WHERE user_id=?", (amt, u_id))
        await m.answer(f"✅ Successfully deducted {fmt_curr(amt)} from target <code>{u_id}</code>.", reply_markup=admin_kb(), parse_mode='HTML')
        await state.clear()
    except ValueError: await m.answer("❌ Critical Error: Input must be a valid number.")

@dp.message(AdminStates.wait_for_warning)
async def exec_warn_user(m: Message, state: FSMContext):
    data = await state.get_data()
    u_id = data['target_u_id']
    warn_text = m.text
    db_query("UPDATE users SET warnings = warnings + 1 WHERE user_id=?", (u_id,))
    await m.answer(f"✅ Official warning dispatched to <code>{u_id}</code>.", reply_markup=admin_kb(), parse_mode='HTML')
    try: await bot.send_message(u_id, f"⚠️ <b>OFFICIAL WARNING FROM SYSTEM ADMIN:</b>\n\n{warn_text}\n\n<i>Subsequent infractions may lead to an automated grid ban.</i>", parse_mode='HTML')
    except: pass
    await state.clear()

# ==============================================================================
# 18. ADMIN STATISTICS
# ==============================================================================
@dp.callback_query(F.data == "admin_view_stats")
async def admin_dashboard_stats(call: CallbackQuery):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    t_users = db_query("SELECT COUNT(*) FROM users", fetchone=True)[0]
    t_resellers = db_query("SELECT COUNT(*) FROM users WHERE is_reseller=1", fetchone=True)[0]
    t_vip = db_query("SELECT COUNT(*) FROM users WHERE is_vip=1", fetchone=True)[0]
    t_prods = db_query("SELECT COUNT(*) FROM products", fetchone=True)[0]
    t_keys = db_query("SELECT COUNT(*) FROM product_keys WHERE is_used=0", fetchone=True)[0]
    t_rev = db_query("SELECT SUM(spent) FROM users", fetchone=True)[0] or 0.0
    today_str = datetime.now().strftime("%Y-%m-%d")
    t_spins = db_query("SELECT COUNT(*) FROM users WHERE last_spin LIKE ?", (f"{today_str}%",), fetchone=True)[0]
    msg = (f"📊 <b><u>GRID INTELLIGENCE DASHBOARD</u></b> 📊\n━━━━━━━━━━━━━━━━━━\n👥 <b>Total Grid Users:</b> {t_users}\n👑 <b>Wholesale Resellers:</b> {t_resellers}\n🌟 <b>Elite VIP Members:</b> {t_vip}\n━━━━━━━━━━━━━━━━━━\n📦 <b>Active Products:</b> {t_prods}\n🔑 <b>Unused Keys in Vault:</b> {t_keys}\n💰 <b>Total Gross Revenue:</b> {fmt_curr(t_rev)}\n🎰 <b>Ludo Spins Today:</b> {t_spins}\n━━━━━━━━━━━━━━━━━━")
    await call.message.edit_text(msg, reply_markup=admin_back_kb(), parse_mode='HTML')

# ==============================================================================
# 19. ADMIN PRODUCT MANAGEMENT
# ==============================================================================
@dp.callback_query(F.data == "admin_add_prod")
async def add_prod_start(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    await state.clear()
    kb=InlineKeyboardMarkup(inline_keyboard=[])
    for cat in FIXED_CATEGORIES:
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"📂 {cat}",callback_data=f"newprodcat_{urllib.parse.quote(cat,safe='')}",style='primary')])
    kb.inline_keyboard.append([InlineKeyboardButton(text="✏️ Custom Category",callback_data="newprodcat_custom",style='primary')])
    kb.inline_keyboard.append([InlineKeyboardButton(text="🔙 Back",callback_data="admin_panel_back",style='danger')])
    await call.message.edit_text("📦 <b>Create Product</b>\n\n<b>Step 1 — Select Category</b>\n\nChoose Android Non Root / Android Root / iPhone / PC / Guild / Carrom.",reply_markup=kb,parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_category)

@dp.callback_query(F.data.startswith("newprodcat_"), AdminStates.add_prod_category)
async def add_prod_category_pick(call: CallbackQuery, state: FSMContext):
    token=call.data.split("newprodcat_",1)[1]
    if token=="custom":
        await call.message.edit_text("📂 <b>Custom Category</b>\n\nSend the category name:",reply_markup=admin_back_kb(),parse_mode='HTML')
        return
    cat=urllib.parse.unquote(token).strip().upper()
    if cat not in FIXED_CATEGORIES: return await call.answer("❌ Invalid category",show_alert=True)
    await state.update_data(cat=cat,panel_name="")
    await call.message.edit_text("📦 <b>Step 2 — Product Name</b>\n\nEnter the product name customers will see.\nExample: <code>BALAMOD</code>",reply_markup=admin_back_kb(),parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_name)
    await call.answer()

@dp.message(AdminStates.add_prod_category)
async def add_prod_custom_category(message: Message, state: FSMContext):
    cat=(message.text or '').strip().upper()
    if not cat: return await message.answer("❌ Category cannot be empty.")
    if len(cat)>40: return await message.answer("❌ Category is too long.")
    await state.update_data(cat=cat,panel_name="")
    await message.answer("📦 <b>Step 2 — Product Name</b>\n\nEnter the product name customers will see.\nExample: <code>BALAMOD</code>",reply_markup=admin_back_kb(),parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_name)

@dp.message(AdminStates.add_prod_name)
async def add_prod_name(m: Message,state: FSMContext):
    name=m.text.strip()
    if not name: return await m.answer("❌ Product name cannot be empty.")
    await state.update_data(name=name,product_group=name)
    await m.answer("🔢 <b>How many duration/Hours options does this product have?</b>\n\nExample: <code>5</code> for <b>1 Hour, 2 Hours, 3 Hours, 5 Hours, 6 Hours</b>.",reply_markup=admin_back_kb(),parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_variant_count)

def duration_preset_kb():
    rows=[]
    presets=[("15 Days","15d"),("30 Days","30d"),("2 Months","2m"),("6 Months","6m"),("1 Year","1y"),("✏️ Custom Duration","custom")]
    for i in range(0,len(presets),2):
        row=[]
        for label,key in presets[i:i+2]: row.append(InlineKeyboardButton(text=f"⏱ {label}",callback_data=f"durpreset_{key}",style='primary'))
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🔙 Back",callback_data="admin_panel_back",style='danger')])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def duration_preset_value(key):
    return {"15d":"15 Days","30d":"30 Days","2m":"2 Months","6m":"6 Months","1y":"1 Year"}.get(key)

@dp.message(AdminStates.add_prod_variant_count)
async def add_prod_variant_count(m: Message,state: FSMContext):
    try: count=int(m.text.strip())
    except ValueError: return await m.answer("❌ Send a whole number, e.g. 5.")
    if count<1 or count>50: return await m.answer("❌ Duration count must be between 1 and 50.")
    await state.update_data(variant_count=count,variant_index=1,variants=[])
    await m.answer("⏱ <b>Duration 1</b>\n\nSelect a preset or use Custom Duration. Each duration gets its own price and API mapping.",reply_markup=duration_preset_kb(),parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_variant_duration)

@dp.callback_query(F.data.startswith("durpreset_"), AdminStates.add_prod_variant_duration)
async def add_prod_duration_preset(call: CallbackQuery,state: FSMContext):
    key=call.data.split("durpreset_",1)[1]
    if key=="custom":
        await call.message.edit_text("✏️ <b>Custom Duration</b>\n\nSend duration exactly as customers should see.\nExample: <code>7 Days</code> or <code>3 Hours</code>.",reply_markup=admin_back_kb(),parse_mode='HTML')
        await call.answer(); return
    duration=duration_preset_value(key)
    if not duration: return await call.answer("❌ Invalid duration",show_alert=True)
    await state.update_data(current_duration=duration,validity=duration)
    await call.message.edit_text(f"⏱ Selected: <b>{duration}</b>\n\n💰 Enter <b>User Price</b> for this duration (₹):",reply_markup=admin_back_kb(),parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_variant_price); await call.answer()

@dp.message(AdminStates.add_prod_variant_duration)
async def add_prod_variant_duration(m: Message,state: FSMContext):
    duration=m.text.strip()
    if not duration: return await m.answer("❌ Duration cannot be empty.")
    await state.update_data(current_duration=duration,validity=duration)
    await m.answer("💰 Enter <b>User Price</b> for this duration (₹):",parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_variant_price)

@dp.message(AdminStates.add_prod_variant_price)
async def add_prod_variant_price(m: Message,state: FSMContext):
    try: price=float(m.text.strip())
    except ValueError: return await m.answer("❌ Price must be a number.")
    await state.update_data(current_price=price)
    await m.answer("👑 Enter <b>Reseller Price</b> for this duration (₹):",parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_variant_reseller)

@dp.message(AdminStates.add_prod_variant_reseller)
async def add_prod_variant_reseller(m: Message,state: FSMContext):
    try: price=float(m.text.strip())
    except ValueError: return await m.answer("❌ Reseller price must be a number.")
    await state.update_data(current_reseller_price=price)
    await m.answer("📱 Enter Device/HWID limit for this duration. Example: <code>1 Device</code>",parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_variant_device)

@dp.message(AdminStates.add_prod_variant_device)
async def add_prod_variant_device(m: Message,state: FSMContext):
    await state.update_data(current_device=m.text.strip())
    await m.answer("🔗 Enter APK/Payload link, or type <code>none</code>:",parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_variant_apk)

@dp.message(AdminStates.add_prod_variant_apk)
async def add_prod_variant_apk(m: Message,state: FSMContext):
    await state.update_data(current_apk="" if m.text.strip().lower()=="none" else m.text.strip())
    providers=db_query("SELECT id,name,requires_android_id FROM api_providers WHERE active=1 ORDER BY id",fetchall=True) or []
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🔌 {n}{' • 📱 Android ID' if req else ''}",callback_data=f"variantprov_{pid}",style='primary')] for pid,n,req in providers])
    kb.inline_keyboard.append([InlineKeyboardButton(text="📦 Manual Keys",callback_data="variantprov_manual",style='success')])
    await m.answer("🔌 <b>Select API for this duration</b>",reply_markup=kb,parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_variant_provider)

@dp.callback_query(F.data.startswith("variantprov_"),AdminStates.add_prod_variant_provider)
async def add_prod_variant_provider(call: CallbackQuery,state: FSMContext):
    token=call.data.split('_',1)[1]
    if token=='manual':
        await state.update_data(current_external=0,current_provider=None,current_pid='',current_api_duration='',current_android=0)
        await call.message.edit_text("📥 <b>Manual Keys</b>\n\nSend one key per line for this duration:",reply_markup=admin_back_kb(),parse_mode='HTML')
        await state.set_state(AdminStates.add_prod_variant_keys); return
    provider=await _get_api_provider(int(token))
    if not provider: return await call.answer("❌ API not found.",show_alert=True)
    await state.update_data(current_external=1,current_provider=int(token),provider_android=int(provider['requires_android_id']))
    await call.message.edit_text(f"🔌 <b>{provider['name']}</b> selected.\n\nEnter exact <b>API Product PID</b> for this duration:",reply_markup=admin_back_kb(),parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_variant_pid)

@dp.message(AdminStates.add_prod_variant_pid)
async def add_prod_variant_pid(m: Message,state: FSMContext):
    pid=m.text.strip()
    if not pid: return await m.answer("❌ API PID cannot be empty.")
    await state.update_data(current_pid=pid)
    await m.answer("⏱ Enter exact <b>API duration</b>. Example: <code>1 Hours</code>, <code>2 Hours</code>, <code>5 Hours</code>.",parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_variant_api_duration)

@dp.message(AdminStates.add_prod_variant_api_duration)
async def add_prod_variant_api_duration(m: Message,state: FSMContext):
    duration=normalize_api_duration(m.text.strip())
    if not duration: return await m.answer("❌ API duration cannot be empty.")
    data=await state.get_data(); req=int(data.get('provider_android',0))
    await state.update_data(current_api_duration=duration)
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📱 Yes — Android ID Required",callback_data="variant_android_yes",style='primary')],[InlineKeyboardButton(text="🌐 No — Android ID Not Required",callback_data="variant_android_no",style='success')]])
    await m.answer(f"📱 Android ID requirement for this duration?\nProvider default: <b>{'Required' if req else 'Optional'}</b>",reply_markup=kb,parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_variant_android)

@dp.callback_query(F.data.in_({"variant_android_yes","variant_android_no"}),AdminStates.add_prod_variant_android)
async def add_prod_variant_android(call: CallbackQuery,state: FSMContext):
    await safe_call_answer(call)
    data=await state.get_data(); req=1 if call.data.endswith('_yes') else 0
    await _finish_product_variant(call.message,state,req)

async def _finish_product_variant(message: Message,state: FSMContext,android_required:int):
    data=await state.get_data(); conn=sqlite3.connect(DB_FILE); c=conn.cursor()
    name=data['name']; duration=data['current_duration']; variant_name=duration
    c.execute("""INSERT INTO products (category,panel_name,name,product_group,price_inr,reseller_price,stock,apk_link,validity,device_limit,external_enabled,external_product_id,requires_android_id,external_duration,api_provider_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (data['cat'],data['panel_name'],variant_name,name,data['current_price'],data['current_reseller_price'],1,data['current_apk'],duration,data['current_device'],1 if data.get('current_external') else 0,data.get('current_pid',''),android_required,data.get('current_api_duration',''),data.get('current_provider')))
    prod_id=c.lastrowid; conn.commit(); conn.close()
    await _after_variant_saved(message,state,prod_id)

async def _after_variant_saved(message:Message,state:FSMContext,prod_id:int):
    data=await state.get_data(); idx=int(data.get('variant_index',1)); count=int(data.get('variant_count',1))
    if idx>=count:
        await message.answer(f"✅ <b>Product Created</b>\n\n📦 <b>{data['name']}</b>\n⏱ {count} duration option(s) created.",reply_markup=admin_kb(),parse_mode='HTML'); await state.clear(); return
    await state.update_data(variant_index=idx+1)
    await message.answer(f"✅ Duration {idx} saved.\n\n⏱ <b>Duration {idx+1}/{count}</b>\nEnter duration, e.g. <code>1 Hour</code> or <code>2 Hours</code>:",reply_markup=admin_back_kb(),parse_mode='HTML')
    await state.set_state(AdminStates.add_prod_variant_duration)

@dp.message(AdminStates.add_prod_variant_keys)
async def add_prod_variant_keys(m: Message,state: FSMContext):
    keys=[k.strip() for k in m.text.strip().split('\n') if k.strip()]
    if not keys: return await m.answer("❌ Send at least one key.")
    data=await state.get_data(); conn=sqlite3.connect(DB_FILE); c=conn.cursor()
    c.execute("""INSERT INTO products (category,panel_name,name,product_group,price_inr,reseller_price,stock,apk_link,validity,device_limit,external_enabled,external_product_id,requires_android_id,external_duration,api_provider_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (data['cat'],data['panel_name'],data['current_duration'],data['name'],data['current_price'],data['current_reseller_price'],len(keys),data['current_apk'],data['current_duration'],data['current_device'],0,'',0,'',None))
    prod_id=c.lastrowid
    for k in keys: c.execute("INSERT INTO product_keys(product_id,key_text) VALUES(?,?)",(prod_id,k))
    conn.commit(); conn.close(); await _after_variant_saved(m,state,prod_id)

@dp.callback_query(F.data == "admin_manage_prods")
async def admin_manage_prods(call: CallbackQuery):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    prods = db_query("SELECT id, name, stock, is_active FROM products", fetchall=True)
    prods = sorted(prods or [], key=lambda row: natural_sort_key(row[1]))
    if not prods: return await call.message.edit_text("📦 Store Database is completely empty.", reply_markup=admin_back_kb(), parse_mode='HTML')
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for p in prods:
        status_dot = "🟢" if p[3] else "🔴"
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"{status_dot} {p[1]}  • Stock: {p[2]}", callback_data=f"admin_view_p_{p[0]}", style="primary")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text("📦 <b>Manage Products</b>\n\nSelect a product:", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("admin_view_p_"))
async def admin_view_product(call: CallbackQuery):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    try:
        p_id=int(call.data.split("_")[3])
        prod=db_query("""SELECT id,name,product_group,price_inr,reseller_price,stock,apk_link,validity,device_limit,is_active,external_enabled,external_product_id,requires_android_id,external_duration,api_provider_id FROM products WHERE id=?""",(p_id,),fetchone=True)
        if not prod: return await call.answer("❌ Product not found.",show_alert=True)
        text=(f"📦 <b>PRODUCT DETAILS</b>\n━━━━━━━━━━━━━━━━━━\n<b>ID:</b> <code>{prod[0]}</code>\n<b>Product:</b> {prod[1]}\n<b>Group:</b> {prod[2] or prod[1]}\n<b>Price:</b> ₹{float(prod[3] or 0):.2f}\n<b>Reseller:</b> ₹{float(prod[4] or 0):.2f}\n<b>Stock:</b> {prod[5]}\n<b>Duration:</b> {prod[7]}\n<b>HWID:</b> {prod[8]}\n<b>API Mode:</b> {'♾️ External API' if prod[10] else '🔢 Manual'}\n<b>API PID:</b> {prod[11] or '—'}\n<b>API Duration:</b> {prod[13] or '—'}\n<b>Visibility:</b> {'Active' if prod[9] else 'Hidden'}\n━━━━━━━━━━━━━━━━━━")
        toggle_btn_text="Hide Product 👁‍🗨" if prod[9] else "Unhide Product 👁"
        kb=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Edit Package Name ✏️",callback_data=f"edit_p_{p_id}_name",style='primary')],
            [InlineKeyboardButton(text="Edit Price 💰",callback_data=f"edit_p_{p_id}_price",style='primary'),InlineKeyboardButton(text="Edit R-Price 👑",callback_data=f"edit_p_{p_id}_rprice",style='primary')],
            [InlineKeyboardButton(text="Edit Duration ⏱️",callback_data=f"edit_p_{p_id}_validity",style='primary'),InlineKeyboardButton(text="Edit Device 📱",callback_data=f"edit_p_{p_id}_device",style='primary')],
            [InlineKeyboardButton(text="Edit API Duration ⏱️",callback_data=f"edit_p_{p_id}_api_duration",style='primary'),InlineKeyboardButton(text="🔌 Change API",callback_data=f"edit_p_{p_id}_api_provider",style='primary')],
            [InlineKeyboardButton(text="Edit APK Link 🔗",callback_data=f"edit_p_{p_id}_apk",style='primary'),InlineKeyboardButton(text="Add Keys ➕",callback_data=f"edit_p_{p_id}_keys",style='success')],
            [InlineKeyboardButton(text="Add to Stock 📦➕",callback_data=f"edit_p_{p_id}_stock_add",style='success')],
            [InlineKeyboardButton(text=toggle_btn_text,callback_data=f"toggle_p_{p_id}",style='primary'),InlineKeyboardButton(text="Nuke Full Node 🗑",callback_data=f"delete_p_{p_id}",style='danger')],
            [InlineKeyboardButton(text="BACK",callback_data="admin_manage_prods",style='danger')]
        ])
        await call.message.edit_text(text,reply_markup=kb,disable_web_page_preview=True,parse_mode='HTML')
    except Exception as e:
        logger.exception("Error in admin_view_product: %s",e)
        await call.message.edit_text("❌ Error loading product.",reply_markup=admin_back_kb(),parse_mode='HTML')

@dp.callback_query(F.data.startswith("toggle_p_"))
async def admin_toggle_product(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    p_id = int(call.data.split("_")[2])
    current = db_query("SELECT is_active FROM products WHERE id=?", (p_id,), fetchone=True)[0]
    new_val = 0 if current == 1 else 1
    db_query("UPDATE products SET is_active=? WHERE id=?", (new_val, p_id))
    await call.answer("Visibility updated successfully!", show_alert=True)
    await admin_view_product(call)

# ==============================================================================
# FIX: Edit product field – correctly handle different data types
# ==============================================================================
@dp.callback_query(F.data.startswith("edit_p_") and F.data.endswith("_api_provider"))
async def edit_product_api_provider(call: CallbackQuery):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    p_id=int(call.data.split('_')[2])
    providers=db_query("SELECT id,name,requires_android_id FROM api_providers WHERE active=1 ORDER BY id",fetchall=True) or []
    kb=InlineKeyboardMarkup(inline_keyboard=[])
    for pid,name,req in providers:
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"🔌 {name}{' • 📱 Android ID' if req else ''}",callback_data=f"setprodapi_{p_id}_{pid}",style="primary")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="🚫 Legacy Global API",callback_data=f"setprodapi_{p_id}_0",style="primary")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="BACK",callback_data=f"admin_view_p_{p_id}",style="danger")])
    await call.message.edit_text("🔌 <b>Select API Provider for this product</b>",reply_markup=kb,parse_mode='HTML')

@dp.callback_query(F.data.startswith("setprodapi_"))
async def set_product_api_provider(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    parts=call.data.split('_'); p_id=int(parts[1]); provider_id=int(parts[2])
    if provider_id:
        provider=await _get_api_provider(provider_id)
        if not provider: return await call.answer("API not found",show_alert=True)
        db_query("UPDATE products SET api_provider_id=?, external_enabled=1, requires_android_id=? WHERE id=?",(provider_id,int(provider['requires_android_id']),p_id))
    else:
        db_query("UPDATE products SET api_provider_id=NULL WHERE id=?",(p_id,))
    await call.answer("✅ Product API updated",show_alert=True)
    await admin_view_product(call)

@dp.callback_query(F.data.startswith("edit_p_"))
async def start_edit_product(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    parts = call.data.split("_")
    p_id = int(parts[2]); field = "_".join(parts[3:])
    await state.update_data(edit_p_id=p_id, edit_field=field)
    if field == 'keys':
        await call.message.edit_text("📥 <b>Vault Injection</b>\nPaste the <b>NEW KEYS</b> to append to the stock (1 key per line):", reply_markup=admin_back_kb(), parse_mode='HTML')
        await state.set_state(AdminStates.wait_for_add_keys)
    elif field == 'stock_add':
        await call.message.edit_text(
            "📦 <b>Add to Stock</b>\n\nEnter how many stock units to add.\nExample: <code>100</code> or <code>200</code>",
            reply_markup=admin_back_kb(), parse_mode='HTML'
        )
        await state.set_state(AdminStates.wait_for_new_value)
    else:
        field_name_map = {'cat': 'New Panel Group/Category Name', 'panel_name': 'New Panel Name', 'name': 'New Package/Date Name', 'price': 'New Standard Price in ₹', 'rprice': 'New Reseller Price in ₹', 'validity': 'New Time Validity String', 'device': 'New HWID Limit String', 'apk': 'New Payload Link (or type "none")', 'api_duration': 'Exact XYZ API Duration (e.g. 1 Day)', 'stock_add': 'Stock units to add'}
        await call.message.edit_text(f"✏️ Input the required data for: <b>{field_name_map[field]}</b>", reply_markup=admin_back_kb(), parse_mode='HTML')
        await state.set_state(AdminStates.wait_for_new_value)

@dp.message(AdminStates.wait_for_new_value)
async def process_edit_value(m: Message, state: FSMContext):
    data = await state.get_data()
    p_id = data['edit_p_id']; field = data['edit_field']; new_val = m.text.strip()
    
    # If field is price or reseller price, convert to float
    if field in ['price', 'rprice']:
        try:
            new_val = float(new_val)
        except ValueError:
            return await m.answer("❌ Invalid number format. Please enter a valid price (e.g., 500).")
    # If field is apk, store as string (don't convert to float!)
    elif field == 'apk':
        new_val = "" if new_val.lower() == 'none' else new_val
    elif field == 'stock_add':
        try:
            add_qty = int(new_val)
            if add_qty <= 0 or add_qty > 100000:
                raise ValueError
        except ValueError:
            return await m.answer("❌ Enter a positive whole number up to 100000, e.g. 100 or 200.")
        db_query("UPDATE products SET stock=COALESCE(stock,0)+? WHERE id=?", (add_qty, p_id))
        new_stock = db_query("SELECT stock FROM products WHERE id=?", (p_id,), fetchone=True)[0]
        await m.answer(f"✅ <b>Stock Added!</b>\n\n➕ Added: <code>{add_qty}</code>\n📦 New Stock: <code>{new_stock}</code>", reply_markup=admin_kb(), parse_mode='HTML')
        await state.clear()
        return
    # For all other fields (cat, panel_name, name, validity, device), keep as string
    
    db_col_map = {'cat': 'category', 'panel_name': 'panel_name', 'name': 'name', 'price': 'price_inr', 'rprice': 'reseller_price', 'validity': 'validity', 'device': 'device_limit', 'apk': 'apk_link', 'api_duration': 'external_duration'}
    db_query(f"UPDATE products SET {db_col_map[field]}=? WHERE id=?", (new_val, p_id))
    await m.answer("✅ <b>Node updated gracefully!</b>", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.message(AdminStates.wait_for_add_keys)
async def process_add_keys(m: Message, state: FSMContext):
    data = await state.get_data()
    p_id = data['edit_p_id']
    keys = [k.strip() for k in m.text.strip().split('\n') if k.strip()]
    if len(keys) == 0: return await m.answer("❌ Protocol breach: Zero valid keys found.", reply_markup=admin_kb(), parse_mode='HTML')
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    for k in keys: c.execute("INSERT INTO product_keys (product_id, key_text) VALUES (?, ?)", (p_id, k))
    c.execute("UPDATE products SET stock = stock + ? WHERE id=?", (len(keys), p_id))
    conn.commit(); conn.close()
    await m.answer(f"✅ <b>Vault Secure!</b> {len(keys)} new keys appended and encrypted.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data.startswith("delete_p_"))
async def admin_delete_product(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    p_id = int(call.data.split("_")[2])
    db_query("DELETE FROM products WHERE id=?", (p_id,))
    db_query("DELETE FROM product_keys WHERE product_id=?", (p_id,))
    await call.answer("☢️ Nuclear wipe successful! Node and vault deleted.", show_alert=True)
    await admin_manage_prods(call)

@dp.callback_query(F.data.startswith("delkey_p_"))
async def admin_delete_key_start(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    p_id = int(call.data.split("_")[2])
    await state.update_data(del_p_id=p_id)
    await call.message.edit_text("🗑 Send the <b>exact string match</b> of the key you wish to purge from the vault:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_delete_key)

@dp.message(AdminStates.wait_for_delete_key)
async def process_delete_key(m: Message, state: FSMContext):
    data = await state.get_data()
    p_id = data['del_p_id']
    key_to_delete = m.text.strip()
    key_data = db_query("SELECT id, is_used FROM product_keys WHERE product_id=? AND key_text=?", (p_id, key_to_delete), fetchone=True)
    if not key_data: return await m.answer("❌ Key not found. Check logs and try again.", reply_markup=admin_back_kb(), parse_mode='HTML')
    if key_data[1] == 1: return await m.answer("⚠️ Action Blocked: This key has already been dispatched to a user.", reply_markup=admin_back_kb(), parse_mode='HTML')
    db_query("DELETE FROM product_keys WHERE id=?", (key_data[0],))
    db_query("UPDATE products SET stock = stock - 1 WHERE id=?", (p_id,))
    await m.answer(f"✅ Key <code>{key_to_delete}</code> securely purged from vault.\n📦 Database indices updated.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

# ==============================================================================
# 20. ADMIN TICKETS, BROADCAST, COUPONS
# ==============================================================================
@dp.callback_query(F.data == "admin_view_tickets")
async def admin_view_tickets(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Access denied.", show_alert=True)
        return
    await call.answer()
    tickets = db_query("SELECT id, user_id, message, created_at FROM tickets WHERE status='Open' ORDER BY id ASC LIMIT 1", fetchall=True) or []
    if not tickets:
        await call.message.edit_text("🎫 <b>Support Tickets</b>\n\n✅ No open support tickets right now.", reply_markup=admin_back_kb(), parse_mode='HTML')
        return
    t = tickets[0]
    text = (f"🎫 <b><u>ACTIVE TICKET #{t[0]}</u></b>\n👤 <b>Origin UID:</b> <code>{t[1]}</code>\n📅 <b>Timestamp:</b> {t[3]}\n\n📝 <b>Payload:</b>\n{t[2]}")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Formulate Reply", callback_data=f"reply_ticket_{t[0]}_{t[1]}", icon_custom_emoji_id=get_emoji_icon("telegram"), style="primary")],
        [InlineKeyboardButton(text="❌ Force Close Ticket", callback_data=f"close_ticket_{t[0]}", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("close_ticket_"))
async def close_ticket(call: CallbackQuery):
    ticket_id = call.data.split("_")[2]
    db_query("UPDATE tickets SET status='Closed' WHERE id=?", (ticket_id,))
    await call.answer("✅ Status set to Closed.", show_alert=True)
    await admin_view_tickets(call) 

@dp.callback_query(F.data.startswith("reply_ticket_"))
async def reply_ticket_start(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    data = call.data.split("_")
    ticket_id, user_id = data[2], data[3]
    await state.update_data(ticket_id=ticket_id, user_id=user_id)
    await call.message.edit_text(f"💬 Formulating reply for node <code>{user_id}</code>.\n\nType your message payload:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.ticket_reply_msg)

@dp.message(AdminStates.ticket_reply_msg)
async def send_ticket_reply(m: Message, state: FSMContext):
    data = await state.get_data()
    try:
        await bot.send_message(data['user_id'], f"📞 <b>Admin Reply (Ref #{data['ticket_id']}):</b>\n\n{m.text}", parse_mode='HTML')
        db_query("UPDATE tickets SET status='Closed' WHERE id=?", (data['ticket_id'],))
        await m.answer("✅ Payload delivered and connection closed successfully.", reply_markup=admin_kb(), parse_mode='HTML')
    except Exception as e: await m.answer(f"❌ Transmission Error: {e}", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_broadcast_btn")
async def admin_broadcast_start(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    await call.message.edit_text("📢 <b>Mass Broadcast Protocol</b>\n\nSend the rich message payload you wish to transmit globally across the grid:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.broadcast_msg)

@dp.message(AdminStates.broadcast_msg)
async def admin_broadcast_send(message: Message, state: FSMContext):
    users = db_query("SELECT user_id FROM users", fetchall=True)
    sent, failed = 0, 0
    m = await message.answer("⏳ Broadcast protocol initiated... Do not interrupt.", parse_mode='HTML')
    for u in users:
        try:
            await message.send_copy(chat_id=u[0])
            sent += 1
        except Exception: failed += 1
        await asyncio.sleep(0.06) 
    await m.edit_text(f"✅ <b>Global Broadcast Complete!</b>\n\n🟢 Nodes reached: {sent}\n🔴 Nodes failed/blocked: {failed}", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_create_coupon")
async def admin_create_coupon_start(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    await call.message.edit_text("🎟 Enter a highly secure alphanumeric sequence for the Promo Code:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.add_coupon_code)

@dp.message(AdminStates.add_coupon_code)
async def admin_coupon_code(m: Message, state: FSMContext):
    await state.update_data(code=m.text.strip().upper())
    await m.answer("💰 Enter the monetary reward payload in <b>RUPEES (₹)</b>:", parse_mode='HTML')
    await state.set_state(AdminStates.add_coupon_amount)

@dp.message(AdminStates.add_coupon_amount)
async def admin_coupon_amount(m: Message, state: FSMContext):
    try:
        await state.update_data(amount=float(m.text)) 
        await m.answer("👥 Enter the exact maximum threshold uses for this code:", parse_mode='HTML')
        await state.set_state(AdminStates.add_coupon_uses)
    except ValueError: await m.answer("❌ Non-numerical data detected. Aborting.")

@dp.message(AdminStates.add_coupon_uses)
async def admin_coupon_uses(m: Message, state: FSMContext):
    try:
        uses = int(m.text)
        data = await state.get_data()
        db_query("INSERT OR REPLACE INTO coupons (code, amount, uses_left) VALUES (?, ?, ?)", (data['code'], data['amount'], uses))
        await m.answer(f"✅ Protocol <b>{data['code']}</b> encoded!\nReward Vector: {fmt_curr(data['amount'])}\nThreshold Limit: {uses} executions.", reply_markup=admin_kb(), parse_mode='HTML')
        await state.clear()
    except ValueError: await m.answer("❌ Non-numerical data detected. Aborting.")

# ==============================================================================
# 21. ADMIN RESELLER & SPIN SETTINGS
# ==============================================================================
@dp.callback_query(F.data == "admin_reseller_menu")
async def admin_reseller_menu(call: CallbackQuery):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    status_check = db_query("SELECT value FROM settings WHERE key='reseller_system_status'", fetchone=True)
    sys_status = status_check[0] if status_check else "ON"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Grant Reseller Rights", callback_data="reseller_make", icon_custom_emoji_id=get_emoji_icon("reseller"), style="success"), 
         InlineKeyboardButton(text="➖ Revoke Reseller", callback_data="reseller_remove", icon_custom_emoji_id=get_emoji_icon("reseller"), style="danger")],
        [InlineKeyboardButton(text="📋 Audit Active Resellers", callback_data="reseller_view", icon_custom_emoji_id=get_emoji_icon("history"), style="primary")],
        [InlineKeyboardButton(text=f"{'🟢' if sys_status == 'ON' else '🔴'} Auto-Upgrade System: {sys_status}", callback_data="admin_toggle_reseller_sys", icon_custom_emoji_id=get_emoji_icon("check_icon"), style="success" if sys_status == 'ON' else "danger")], 
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text("👑 <b>Wholesale Reseller Protocols</b>\nSelect administrative action:", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "admin_toggle_reseller_sys")
async def toggle_reseller_sys(call: CallbackQuery):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    res = db_query("SELECT value FROM settings WHERE key='reseller_system_status'", fetchone=True)
    current = res[0] if res else 'ON'
    new_status = 'OFF' if current == 'ON' else 'ON'
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('reseller_system_status', ?)", (new_status,))
    await admin_reseller_menu(call)

@dp.callback_query(F.data.in_(["reseller_make", "reseller_remove"]))
async def reseller_prompt_id(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    action = call.data
    await state.update_data(reseller_action=action)
    await call.message.edit_text("👤 Identify target node. Input <b>User ID</b> or <b>@username</b>:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.reseller_manage_id)

@dp.message(AdminStates.reseller_manage_id)
async def process_reseller_manage(m: Message, state: FSMContext):
    data = await state.get_data()
    target = m.text.strip()
    if target.startswith('@'): target = target[1:]
    user_q = db_query("SELECT user_id, first_name FROM users WHERE user_id=? OR username=? COLLATE NOCASE", (target, target), fetchone=True)
    if not user_q: return await m.answer("❌ Target completely ghosted. Not in database.", reply_markup=admin_back_kb(), parse_mode='HTML')
    u_id, u_name = user_q[0], user_q[1]
    if data['reseller_action'] == "reseller_make":
        db_query("UPDATE users SET is_reseller=1, reseller_since=?, account_type='Reseller' WHERE user_id=?", (datetime.now().strftime("%Y-%m-%d"), u_id))
        await m.answer(f"✅ Credentials upgraded. <b>{u_name}</b> (<code>{u_id}</code>) has reseller rights.", reply_markup=admin_kb(), parse_mode='HTML')
    else:
        db_query("UPDATE users SET is_reseller=0, account_type='Regular' WHERE user_id=?", (u_id,))
        await m.answer(f"✅ Credentials revoked. <b>{u_name}</b> (<code>{u_id}</code>) is back to regular user.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "reseller_view")
async def reseller_view(call: CallbackQuery):
    await safe_call_answer(call)
    resellers = db_query("SELECT user_id, first_name, username FROM users WHERE is_reseller=1", fetchall=True)
    if not resellers: return await call.message.edit_text("📋 Zero active resellers found.", reply_markup=admin_back_kb(), parse_mode='HTML')
    text = "👑 <b><u>ACTIVE RESELLER AUDIT LOG</u></b> 👑\n━━━━━━━━━━━━━━━━━━\n"
    for r in resellers:
        uname = f"(@{r[2]})" if r[2] else ""
        text += f"👤 {r[1]} {uname}\n🆔 <code>{r[0]}</code>\n\n"
    await call.message.edit_text(text, reply_markup=admin_back_kb(), parse_mode='HTML')

@dp.callback_query(F.data == "ignore_stock_click")
async def ignore_stock_click(call: CallbackQuery):
    await safe_call_answer(call, "❌ This option is currently out of stock.", show_alert=True)


@dp.callback_query(F.data == "admin_spin_menu")
async def admin_spin_menu(call: CallbackQuery):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    status = db_query("SELECT value FROM settings WHERE key='spin_status'", fetchone=True)
    limit = db_query("SELECT value FROM settings WHERE key='daily_spin_limit'", fetchone=True)
    status_val = status[0] if status else 'ON'
    limit_val = limit[0] if limit else '50.0'
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Append Reward Logic", callback_data="spin_add", icon_custom_emoji_id=get_emoji_icon("add_balance"), style="success"), 
         InlineKeyboardButton(text="❌ Drop Reward Logic", callback_data="spin_del", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")],
        [InlineKeyboardButton(text="📋 Audit Configs", callback_data="spin_view", icon_custom_emoji_id=get_emoji_icon("history"), style="primary"), 
         InlineKeyboardButton(text="⚙️ Throttle Limits", callback_data="spin_limit", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
        [InlineKeyboardButton(text=f"{'🟢' if status_val == 'ON' else '🔴'} Master Toggle: {status_val}", callback_data="spin_toggle", icon_custom_emoji_id=get_emoji_icon("check_icon"), style="success" if status_val == 'ON' else "danger")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text(f"🎰 <b>Advanced Ludo/Spin Algorithms</b>\nCurrent Threshold: ₹{limit_val}", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "spin_del")
async def spin_delete_reward(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    row = db_query("SELECT id, amount FROM spin_rewards ORDER BY id DESC LIMIT 1", fetchone=True)
    if not row:
        return await safe_call_answer(call, "ℹ️ No reward logic exists.", show_alert=True)
    db_query("DELETE FROM spin_rewards WHERE id=?", (row[0],))
    await safe_call_answer(call, "✅ Last reward removed.", show_alert=True)
    await admin_spin_menu(call)


@dp.callback_query(F.data == "spin_limit")
async def spin_limit_start(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id):
        return
    current = get_setting("daily_spin_limit", "50.0")
    await call.message.edit_text(
        f"⚙️ <b>Spin Throttle Limit</b>\n\nCurrent limit: <b>₹{current}</b>\n\nSend the new numeric limit. Example: <code>50</code>.",
        reply_markup=admin_back_kb(), parse_mode="HTML"
    )
    await state.set_state(AdminStates.spin_limit)


@dp.message(AdminStates.spin_limit)
async def spin_limit_save(m: Message, state: FSMContext):
    try:
        value = float((m.text or "").strip())
        if value < 0:
            raise ValueError
    except ValueError:
        return await m.answer("❌ Send a valid non-negative number.")
    set_setting("daily_spin_limit", str(value))
    await state.clear()
    await m.answer(f"✅ Spin limit saved: ₹{value:.2f}", reply_markup=admin_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "spin_toggle")
async def spin_toggle(call: CallbackQuery):
    await safe_call_answer(call)
    res = db_query("SELECT value FROM settings WHERE key='spin_status'", fetchone=True)
    current = res[0] if res else 'ON'
    new_status = 'OFF' if current == 'ON' else 'ON'
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('spin_status', ?)", (new_status,))
    await admin_spin_menu(call)

@dp.callback_query(F.data == "admin_toggle_bot")
async def toggle_bot(call: CallbackQuery):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    res = db_query("SELECT value FROM settings WHERE key='bot_status'", fetchone=True, commit=False)
    current = res[0] if res else 'ON'
    new_status = 'OFF' if current == 'ON' else 'ON'
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('bot_status', ?)", (new_status,))
    await call.message.edit_reply_markup(reply_markup=admin_kb())

@dp.callback_query(F.data == "spin_add")
async def spin_add_start(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    await call.message.edit_text("🎰 Inject new decimal logic limit (e.g. 15.50):", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.spin_add_reward)

@dp.message(AdminStates.spin_add_reward)
async def spin_add_exec(m: Message, state: FSMContext):
    try:
        amt = float(m.text)
        db_query("INSERT INTO spin_rewards (amount) VALUES (?)", (amt,))
        await m.answer(f"✅ Algorithm updated. New vector {fmt_curr(amt)} injected.", reply_markup=admin_kb(), parse_mode='HTML')
        await state.clear()
    except ValueError: await m.answer("❌ Math parsing error.")

@dp.callback_query(F.data == "spin_view")
async def spin_view(call: CallbackQuery):
    await safe_call_answer(call)
    rewards = db_query("SELECT amount FROM spin_rewards ORDER BY amount ASC", fetchall=True)
    text = "🎰 <b>Live Ludo Constants</b>\n\n"
    for r in rewards: text += f"🎁 {fmt_curr(r[0])}\n"
    await call.message.edit_text(text, reply_markup=admin_back_kb(), parse_mode='HTML')

@dp.callback_query(F.data == "admin_set_video")
async def admin_set_video_start(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    await call.message.edit_text("📹 Input direct streaming / YouTube Link for Tutorial system:\n<i>(Or type 'None' to clear registry):</i>", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_howto_video)

@dp.message(AdminStates.wait_for_howto_video)
async def exec_set_video(m: Message, state: FSMContext):
    link = m.text.strip()
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('how_to_video', ?)", (link,))
    await m.answer("✅ Routing complete. Video linked.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_set_all_files")
async def admin_set_all_files_start(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    await call.message.edit_text("🔗 Input the direct Channel / Cloud URL for 'Download Files' button:\n<i>(Or type 'None' to format data):</i>", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_all_files_link)

@dp.message(AdminStates.wait_for_all_files_link)
async def exec_set_all_files(m: Message, state: FSMContext):
    link = m.text.strip()
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES ('all_files_link', ?)", (link,))
    await m.answer("✅ Global resource variable updated.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_edit_emojis")
async def admin_edit_emojis(call: CallbackQuery):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    rows = db_query("SELECT key, value FROM settings WHERE key LIKE 'emoji_%' ORDER BY key", fetchall=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for row in rows:
        key = row[0]
        slot = key.replace("emoji_", "")
        current_id = row[1] if row[1] else "Not set"
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"{slot} (ID: {current_id})", callback_data=f"edit_emoji_{slot}", style="primary")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text("🎨 <b>Edit All Emojis</b>\nChoose an emoji slot to change its ID:", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("edit_emoji_"))
async def admin_edit_emoji_prompt(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    slot = call.data.split("edit_emoji_", 1)[1]
    await state.update_data(emoji_slot=slot)
    current = get_setting(f"emoji_{slot}", "Not set")
    await call.message.edit_text(f"✏️ Enter new emoji ID for <b>{slot}</b>:\nCurrent: {current}\n(Leave empty to reset to default)", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_emoji_slot)

@dp.message(AdminStates.wait_for_emoji_slot)
async def save_emoji_slot(m: Message, state: FSMContext):
    data = await state.get_data()
    slot = data['emoji_slot']
    new_id = m.text.strip()
    if new_id == "":
        db_query("DELETE FROM settings WHERE key=?", (f"emoji_{slot}",))
        await m.answer(f"✅ Reset emoji for '{slot}' to default.", reply_markup=admin_kb(), parse_mode='HTML')
    else:
        if not new_id.isdigit():
            await m.answer("❌ Invalid ID! Must be numeric.", reply_markup=admin_kb(), parse_mode='HTML')
            return
        set_setting(f"emoji_{slot}", new_id)
        await m.answer(f"✅ Emoji for '{slot}' updated to ID {new_id}.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_edit_ui_menu")
async def admin_edit_ui_menu(call: CallbackQuery):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Edit Start Menu Text", callback_data="edit_ui_start", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
        [InlineKeyboardButton(text="Edit Download Files Text", callback_data="edit_ui_download", icon_custom_emoji_id=get_emoji_icon("download"), style="primary")],
        [InlineKeyboardButton(text="Edit VIP Menu Text", callback_data="edit_ui_vip", icon_custom_emoji_id=get_emoji_icon("vip"), style="primary")],
        [InlineKeyboardButton(text="Edit Lucky Dice Text", callback_data="edit_ui_dice", icon_custom_emoji_id=get_emoji_icon("ludo_spin"), style="primary")],
        [InlineKeyboardButton(text="Edit Add Balance Text", callback_data="edit_ui_add_balance", icon_custom_emoji_id=get_emoji_icon("add_balance"), style="primary")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text("✏️ <b>Edit User Interface Texts</b>\nSelect which text you want to modify:", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("edit_ui_"))
async def admin_edit_ui_prompt(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    ui_key = call.data.split("_")[2]
    await state.update_data(ui_key=ui_key)
    current_text = get_ui_text(ui_key)
    await call.message.edit_text(f"📝 Send the new text for <b>{ui_key.upper()}</b> menu.\n\nCurrent text:\n{current_text}", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.edit_ui_text)

@dp.message(AdminStates.edit_ui_text)
async def admin_save_ui_text(m: Message, state: FSMContext):
    data = await state.get_data()
    ui_key = data['ui_key']
    new_text = m.text
    db_query("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (f"ui_{ui_key}", new_text))
    await m.answer(f"✅ UI text <b>{ui_key}</b> updated successfully!", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_edit_reseller_price")
async def admin_edit_reseller_price_start(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    prods = db_query("SELECT id, name, category, panel_name, reseller_price FROM products", fetchall=True)
    prods = sorted(prods or [], key=lambda row: (natural_sort_key(row[2]), natural_sort_key(row[3]), natural_sort_key(row[1])))
    if not prods: return await call.message.edit_text("No products to edit.", reply_markup=admin_back_kb(), parse_mode='HTML')
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for p in prods:
        panel_name = p[3] if p[3] is not None else ""
        r_price = float(p[4]) if p[4] is not None else 0.0
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"{p[2]} - {panel_name} - {p[1]} (₹{r_price:.2f})", callback_data=f"edit_reseller_{p[0]}", icon_custom_emoji_id=get_emoji_icon("money_icon"), style="primary")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text("👑 <b>Edit Reseller Price per Product</b>\nSelect a product to change its wholesale price:", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("edit_reseller_"))
async def admin_edit_reseller_price_prompt(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    prod_id = int(call.data.split("_")[2])
    await state.update_data(edit_reseller_prod_id=prod_id)
    await call.message.edit_text("💰 Enter the new <b>Reseller Price</b> in Rupees (₹) for this product:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.edit_reseller_price)

@dp.message(AdminStates.edit_reseller_price)
async def admin_save_reseller_price(m: Message, state: FSMContext):
    try:
        new_price = float(m.text)
        data = await state.get_data()
        prod_id = data['edit_reseller_prod_id']
        db_query("UPDATE products SET reseller_price=? WHERE id=?", (new_price, prod_id))
        await m.answer(f"✅ Reseller price updated to {fmt_curr(new_price)} for product ID {prod_id}.", reply_markup=admin_kb(), parse_mode='HTML')
        await state.clear()
    except ValueError: await m.answer("❌ Invalid number. Please enter a valid price.")

@dp.callback_query(F.data == "admin_set_reseller_fee")
async def admin_set_reseller_fee(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    await call.message.edit_text("💰 Enter the new <b>Reseller Setup Fee</b> in Rupees (₹):\nCurrent: " + get_setting("reseller_setup_fee", "200.0"), reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_reseller_setup_fee)

@dp.message(AdminStates.wait_for_reseller_setup_fee)
async def admin_save_reseller_fee(m: Message, state: FSMContext):
    try:
        fee = float(m.text)
        set_setting("reseller_setup_fee", str(fee))
        await m.answer(f"✅ Reseller setup fee updated to {fmt_curr(fee)}.", reply_markup=admin_kb(), parse_mode='HTML')
        await state.clear()
    except ValueError: await m.answer("❌ Invalid number. Please enter a valid amount.")

@dp.callback_query(F.data == "admin_set_reseller_min")
async def admin_set_reseller_min(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    await call.message.edit_text("💳 Enter the new <b>Minimum Balance</b> required to become reseller (₹):\nCurrent: " + get_setting("reseller_min_balance", "500.0"), reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_reseller_min_balance)

@dp.message(AdminStates.wait_for_reseller_min_balance)
async def admin_save_reseller_min(m: Message, state: FSMContext):
    try:
        min_bal = float(m.text)
        set_setting("reseller_min_balance", str(min_bal))
        await m.answer(f"✅ Minimum reseller balance updated to {fmt_curr(min_bal)}.", reply_markup=admin_kb(), parse_mode='HTML')
        await state.clear()
    except ValueError: await m.answer("❌ Invalid number. Please enter a valid amount.")

@dp.callback_query(F.data == "admin_set_support_links")
async def admin_set_support_links(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Access denied.", show_alert=True)
        return
    await call.answer()
    tg = get_setting("support_telegram", "Not set")
    wa = get_setting("support_whatsapp", "Not set")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📞 Set Telegram Link", callback_data="admin_set_telegram", icon_custom_emoji_id=get_emoji_icon("telegram"), style="primary")],
        [InlineKeyboardButton(text="📱 Set WhatsApp Link", callback_data="admin_set_whatsapp", icon_custom_emoji_id=get_emoji_icon("whatsapp"), style="primary")],
        [InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    await call.message.edit_text(
        "📌 <b>Support Contact Links</b>\n\n"
        f"✈️ Telegram: <code>{tg}</code>\n"
        f"📱 WhatsApp: <code>{wa}</code>\n\n"
        "Set the URLs below. The user Support menu will automatically hide any invalid/unset link.",
        reply_markup=kb, parse_mode='HTML'
    )

@dp.callback_query(F.data == "admin_set_telegram")
async def admin_set_telegram(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    await call.message.edit_text("✈️ Enter the Telegram contact URL (e.g., https://t.me/YOUR_SUPPORT):", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_support_telegram)

@dp.message(AdminStates.wait_for_support_telegram)
async def save_telegram_link(m: Message, state: FSMContext):
    link = (m.text or "").strip()
    if not _valid_support_url(link, "telegram"):
        await m.answer("❌ Invalid Telegram URL. Use a link like https://t.me/YourUsername", reply_markup=admin_back_kb(), parse_mode='HTML')
        return
    set_setting("support_telegram", link)
    await m.answer("✅ Telegram support link updated!", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_set_whatsapp")
async def admin_set_whatsapp(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    await call.message.edit_text("📱 Enter the WhatsApp contact URL (e.g., https://wa.me/1234567890):", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_support_whatsapp)

@dp.message(AdminStates.wait_for_support_whatsapp)
async def save_whatsapp_link(m: Message, state: FSMContext):
    link = (m.text or "").strip()
    if not _valid_support_url(link, "whatsapp"):
        await m.answer("❌ Invalid WhatsApp URL. Use a link like https://wa.me/919876543210", reply_markup=admin_back_kb(), parse_mode='HTML')
        return
    set_setting("support_whatsapp", link)
    await m.answer("✅ WhatsApp support link updated!", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_set_category_emojis")
async def admin_set_category_emojis(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for cat in FIXED_CATEGORIES:
        current = get_setting(f"cat_emoji_{cat}", "")
        status = "Custom emoji set" if current and current.isdigit() else "Default emoji"
        kb.inline_keyboard.append([InlineKeyboardButton(
            text=f"{category_display_name(cat)} · {status}",
            callback_data=f"set_cat_emoji_{cat}",
            icon_custom_emoji_id=get_emoji_icon("info_icon"),
            style="primary")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text("🎨 <b>Set Category Emojis</b>\nChoose a category to set its custom emoji ID:", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("set_cat_emoji_"))
async def admin_set_category_emoji_prompt(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    category = call.data.split("set_cat_emoji_", 1)[1]
    await state.update_data(cat_emoji_category=category)
    await call.message.edit_text(f"🎨 Enter the emoji ID for <b>{category}</b>:\n(Leave empty to reset to default)", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_category_emoji)

@dp.message(AdminStates.wait_for_category_emoji)
async def save_category_emoji(m: Message, state: FSMContext):
    data = await state.get_data()
    category = data['cat_emoji_category']
    emoji_id = m.text.strip()
    if emoji_id == "":
        db_query("DELETE FROM settings WHERE key=?", (f"cat_emoji_{category}",))
        await m.answer(f"✅ Reset emoji for {category} to default.", reply_markup=admin_kb(), parse_mode='HTML')
    else:
        if not emoji_id.isdigit():
            await m.answer("❌ Invalid ID! Must be numeric.", reply_markup=admin_kb(), parse_mode='HTML')
            return
        set_setting(f"cat_emoji_{category}", emoji_id)
        await m.answer(f"✅ Emoji set for {category} successfully!", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_set_panel_emojis")
async def admin_set_panel_emojis(call: CallbackQuery):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    panels = db_query("SELECT DISTINCT panel_name FROM products WHERE panel_name != '' ORDER BY panel_name", fetchall=True)
    if not panels:
        await call.message.edit_text("No panel names found in products.", reply_markup=admin_back_kb(), parse_mode='HTML')
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for p in panels:
        panel = p[0]
        current = get_setting(f"panel_emoji_{panel}", "Not set")
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"{panel} (ID: {current})", callback_data=f"set_panel_emoji_{panel}", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")])
    await call.message.edit_text("🖼 <b>Set Panel Emojis</b>\nChoose a panel name to set its custom emoji ID:", reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data.startswith("set_panel_emoji_"))
async def admin_set_panel_emoji_prompt(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    panel_name = call.data.split("set_panel_emoji_", 1)[1]
    await state.update_data(panel_emoji_name=panel_name)
    await call.message.edit_text(f"🎨 Enter the emoji ID for panel <b>{panel_name}</b>:\n(Leave empty to reset to default)", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_panel_emoji_id)

@dp.message(AdminStates.wait_for_panel_emoji_id)
async def save_panel_emoji(m: Message, state: FSMContext):
    data = await state.get_data()
    panel_name = data['panel_emoji_name']
    emoji_id = m.text.strip()
    if emoji_id == "":
        db_query("DELETE FROM settings WHERE key=?", (f"panel_emoji_{panel_name}",))
        await m.answer(f"✅ Reset emoji for panel '{panel_name}'.", reply_markup=admin_kb(), parse_mode='HTML')
    else:
        if not emoji_id.isdigit():
            await m.answer("❌ Invalid ID! Must be numeric.", reply_markup=admin_kb(), parse_mode='HTML')
            return
        set_setting(f"panel_emoji_{panel_name}", emoji_id)
        await m.answer(f"✅ Emoji set for panel '{panel_name}'!", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

def fampay_admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌐 Set Base URL", callback_data="admin_fampay_set_url", style="primary")],
        [InlineKeyboardButton(text="🔑 Set API Key", callback_data="admin_fampay_set_key", style="primary")],
        [InlineKeyboardButton(text="📧 Set Gmail", callback_data="admin_fampay_set_gmail", style="primary"),
         InlineKeyboardButton(text="💠 Set UPI", callback_data="admin_fampay_set_upi", style="primary")],
        [InlineKeyboardButton(text="🧪 Test Gateway", callback_data="admin_test_fampay", style="success")],
        [InlineKeyboardButton(text="🔙 Back to Admin", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])

@dp.callback_query(F.data == "admin_setup_fampay")
async def setup_fampay_start(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    await state.clear()
    current_key = get_setting("fampay_api_key", "")
    current_url = get_fampay_base_url()
    current_gmail = get_setting("fampay_gmail", "")
    current_upi = get_setting("fampay_upi", "")
    masked = (current_key[:4] + "••••••" + current_key[-4:]) if len(current_key) > 8 else ("Not configured" if not current_key else "Configured")
    text=(
        "💳 <b>FAMPAY / FAMGATEWAY SETTINGS</b>\n\n"
        f"🌐 Base URL: <code>{current_url}</code>\n"
        f"🔑 API Key: <code>{masked}</code>\n"
        f"📧 Gmail: <code>{current_gmail or 'Not set'}</code>\n"
        f"💠 UPI: <code>{current_upi or 'Not set'}</code>\n\n"
        "Owner can change every gateway field below. Gmail/App Password is configured on the gateway dashboard; the bot does not store the App Password.")
    await call.message.edit_text(text, reply_markup=fampay_admin_kb(), parse_mode='HTML')

async def _ask_fampay_field(call: CallbackQuery, state: FSMContext, field: str, prompt: str):
    if not is_admin(call.from_user.id): return
    await state.clear()
    await state.update_data(fampay_field=field)
    await state.set_state(AdminStates.wait_for_fampay_api)
    await call.message.edit_text(prompt, reply_markup=admin_back_kb(), parse_mode='HTML')

@dp.callback_query(F.data == "admin_fampay_set_url")
async def admin_fampay_set_url(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    await _ask_fampay_field(call, state, "url", "🌐 Send the FamGateway Base URL.\nExample: <code>https://famgateway.in</code>")

@dp.callback_query(F.data == "admin_fampay_set_key")
async def admin_fampay_set_key(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    await _ask_fampay_field(call, state, "key", "🔑 Send the FamGateway API Key.")

@dp.callback_query(F.data == "admin_fampay_set_gmail")
async def admin_fampay_set_gmail(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    await _ask_fampay_field(call, state, "gmail", "📧 Send the gateway-linked Gmail address.")

@dp.callback_query(F.data == "admin_fampay_set_upi")
async def admin_fampay_set_upi(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    await _ask_fampay_field(call, state, "upi", "💠 Send the FamPay UPI ID, e.g. <code>yourname@fam</code>.")

@dp.message(AdminStates.wait_for_fampay_api)
async def fampay_api(m: Message, state: FSMContext):
    value=(m.text or '').strip()
    if value.lower() == '/cancel':
        await state.clear(); return await m.answer("Setup cancelled.", reply_markup=admin_kb(), parse_mode='HTML')
    data=await state.get_data(); field=data.get("fampay_field")
    if field == "url":
        candidate=value.rstrip("/")
        parsed=urlparse(candidate)
        if parsed.scheme not in ("http","https") or not parsed.netloc:
            return await m.answer("❌ Invalid URL. Example: <code>https://famgateway.in</code>", parse_mode='HTML')
        set_setting("fampay_base_url", candidate)
    elif field == "key":
        if len(value) < 8: return await m.answer("❌ API key looks too short. Please send the valid key.")
        set_setting("fampay_api_key", value)
    elif field == "gmail":
        if "@" not in value or "." not in value.split("@")[-1]:
            return await m.answer("❌ Invalid Gmail address.")
        set_setting("fampay_gmail", value)
    elif field == "upi":
        if "@" not in value or len(value) < 5: return await m.answer("❌ Invalid UPI ID. Example: <code>yourname@fam</code>", parse_mode='HTML')
        set_setting("fampay_upi", value)
    else:
        await state.clear(); return await m.answer("❌ Unknown gateway field. Open Gateway Setup again.", reply_markup=admin_kb())
    await state.clear()
    await m.answer("✅ Gateway setting saved successfully.", reply_markup=fampay_admin_kb(), parse_mode='HTML')

@dp.callback_query(F.data == "admin_test_fampay")
async def admin_test_fampay(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    api_key=get_setting("fampay_api_key","").strip()
    base=get_fampay_base_url()
    if not api_key:
        return await call.answer("❌ API key is not configured.", show_alert=True)
    await call.answer("🧪 Testing gateway connection...", show_alert=False)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(get_fampay_verify_url(), params={"api_key":api_key,"order_id":"TEST-INVALID-ORDER"}, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                try: data=await resp.json(content_type=None)
                except Exception: data={}
                if resp.status == 401:
                    msg="❌ API key rejected (401). Check the active FamGateway API key."
                elif resp.status == 403:
                    msg="❌ Gateway access forbidden/suspended (403)."
                elif resp.status in (200,400,404,408):
                    msg=f"✅ Gateway reachable. HTTP {resp.status}. Base URL is working; test order was intentionally invalid."
                else:
                    msg=f"⚠️ Gateway responded with HTTP {resp.status}: {data.get('message','Unknown response')}"
    except Exception as e:
        logger.error("FamPay admin test failed: %s", e)
        msg=f"❌ Cannot connect to gateway: {e}"
    text=(f"🧪 <b>GATEWAY TEST</b>\n\n🌐 <code>{base}</code>\n\n{msg}")
    await call.message.edit_text(text, reply_markup=fampay_admin_kb(), parse_mode='HTML')

# ==============================================================================
# 22. BOOTSTRAPPING & MAIN
# ==============================================================================
async def main() -> None:
    if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN is not configured")
    if not ADMIN_ID: raise RuntimeError("ADMIN_ID is not configured")
    _license_db_init()
    init_db()
    logger.info("Initializing DB structure...")
    migrate_categories()
    asyncio.create_task(auto_verify_task())
    logger.info("FamPay Auto-Verifier Daemon Running in Background.")
    logger.info("🚀 CORE SYSTEM IS FULLY OPERATIONAL...")
    try:
        # This child uses polling, so remove any stale Telegram webhook first.
        try:
            await bot.delete_webhook(drop_pending_updates=False)
        except Exception as webhook_err:
            logger.warning("Webhook cleanup before polling failed: %s", webhook_err)
        await dp.start_polling(bot)
    except Exception as err:
        logger.error(f"Critical System Failure in Polling: {err}")
    finally:
        global _API_HTTP_SESSION
        if _API_HTTP_SESSION is not None and not _API_HTTP_SESSION.closed:
            await _API_HTTP_SESSION.close()
        await bot.session.close()

# ==============================================================================
# EXTERNAL KEY GENERATION API
# ==============================================================================
def normalize_api_duration(duration: str) -> str:
    """Normalize the package name into the duration format expected by the API."""
    value = str(duration or "").strip()
    if not value:
        return value

    # Normalize whitespace
    value = re.sub(r"\s+", " ", value)
    
    # Define exact mapping for your API format
    # API expects: "X Hours" or "X DaYs" (with capital D and Y)
    duration_map = {
        # Hours - API expects "X Hours"
        "1 hour": "1 Hours",
        "1 hours": "1 Hours",
        "1hr": "1 Hours",
        "1h": "1 Hours",
        "2 hour": "2 Hours",
        "2 hours": "2 Hours",
        "2hr": "2 Hours",
        "3 hour": "3 Hours",
        "3 hours": "3 Hours",
        "3hr": "3 Hours",
        "6 hour": "6 Hours",
        "6 hours": "6 Hours",
        "6hr": "6 Hours",
        "12 hour": "12 Hours",
        "12 hours": "12 Hours",
        "12hr": "12 Hours",
        
        # Days - API expects "X DaYs" (capital D and Y)
        "1 day": "1 DaYs",
        "1 days": "1 DaYs",
        "1d": "1 DaYs",
        "2 day": "2 DaYs",
        "2 days": "2 DaYs",
        "2d": "2 DaYs",
        "3 day": "3 DaYs",
        "3 days": "3 DaYs",
        "3d": "3 DaYs",
        "5 day": "5 DaYs",
        "5 days": "5 DaYs",
        "5d": "5 DaYs",
        "7 day": "7 DaYs",
        "7 days": "7 DaYs",
        "7d": "7 DaYs",
        "30 day": "30 DaYs",
        "30 days": "30 DaYs",
        "30d": "30 DaYs",
    }
    
    # Check exact matches first (case insensitive)
    lower_val = value.lower()
    for pattern, result in duration_map.items():
        if lower_val == pattern.lower():
            return result
    
    # Check if it's already in correct format
    if value in ["1 Hours", "2 Hours", "3 Hours", "6 Hours", "12 Hours", 
                 "1 DaYs", "2 DaYs", "3 DaYs", "5 DaYs", "7 DaYs"]:
        return value
    
    # Try to extract number and determine unit
    match = re.match(r"(\d+)\s*(hour|hours|hr|hrs|h|day|days|d|month|months|mo|year|years|yr|y)", value, re.IGNORECASE)
    if match:
        number = match.group(1)
        unit = match.group(2).lower()
        if unit in ["hour", "hours", "hr", "hrs", "h"]:
            return f"{number} Hours"
        if unit in ["day", "days", "d"]:
            return f"{number} DaYs"
        if unit in ["month", "months", "mo"]:
            return f"{number} Months"
        if unit in ["year", "years", "yr", "y"]:
            return f"{number} Years"
    
    # If it's just a number, treat as hours
    if value.isdigit():
        return f"{value} Hours"
    
    # Return as-is if nothing matches (fallback)
    return value

async def _get_api_provider(provider_id: int = None) -> Optional[dict]:
    if provider_id:
        row = db_query("""SELECT id,name,url,api_key,master_key,http_method,api_key_field,master_key_header,
                         action_field,product_id_field,duration_field,android_id_field,response_key_field,
                         success_field,success_value,active,requires_android_id
                         FROM api_providers WHERE id=?""", (provider_id,), fetchone=True)
    else:
        row = None
    if not row:
        return None
    keys = ['id','name','url','api_key','master_key','http_method','api_key_field','master_key_header',
            'action_field','product_id_field','duration_field','android_id_field','response_key_field',
            'success_field','success_value','active','requires_android_id']
    return dict(zip(keys, row))

def _nested_get(obj: Any, path: str, default=None):
    if not path:
        return default
    cur = obj
    for part in str(path).split('.'):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur

async def fetch_external_key(product_id: str, duration: str, android_id: str = "", provider_id: int = None) -> dict:
    """Purchase from the selected API provider. Falls back to legacy global API settings for old products."""
    provider = await _get_api_provider(provider_id)
    if provider and not provider.get('active'):
        return {"status": "error", "msg": f"API provider '{provider['name']}' is disabled"}

    if provider:
        url = str(provider.get('url') or '').strip()
        api_key = str(provider.get('api_key') or '').strip()
        master_key = str(provider.get('master_key') or '').strip()
        method = str(provider.get('http_method') or 'POST').upper()
        api_key_field = str(provider.get('api_key_field') or 'api_key')
        master_header = str(provider.get('master_key_header') or 'x-master-key')
        action_field = str(provider.get('action_field') or 'action')
        product_field = str(provider.get('product_id_field') or 'product_id')
        duration_field = str(provider.get('duration_field') or 'duration')
        android_field = str(provider.get('android_id_field') or 'android_id')
        response_key_field = str(provider.get('response_key_field') or 'key')
        success_field = str(provider.get('success_field') or 'status')
        success_value = str(provider.get('success_value') or 'success')
        requires_android = bool(provider.get('requires_android_id'))
    else:
        url = get_setting("external_api_url", "https://ffpanelshop.onrender.com/api/reseller_v1.php").strip()
        api_key = get_setting("external_api_key", "").strip()
        master_key = get_setting("external_master_key", "").strip()
        method = "POST"; api_key_field="api_key"; master_header="x-master-key"
        action_field="action"; product_field="product_id"; duration_field="duration"; android_field="android_id"
        response_key_field="key"; success_field="status"; success_value="success"; requires_android=False

    if not url:
        return {"status": "error", "msg": "API URL is not configured"}
    if not api_key:
        return {"status": "error", "msg": "API key is not configured"}
    product_id = str(product_id or '').strip(); duration = str(duration or '').strip(); android_id = str(android_id or '').strip()
    if not product_id: return {"status":"error","msg":"API Product ID is empty"}
    if not duration: return {"status":"error","msg":"Product duration is empty"}
    if requires_android and not android_id:
        return {"status":"error","msg":"Android ID is required for this API/product"}

    data = {api_key_field: api_key, action_field: 'buy', product_field: product_id, duration_field: duration}
    # Device-bound products explicitly require Android ID; V2/non-device products do not receive it.
    if requires_android and android_id:
        data[android_field] = android_id
    headers = {"Accept":"application/json, text/plain, */*"}
    if master_key:
        headers[master_header] = master_key
    # Product-buy requests are intentionally NOT retried automatically: a POST can succeed
    # remotely while its response is lost, and retrying it could issue a second key.
    timeout=aiohttp.ClientTimeout(total=20, connect=6, sock_connect=6, sock_read=12)
    try:
        session = await _get_api_http_session()
        request_kwargs = {"headers": headers, "allow_redirects": True}
        if method == 'GET':
            async with session.get(url, params=data, **request_kwargs) as resp:
                raw=await resp.text()
        else:
            headers.setdefault('Content-Type','application/x-www-form-urlencoded')
            async with session.post(url, data=data, **request_kwargs) as resp:
                raw=await resp.text()
        logger.info("API BUY %s HTTP %s body=%s", provider.get('name') if provider else 'Legacy', resp.status, raw[:1000])
        if resp.status != 200:
            return {"status":"error","msg":f"HTTP {resp.status}: {raw[:500]}"}
        if not raw.strip(): return {"status":"error","msg":"API returned an empty response"}
        try: result=json.loads(raw)
        except json.JSONDecodeError: return {"status":"error","msg":f"API returned invalid JSON: {raw[:300]}"}
        if not isinstance(result,dict): return {"status":"error","msg":"API returned an invalid response object"}
        status_val=_nested_get(result, success_field, None)
        key_val=_nested_get(result, response_key_field, None)
        explicit_error = str(status_val).lower() in {"error", "failed", "failure", "false", "0"} if status_val is not None else False
        success_match = status_val is not None and str(status_val).lower() == success_value.lower()
        boolean_success = result.get("success") is True
        if success_match or boolean_success:
            if key_val is not None: result.setdefault('key', key_val)
            result['status']='success'
        elif not explicit_error and key_val not in (None, "", "KEY_NOT_FOUND"):
            result['key'] = key_val
            result['status'] = 'success'
        else:
            result['status'] = str(status_val or result.get('status') or 'error')
        if not result.get('msg'):
            result['msg'] = result.get('message') or result.get('error') or result.get('msg') or 'API request was rejected'
        return result
    except (aiohttp.ClientConnectorError,aiohttp.ClientConnectionError,aiohttp.ServerTimeoutError,asyncio.TimeoutError) as exc:
        logger.warning("API buy connection failed (no automatic retry to prevent duplicate remote orders): %s", exc)
        return {"status":"error","msg":f"API connection failed: {type(exc).__name__}: {exc}"}
    except aiohttp.ClientError as exc:
        logger.exception("API client error")
        return {"status":"error","msg":f"API client error: {exc}"}
    except Exception as exc:
        logger.exception("API unexpected error")
        return {"status":"error","msg":f"API unexpected error: {exc}"}

@dp.callback_query(F.data == "admin_api_manager")
async def admin_api_manager(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    providers=db_query("SELECT id,name,url,active,requires_android_id FROM api_providers ORDER BY id", fetchall=True) or []
    kb=InlineKeyboardMarkup(inline_keyboard=[])
    for pid,name,url,active,req in providers:
        dot='🟢' if active else '🔴'; hw='📱' if req else '🌐'
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"{dot} {name} {hw}", callback_data=f"api_view_{pid}", style="primary")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="➕ Create New API", callback_data="api_add", style="success")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="🧩 Create New Paste Code API", callback_data="api_paste_code", style="primary")])
    kb.inline_keyboard.append([InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel_back", style="danger")])
    text="🔌 <b>Multi-API Manager</b>\n\nCreate unlimited API providers here. Example: BUNTY BHAIYA, PANELIST, PANEL ORDERER, XYZ API, etc. Each product can be connected to a different API provider.\n\n"
    text += "\n".join([f"• {('🟢' if a else '🔴')} <b>{n}</b> — <code>{u}</code>" for _,n,u,a,_ in providers]) or "No API providers created yet."
    await call.message.edit_text(text,reply_markup=kb,parse_mode='HTML',disable_web_page_preview=True)

def parse_api_code_snippet(raw: str) -> dict:
    """Parse common JSON/curl/Python-like API snippets without executing user code."""
    out={"url":"","api_key":"","master_key":"","http_method":"POST","api_key_field":"api_key","master_key_header":"x-master-key","action_field":"action","product_id_field":"product_id","duration_field":"duration","android_id_field":"android_id","response_key_field":"key","success_field":"status","success_value":"success"}
    text=(raw or '').strip()
    # JSON configuration is the most reliable paste format.
    try:
        obj=json.loads(text)
        if isinstance(obj,dict):
            for k in out:
                if k in obj and obj[k] is not None: out[k]=str(obj[k])
            if not out['url']:
                out['url']=str(obj.get('endpoint') or obj.get('api_url') or '')
            return out
    except Exception: pass
    urlm=re.search(r'https?://[^\s\'"`<>]+',text,re.I)
    if urlm: out['url']=urlm.group(0).rstrip('),;')
    mm=re.search(r'\b(method|http_method)\s*[:=]\s*[\'" ]*(GET|POST|PUT|PATCH|DELETE)',text,re.I)
    if mm: out['http_method']=mm.group(2).upper()
    # Detect header names and values.
    hm=re.search(r'[\'"`]x-master-key[\'"`]\s*[:=]\s*[\'"`]([^\'"`\n]+)',text,re.I)
    if hm: out['master_key']=hm.group(1).strip()
    am=re.search(r'(?:api_key|apikey|api-key)[\'"`]??\s*[:=]\s*[\'"`]([^\'"`\n&]+)',text,re.I)
    if am: out['api_key']=am.group(1).strip()
    if not out['api_key']:
        # common curl -d api_key=VALUE or params dict
        am=re.search(r'(?:api_key|apikey|api-key)\s*[=:]\s*([^\s,&}\'"`]+)',text,re.I)
        if am: out['api_key']=am.group(1).strip()
    for field,key in [('action','action_field'),('product_id','product_id_field'),('duration','duration_field'),('android_id','android_id_field')]:
        fm=re.search(r'[\'"`]'+re.escape(field)+r'[\'"`]\s*[:=]',text,re.I)
        if fm: out[key]=field
    # Infer alternate API-key parameter name when visible in a payload.
    km=re.search(r'[\'"`]([A-Za-z0-9_-]*(?:api[_-]?key|token|key)[A-Za-z0-9_-]*)[\'"`]\s*[:=]',text,re.I)
    if km: out['api_key_field']=km.group(1)
    sm=re.search(r'[\'"`](status|success|result)[\'"`]\s*[:=]',text,re.I)
    if sm: out['success_field']=sm.group(1)
    return out

@dp.callback_query(F.data == "api_paste_code")
async def api_paste_code_start(call: CallbackQuery,state:FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    await call.message.edit_text("📋 <b>Paste API Code</b>\n\nPaste the provider's <b>curl / JSON config / Python request snippet</b> here.\n\n⚠️ Code is only parsed as text; it is <b>never executed</b>.\n\n/cancel to stop.",reply_markup=admin_back_kb(),parse_mode='HTML')
    await state.set_state(AdminStates.api_add_code)

@dp.message(AdminStates.api_add_code)
async def api_paste_code_save(m: Message,state:FSMContext):
    raw=m.text or ''
    if raw.strip().lower()=='/cancel':
        await state.clear(); return await m.answer("❌ Cancelled.",reply_markup=admin_kb())
    if len(raw)>12000: return await m.answer("❌ API snippet is too large. Keep it under 12,000 characters.",reply_markup=admin_back_kb())
    cfg=parse_api_code_snippet(raw)
    if not cfg['url']:
        return await m.answer("❌ URL auto-detect failed. Paste a snippet containing an http:// or https:// endpoint.",reply_markup=admin_back_kb())
    await state.update_data(api_raw_code=raw, api_detected=cfg)
    await m.answer(f"🔎 <b>Detected API</b>\n\n🌐 URL: <code>{cfg['url']}</code>\n🔧 Method: <b>{cfg['http_method']}</b>\n🔑 API field: <code>{cfg['api_key_field']}</code>\n🔐 Master header: <code>{cfg['master_key_header']}</code>\n\nNow enter a provider name (example: <code>Panelist Auto</code>).",reply_markup=admin_back_kb(),parse_mode='HTML')
    await state.set_state(AdminStates.api_add_name)

@dp.callback_query(F.data == "api_add")
async def api_add_start(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    await call.message.edit_text("🔌 <b>New API Provider</b>\n\nEnter a short unique name. Example: <code>FFPanel</code> or <code>XYZ V2</code>",reply_markup=admin_back_kb(),parse_mode='HTML')
    await state.set_state(AdminStates.api_add_name)

@dp.message(AdminStates.api_add_name)
async def api_add_name(m: Message,state: FSMContext):
    name=m.text.strip()
    if not name or len(name)>40: return await m.answer("❌ Enter a valid API name (1–40 characters).")
    if db_query("SELECT id FROM api_providers WHERE lower(name)=lower(?)",(name,),fetchone=True): return await m.answer("❌ This API name already exists.")
    await state.update_data(api_name=name)
    data=await state.get_data()
    if data.get('api_detected'):
        cfg=data['api_detected']; await m.answer("🧪 Auto-detected configuration ready. Enter/edit API URL, or send <code>same</code> to keep detected URL.",reply_markup=admin_back_kb(),parse_mode='HTML'); await state.set_state(AdminStates.api_add_url); return
    await m.answer("🌐 Enter API URL:\nExample: <code>https://example.com/api/reseller.php</code>",reply_markup=admin_back_kb(),parse_mode='HTML')
    await state.set_state(AdminStates.api_add_url)

@dp.message(AdminStates.api_add_url)
async def api_add_url(m: Message,state: FSMContext):
    url=m.text.strip()
    data=await state.get_data()
    if data.get('api_detected') and url.lower()=='same':
        url=data['api_detected']['url']
    parsed=urlparse(url)
    if parsed.scheme not in ('http','https') or not parsed.netloc: return await m.answer("❌ Invalid URL. Use http:// or https://")
    await state.update_data(api_url=url.rstrip('/'))
    data=await state.get_data()
    if data.get('api_detected'):
        cfg=data['api_detected']; await m.answer(f"🔑 Enter API Key, or send <code>same</code> to keep detected value.",reply_markup=admin_back_kb(),parse_mode='HTML')
    else: await m.answer("🔑 Enter API Key:",reply_markup=admin_back_kb())
    await state.set_state(AdminStates.api_add_key)

@dp.message(AdminStates.api_add_key)
async def api_add_key(m: Message,state: FSMContext):
    key=m.text.strip(); data=await state.get_data()
    if data.get('api_detected') and key.lower()=='same': key=data['api_detected'].get('api_key','')
    if not key: return await m.answer("❌ API Key cannot be empty.")
    await state.update_data(api_key=key)
    await m.answer("🔐 Enter Master Key / Secret header value. If not required, send <code>none</code>; with pasted code, <code>same</code> keeps detected value.",reply_markup=admin_back_kb(),parse_mode='HTML')
    await state.set_state(AdminStates.api_add_master)

@dp.message(AdminStates.api_add_master)
async def api_add_master(m: Message,state: FSMContext):
    master_text=m.text.strip(); data=await state.get_data()
    if data.get('api_detected') and master_text.lower()=='same': master=data['api_detected'].get('master_key','')
    else: master='' if master_text.lower()=='none' else master_text
    cfg=data.get('api_detected') or {}
    db_query("""INSERT INTO api_providers(name,url,api_key,master_key,http_method,api_key_field,master_key_header,action_field,product_id_field,duration_field,android_id_field,response_key_field,success_field,success_value,raw_code) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(data['api_name'],data['api_url'],data['api_key'],master,cfg.get('http_method','POST'),cfg.get('api_key_field','api_key'),cfg.get('master_key_header','x-master-key'),cfg.get('action_field','action'),cfg.get('product_id_field','product_id'),cfg.get('duration_field','duration'),cfg.get('android_id_field','android_id'),cfg.get('response_key_field','key'),cfg.get('success_field','status'),cfg.get('success_value','success'),data.get('api_raw_code','')))
    await m.answer(f"✅ <b>API Created</b>\n\n🔌 {data['api_name']}\n🌐 <code>{data['api_url']}</code>\n\nअब Add Product में यही API select कर सकते हो.",reply_markup=admin_kb(),parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data.startswith("api_view_"))
async def api_view(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    pid=int(call.data.split('_')[2]); row=await _get_api_provider(pid)
    if not row: return await call.answer("❌ API not found.",show_alert=True)
    mask=lambda x: (x[:4]+'••••'+x[-4:]) if len(x)>8 else ('Configured' if x else 'Not set')
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ URL",callback_data=f"api_edit_{pid}_url",style="primary"),InlineKeyboardButton(text="🔑 API Key",callback_data=f"api_edit_{pid}_key",style="primary")],
        [InlineKeyboardButton(text="🔐 Master Key",callback_data=f"api_edit_{pid}_master",style="primary"),InlineKeyboardButton(text="📱 Android ID",callback_data=f"api_edit_{pid}_android",style="primary")],
        [InlineKeyboardButton(text="🧪 Test API",callback_data=f"api_test_{pid}",style="success")],
        [InlineKeyboardButton(text="🔴 Disable" if row['active'] else "🟢 Enable",callback_data=f"api_toggle_{pid}",style="primary"),InlineKeyboardButton(text="🗑 Delete",callback_data=f"api_delete_{pid}",style="danger")],
        [InlineKeyboardButton(text="🔙 API List",callback_data="admin_api_manager",style="danger")]
    ])
    text=(f"🔌 <b>{row['name']}</b>\n━━━━━━━━━━━━━━\n🌐 URL: <code>{row['url']}</code>\n🔑 API Key: <code>{mask(row['api_key'])}</code>\n🔐 Master: <code>{mask(row['master_key'])}</code>\n📱 Android ID: <b>{'Required' if row['requires_android_id'] else 'Optional'}</b>\n📡 Status: <b>{'Enabled' if row['active'] else 'Disabled'}</b>")
    await call.message.edit_text(text,reply_markup=kb,parse_mode='HTML',disable_web_page_preview=True)

@dp.callback_query(F.data.startswith("api_toggle_"))
async def api_toggle(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    pid=int(call.data.split('_')[2]); row=await _get_api_provider(pid)
    if not row: return await call.answer("API not found",show_alert=True)
    db_query("UPDATE api_providers SET active=? WHERE id=?",(0 if row['active'] else 1,pid)); await api_view(call)

@dp.callback_query(F.data.startswith("api_delete_"))
async def api_delete(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    pid=int(call.data.split('_')[2])
    db_query("UPDATE products SET api_provider_id=NULL WHERE api_provider_id=?",(pid,))
    db_query("DELETE FROM api_providers WHERE id=?",(pid,))
    await call.answer("✅ API deleted. Products were switched to legacy API/manual mode.",show_alert=True)
    await admin_api_manager(call,None)

@dp.callback_query(F.data.startswith("api_edit_"))
async def api_edit_start(call: CallbackQuery,state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    parts=call.data.split('_'); pid=int(parts[2]); field=parts[3]
    await state.update_data(api_edit_id=pid,api_edit_field=field)
    labels={'url':'API URL','key':'API Key','master':'Master Key','android':'Android ID requirement (yes/no)'}
    await call.message.edit_text(f"✏️ Enter new <b>{labels.get(field,field)}</b>:",reply_markup=admin_back_kb(),parse_mode='HTML')
    await state.set_state(AdminStates.api_edit_value)

@dp.message(AdminStates.api_edit_value)
async def api_edit_save(m: Message,state: FSMContext):
    data=await state.get_data(); pid=data['api_edit_id']; field=data['api_edit_field']; value=m.text.strip()
    cols={'url':'url','key':'api_key','master':'master_key'}
    if field=='android':
        if value.lower() not in ('yes','no','on','off','1','0'): return await m.answer("❌ Send yes or no.")
        db_query("UPDATE api_providers SET requires_android_id=? WHERE id=?",(1 if value.lower() in ('yes','on','1') else 0,pid))
    elif field in cols:
        if field=='url':
            parsed=urlparse(value)
            if parsed.scheme not in ('http','https') or not parsed.netloc: return await m.answer("❌ Invalid URL.")
            value=value.rstrip('/')
        db_query(f"UPDATE api_providers SET {cols[field]}=? WHERE id=?",(value,pid))
    await state.clear(); await m.answer("✅ API updated.",reply_markup=admin_kb(),parse_mode='HTML')

@dp.callback_query(F.data.startswith("api_test_"))
async def api_test(call: CallbackQuery):
    if not is_admin(call.from_user.id): return
    pid=int(call.data.split('_')[2]); row=await _get_api_provider(pid)
    if not row: return await call.answer("API not found",show_alert=True)
    if not row['url']: return await call.answer("❌ URL is empty",show_alert=True)
    headers={"Accept":"application/json, text/plain, */*"}
    if row['master_key']: headers[row['master_key_header'] or 'x-master-key']=row['master_key']
    params={row['api_key_field'] or 'api_key': row['api_key']}
    try:
        timeout=aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Safe connectivity/auth check: deliberately do NOT send action=buy/product_id/duration.
            if str(row['http_method'] or 'POST').upper()=='GET':
                async with session.get(row['url'],params=params,headers=headers,allow_redirects=True) as resp:
                    raw=(await resp.text())[:300]
            else:
                headers.setdefault('Content-Type','application/x-www-form-urlencoded')
                async with session.post(row['url'],data=params,headers=headers,allow_redirects=True) as resp:
                    raw=(await resp.text())[:300]
        if resp.status in (200,400,401,403,404,405,422):
            await call.answer(f"🟢 API reachable • HTTP {resp.status}",show_alert=True)
        else:
            await call.answer(f"🟠 API responded HTTP {resp.status}",show_alert=True)
    except Exception as e:
        await call.answer(f"🔴 Connection failed: {type(e).__name__}",show_alert=True)

@dp.callback_query(F.data == "admin_setup_external_api")
async def admin_setup_external_api(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id):
        return
    url = get_setting("external_api_url", "")
    key = get_setting("external_api_key", "")
    master = get_setting("external_master_key", "")
    mask = lambda x: (x[:4] + "••••" + x[-4:]) if len(x) > 8 else ("Configured" if x else "Not set")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Set API URL", callback_data="admin_set_ext_url", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
        [InlineKeyboardButton(text="Set API Key", callback_data="admin_set_ext_key", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
        [InlineKeyboardButton(text="Set Master Key", callback_data="admin_set_ext_master", icon_custom_emoji_id=get_emoji_icon("info_icon"), style="primary")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel_back", icon_custom_emoji_id=get_emoji_icon("back"), style="danger")]
    ])
    text = ("🔗 <b>External Key API Configuration</b>\n\n"
            f"URL: <code>{url or 'Not set'}</code>\n"
            f"API Key: <code>{mask(key)}</code>\n"
            f"Master Key: <code>{mask(master)}</code>\n\n"
            "Products can be switched to API generation from the Add Product flow.")
    await call.message.edit_text(text, reply_markup=kb, parse_mode='HTML')

@dp.callback_query(F.data == "admin_set_ext_url")
async def set_ext_url(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    await call.message.edit_text("Enter External API URL:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_ext_url)

@dp.message(AdminStates.wait_for_ext_url)
async def save_ext_url(m: Message, state: FSMContext):
    value = m.text.strip()
    if not value.startswith(("http://", "https://")):
        return await m.answer("❌ URL must start with http:// or https://")
    set_setting("external_api_url", value)
    await m.answer("✅ External API URL saved.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_set_ext_key")
async def set_ext_key(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    await call.message.edit_text("Enter External API Key:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_ext_key)

@dp.message(AdminStates.wait_for_ext_key)
async def save_ext_key(m: Message, state: FSMContext):
    set_setting("external_api_key", m.text.strip())
    await m.answer("✅ External API Key saved.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query(F.data == "admin_set_ext_master")
async def set_ext_master(call: CallbackQuery, state: FSMContext):
    await safe_call_answer(call)
    if not is_admin(call.from_user.id): return
    await call.message.edit_text("Enter External API Master Key:", reply_markup=admin_back_kb(), parse_mode='HTML')
    await state.set_state(AdminStates.wait_for_ext_master)

@dp.message(AdminStates.wait_for_ext_master)
async def save_ext_master(m: Message, state: FSMContext):
    set_setting("external_master_key", m.text.strip())
    await m.answer("✅ External API Master Key saved.", reply_markup=admin_kb(), parse_mode='HTML')
    await state.clear()

@dp.callback_query()
async def unhandled_callback_fallback(call: CallbackQuery):
    """Fail safely for an outdated/stale inline button instead of silently doing nothing."""
    await safe_call_answer(call, "⚠️ This button is outdated. Please reopen the menu.", show_alert=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("System shutting down gracefully. Goodbye.")
