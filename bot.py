import logging
import os
import sys
import asyncio
import traceback
from datetime import datetime, timedelta
from functools import wraps
from typing import Dict, List, Optional, Tuple, Any, Callable, TypeVar

# Import Telegram Bot API types and helpers
from telegram import (
    Update,
    ChatMember,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramError,
    constants,
)
from telegram.constants import ParseMode, ChatMemberStatus, ChatType
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    JobQueue,
    Job,
)
from telegram.error import BadRequest, Forbidden
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    BigInteger,
    Text,
    ForeignKey,
    and_,
)
from sqlalchemy.orm import sessionmaker, declarative_base, Mapped, mapped_column, relationship
from sqlalchemy.exc import SQLAlchemyError
from dotenv import load_dotenv

# --- [ 0. Configuration and Environment Setup ] ---

# Load environment variables from .env file for secure configuration.
# This helps keep sensitive data like BOT_TOKEN out of the main codebase.
load_dotenv()

# Retrieve bot token from environment variables.
# If BOT_TOKEN is not set, a critical error is logged, and the bot exits.
# Replace os.getenv("BOT_TOKEN", "") with your actual bot token string if you prefer
# not to use a .env file (e.g., BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN_HERE").
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "8239455701:AAG3Bx6xEn42e3fggTWhcRf66-CDPQCiOZs")
if not BOT_TOKEN:
    logging.critical("BOT_TOKEN is not set. Please set the BOT_TOKEN environment variable or replace the placeholder in the code.")
    sys.exit(1)

# Retrieve bot owner's Telegram User ID from environment variables.
# This ID is used for owner-only commands and error reporting.
# Replace os.getenv("OWNER_USER_ID", "123456789") with your actual user ID.
# Ensure it's an integer.
OWNER_USER_ID: int = int(os.getenv("OWNER_USER_ID", "6508600903")) # Default placeholder ID

# Database connection URL. SQLite is used by default for simplicity and portability.
# For production, consider PostgreSQL or MySQL for better performance and concurrency.
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///bot_data.db")

# Logging configuration: setup to log messages to both a file and standard output (console).
# This helps in debugging and monitoring bot activity.
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot_log.log", encoding="utf-8"), # Log to a file named bot_log.log
        logging.StreamHandler(sys.stdout),                     # Log to console
    ],
)
# Suppress excessive logging from the httpx library (used by python-telegram-bot)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Create a logger instance for this module.
logger = logging.getLogger(__name__)

# --- [ 1. Database Setup with SQLAlchemy Models ] ---
# SQLAlchemy is an Object Relational Mapper (ORM) that simplifies database interactions
# by mapping Python objects to database tables.

Base = declarative_base() # Base class for our declarative models

class Group(Base):
    """
    Represents a Telegram group chat managed by the bot.
    Stores various settings specific to each group.
    """
    __tablename__ = "groups" # Name of the database table

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False,
                                    comment="Telegram chat ID of the group")
    title: Mapped[str] = mapped_column(String(255), nullable=False,
                                       comment="Current title of the group chat")
    welcome_message: Mapped[Optional[str]] = mapped_column(Text, default=None,
                                                          comment="Custom welcome message for new members")
    rules_message: Mapped[Optional[str]] = mapped_column(Text, default=None,
                                                         comment="Custom rules message for the group")
    anti_flood_enabled: Mapped[bool] = mapped_column(Boolean, default=True,
                                                     comment="Boolean flag: Is anti-flood system enabled?")
    anti_flood_limit: Mapped[int] = mapped_column(Integer, default=5,
                                                  comment="Max messages allowed in anti_flood_time interval")
    anti_flood_time: Mapped[int] = mapped_column(Integer, default=10,
                                                 comment="Time interval in seconds for anti-flood limit")
    welcome_enabled: Mapped[bool] = mapped_column(Boolean, default=True,
                                                  comment="Boolean flag: Is welcome message enabled?")
    rules_enabled: Mapped[bool] = mapped_column(Boolean, default=True,
                                                comment="Boolean flag: Are rules message enabled?")
    mute_on_warn_count: Mapped[int] = mapped_column(Integer, default=3,
                                                    comment="Number of warns before a user is automatically muted")
    ban_on_warn_count: Mapped[int] = mapped_column(Integer, default=5,
                                                   comment="Number of warns before a user is automatically banned")
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                                                   comment="Timestamp of the last update to group settings")
    
    # Media lock settings (new features)
    lock_photos: Mapped[bool] = mapped_column(Boolean, default=False, comment="Lock photo messages")
    lock_videos: Mapped[bool] = mapped_column(Boolean, default=False, comment="Lock video messages")
    lock_links: Mapped[bool] = mapped_column(Boolean, default=False, comment="Lock messages with links")
    lock_forwards: Mapped[bool] = mapped_column(Boolean, default=False, comment="Lock forwarded messages")
    lock_stickers: Mapped[bool] = mapped_column(Boolean, default=False, comment="Lock sticker messages")
    lock_gifs: Mapped[bool] = mapped_column(Boolean, default=False, comment="Lock GIF messages")
    lock_voice: Mapped[bool] = mapped_column(Boolean, default=False, comment="Lock voice messages")
    lock_documents: Mapped[bool] = mapped_column(Boolean, default=False, comment="Lock document messages")
    lock_videonotes: Mapped[bool] = mapped_column(Boolean, default=False, comment="Lock video note messages")
    lock_polls: Mapped[bool] = mapped_column(Boolean, default=False, comment="Lock poll messages")
    lock_games: Mapped[bool] = mapped_column(Boolean, default=False, comment="Lock game messages")

    # Anti-spam for new members (new feature)
    restrict_new_members: Mapped[bool] = mapped_column(Boolean, default=False,
                                                       comment="New members can only send text for a duration")
    restrict_duration_minutes: Mapped[int] = mapped_column(Integer, default=5,
                                                          comment="Duration in minutes for new member restriction")

    def __repr__(self):
        return f"<Group(id={self.id}, title='{self.title}')>"

class User(Base):
    """
    Represents a Telegram user known to the bot.
    Stores basic user information.
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False,
                                    comment="Telegram user ID")
    first_name: Mapped[str] = mapped_column(String(255), nullable=False,
                                           comment="User's first name")
    last_name: Mapped[Optional[str]] = mapped_column(String(255), default=None,
                                                     comment="User's last name (optional)")
    username: Mapped[Optional[str]] = mapped_column(String(255), default=None,
                                                   comment="User's username (optional)")
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False,
                                         comment="True if the user is a bot")
    reputation: Mapped[int] = mapped_column(Integer, default=0,
                                            comment="User's reputation score (new feature)")
    last_activity: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow,
                                                    comment="Timestamp of the user's last activity")

    def __repr__(self):
        return f"<User(id={self.id}, username='{self.username or self.first_name}')>"

class GroupUser(Base):
    """
    Associates a user with a specific group and stores group-specific data
    for that user, such as warn counts, mute status, and anti-flood counters.
    """
    __tablename__ = "group_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.id"), nullable=False, index=True,
                                          comment="Foreign key to the Group table")
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False, index=True,
                                         comment="Foreign key to the User table")
    warns: Mapped[int] = mapped_column(Integer, default=0,
                                       comment="Number of warns for the user in this group")
    is_muted: Mapped[bool] = mapped_column(Boolean, default=False,
                                           comment="Boolean flag: Is the user currently muted in this group?")
    mute_until: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None,
                                                           comment="Timestamp until which the user is muted")
    last_message_time: Mapped[Optional[datetime]] = mapped_column(DateTime, default=None,
                                                                  comment="Timestamp of the user's last message for anti-flood")
    message_count_in_interval: Mapped[int] = mapped_column(Integer, default=0,
                                                          comment="Message count for anti-flood in the current interval")
    
    # Relationships for easier access
    group: Mapped["Group"] = relationship("Group", backref="group_users")
    user: Mapped["User"] = relationship("User", backref="group_users")

    def __repr__(self):
        return f"<GroupUser(group_id={self.group_id}, user_id={self.user_id}, warns={self.warns}, is_muted={self.is_muted})>"

class ForbiddenWord(Base):
    """
    Stores words that are forbidden in a specific group.
    Messages containing these words will be deleted.
    """
    __tablename__ = "forbidden_words"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.id"), nullable=False, index=True,
                                          comment="Foreign key to the Group table")
    word: Mapped[str] = mapped_column(String(255), nullable=False,
                                      comment="The forbidden word (stored in lowercase)")
    
    # Ensure a word is unique per group
    __table_args__ = (
        UniqueConstraint('group_id', 'word', name='_group_word_uc'),
    )

    def __repr__(self):
        return f"<ForbiddenWord(group_id={self.group_id}, word='{self.word}')>"

class AdminLog(Base):
    """
    Records moderation actions performed by administrators in groups.
    (New feature for accountability and history tracking).
    """
    __tablename__ = "admin_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.id"), nullable=False, index=True,
                                          comment="ID of the group where the action occurred")
    admin_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False,
                                          comment="ID of the admin who performed the action")
    target_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True,
                                                 comment="ID of the user affected by the action (if any)")
    action: Mapped[str] = mapped_column(String(50), nullable=False,
                                        comment="Type of action (e.g., 'warn', 'mute', 'ban', 'kick', 'add_filter')")
    reason: Mapped[Optional[str]] = mapped_column(Text, default=None,
                                                  comment="Reason for the moderation action")
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow,
                                                comment="Time when the action was performed")

    # Relationships for easier access to admin and target user details
    admin: Mapped["User"] = relationship("User", foreign_keys=[admin_id], backref="performed_actions")
    target_user: Mapped["User"] = relationship("User", foreign_keys=[target_user_id], backref="received_actions")
    group: Mapped["Group"] = relationship("Group", backref="admin_logs")

    def __repr__(self):
        return f"<AdminLog(group_id={self.group_id}, admin_id={self.admin_id}, action='{self.action}')>"

# Initialize the SQLAlchemy engine for connecting to the database.
engine = create_engine(DATABASE_URL)
# Create all defined tables in the database if they do not already exist.
Base.metadata.create_all(engine)

# Create a session factory for creating new database session objects.
Session = sessionmaker(bind=engine)

# --- [ 2. Database Helper Functions ] ---
# These functions abstract common database operations, making the handlers cleaner.

def get_session():
    """
    Provides a new SQLAlchemy session object.
    It's crucial to close the session after use using `session.close()`.
    """
    return Session()

async def get_or_create_group(session, chat_id: int, chat_title: str) -> Group:
    """
    Retrieves an existing Group object by its Telegram chat ID or creates a new one
    if it doesn't exist. Commits the session if a new group is created.
    """
    group = session.query(Group).filter_by(id=chat_id).first()
    if not group:
        group = Group(id=chat_id, title=chat_title)
        session.add(group)
        try:
            session.commit()
            logger.info(f"New group added to DB: {group.title} ({group.id})")
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Error creating new group {chat_id}: {e}")
            raise
    return group

async def get_or_create_user(session, user_data: Dict[str, Any]) -> User:
    """
    Retrieves an existing User object by its Telegram user ID or creates a new one.
    Also updates user details (first_name, last_name, username) and last_activity timestamp.
    Commits the session if a new user is created or an existing one is updated.
    """
    user = session.query(User).filter_by(id=user_data['id']).first()
    if not user:
        user = User(
            id=user_data['id'],
            first_name=user_data.get('first_name'),
            last_name=user_data.get('last_name'),
            username=user_data.get('username'),
            is_bot=user_data.get('is_bot', False)
        )
        session.add(user)
        try:
            session.commit()
            logger.info(f"New user added to DB: {user.username or user.first_name} ({user.id})")
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Error creating new user {user_data['id']}: {e}")
            raise
    else:
        # Update user info in case it changed (e.g., username, name change)
        updated = False
        if user.first_name != user_data.get('first_name'):
            user.first_name = user_data.get('first_name')
            updated = True
        if user.last_name != user_data.get('last_name'):
            user.last_name = user_data.get('last_name')
            updated = True
        if user.username != user_data.get('username'):
            user.username = user_data.get('username')
            updated = True
        if user.is_bot != user_data.get('is_bot', False):
            user.is_bot = user_data.get('is_bot', False)
            updated = True
        
        user.last_activity = datetime.utcnow() # Always update last activity
        
        if updated:
            try:
                session.commit()
                logger.debug(f"User {user.id} information updated.")
            except SQLAlchemyError as e:
                session.rollback()
                logger.error(f"Error updating user {user.id}: {e}")
                raise
    return user

async def get_or_create_group_user(session, group_id: int, user_id: int) -> GroupUser:
    """
    Retrieves an existing GroupUser association or creates a new one.
    Commits the session if a new association is created.
    """
    group_user = session.query(GroupUser).filter_by(group_id=group_id, user_id=user_id).first()
    if not group_user:
        group_user = GroupUser(group_id=group_id, user_id=user_id)
        session.add(group_user)
        try:
            session.commit()
            logger.info(f"New GroupUser association created for group {group_id} and user {user_id}")
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Error creating new GroupUser for {group_id}/{user_id}: {e}")
            raise
    return group_user

async def update_group_settings(session, group_id: int, **kwargs) -> Optional[Group]:
    """
    Updates specific settings for a given group.
    Uses keyword arguments to update multiple fields dynamically.
    Commits the session if updates are made.
    """
    group = session.query(Group).filter_by(id=group_id).first()
    if group:
        for key, value in kwargs.items():
            if hasattr(group, key):
                setattr(group, key, value)
        try:
            session.commit()
            logger.info(f"Group {group_id} settings updated: {kwargs}")
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Error updating group {group_id} settings: {e}")
            raise
    return group

async def add_forbidden_word(session, group_id: int, word: str) -> bool:
    """
    Adds a forbidden word to a group's filter list.
    Returns True if added, False if it already exists.
    Commits the session if a new word is added.
    """
    existing_word = session.query(ForbiddenWord).filter_by(group_id=group_id, word=word.lower()).first()
    if existing_word:
        return False
    new_word = ForbiddenWord(group_id=group_id, word=word.lower())
    session.add(new_word)
    try:
        session.commit()
        logger.info(f"Forbidden word '{word}' added for group {group_id}")
        return True
    except SQLAlchemyError as e:
        session.rollback()
        logger.error(f"Error adding forbidden word '{word}' to group {group_id}: {e}")
        raise

async def remove_forbidden_word(session, group_id: int, word: str) -> bool:
    """
    Removes a forbidden word from a group's filter list.
    Returns True if removed, False if not found.
    Commits the session if a word is removed.
    """
    forbidden_word = session.query(ForbiddenWord).filter_by(group_id=group_id, word=word.lower()).first()
    if forbidden_word:
        session.delete(forbidden_word)
        try:
            session.commit()
            logger.info(f"Forbidden word '{word}' removed for group {group_id}")
            return True
        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Error removing forbidden word '{word}' from group {group_id}: {e}")
            raise
    return False

async def get_forbidden_words(session, group_id: int) -> List[str]:
    """
    Retrieves all forbidden words for a specific group.
    """
    words = session.query(ForbiddenWord).filter_by(group_id=group_id).all()
    return [fw.word for fw in words]

async def log_admin_action(session, group_id: int, admin_id: int, action: str,
                           target_user_id: Optional[int] = None, reason: Optional[str] = None) -> None:
    """
    Logs an administrative action to the AdminLog table.
    Commits the session after logging the action.
    """
    admin_log_entry = AdminLog(
        group_id=group_id,
        admin_id=admin_id,
        target_user_id=target_user_id,
        action=action,
        reason=reason,
        timestamp=datetime.utcnow()
    )
    session.add(admin_log_entry)
    try:
        session.commit()
        logger.info(f"Admin action logged: Group={group_id}, Admin={admin_id}, Action={action}, Target={target_user_id}")
    except SQLAlchemyError as e:
        session.rollback()
        logger.error(f"Error logging admin action: {e}")
        raise

# --- [ 3. Utility Functions and Decorators ] ---
# Helper functions and decorators to simplify handler logic and manage permissions.

T = TypeVar('T') # Type variable for decorator typing

def get_user_mention(user: Any) -> str:
    """
    Generates an HTML-formatted mention string for a Telegram user.
    Handles different user object types (database User model, or dict from Update.effective_user).
    """
    if isinstance(user, User):
        if user.username:
            return f"@{user.username}"
        return f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"
    elif isinstance(user, dict): # For Update.effective_user.to_dict()
        if user.get('username'):
            return f"@{user['username']}"
        return f"<a href='tg://user?id={user['id']}'>{user.get('first_name', 'کاربر')}</a>"
    elif hasattr(user, 'id') and hasattr(user, 'first_name'): # For telegram.User object directly
        if hasattr(user, 'username') and user.username:
            return f"@{user.username}"
        return f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"
    return "کاربر ناشناس" # Fallback

def parse_time_duration(duration_str: str) -> Optional[timedelta]:
    """
    Parses a string like "1h", "30m", "7d" into a timedelta object.
    Supports 'm' (minutes), 'h' (hours), 'd' (days).
    Returns None if the string cannot be parsed.
    """
    duration_str = duration_str.lower().strip()
    if not duration_str:
        return None

    unit_map = {'m': 'minutes', 'h': 'hours', 'd': 'days'}
    
    try:
        value = int(duration_str[:-1])
        unit_char = duration_str[-1]
        
        if unit_char in unit_map:
            return timedelta(**{unit_map[unit_char]: value})
        else:
            # If no unit or invalid unit, try to parse as minutes by default
            return timedelta(minutes=int(duration_str))
    except (ValueError, IndexError):
        return None

async def is_user_admin_or_owner(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Checks if a user is an administrator or the owner of the given chat.
    This also considers the bot's owner (OWNER_USER_ID) as having full rights.
    """
    if user_id == OWNER_USER_ID:
        return True # Bot owner bypasses all group permissions
    try:
        member: ChatMember = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
    except TelegramError as e:
        logger.warning(f"Could not get chat member status for user {user_id} in chat {chat_id}: {e}")
        return False

async def is_bot_admin(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Checks if the bot itself is an administrator in the given chat.
    This is crucial for many moderation actions.
    """
    try:
        bot_member: ChatMember = await context.bot.get_chat_member(chat_id, context.bot.id)
        return bot_member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
    except TelegramError as e:
        logger.error(f"Could not check bot admin status in chat {chat_id}: {e}")
        return False

async def get_bot_admin_permissions(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> Optional[ChatMemberAdministrator]:
    """
    Retrieves the bot's administrative permissions in the given chat.
    Returns ChatMemberAdministrator object if bot is admin, None otherwise.
    """
    try:
        bot_member: ChatMember = await context.bot.get_chat_member(chat_id, context.bot.id)
        if isinstance(bot_member, ChatMemberAdministrator):
            return bot_member
        elif isinstance(bot_member, ChatMemberOwner):
             # Owner has all permissions, simulate as admin with all perms
            return ChatMemberAdministrator(
                user=bot_member.user,
                status=ChatMemberStatus.ADMINISTRATOR,
                can_be_edited=True, # For self, implies can set permissions
                can_manage_chat=True,
                can_change_info=True,
                can_delete_messages=True,
                can_invite_users=True,
                can_restrict_members=True,
                can_pin_messages=True,
                can_promote_members=True,
                can_manage_video_chats=True,
                is_anonymous=False,
                can_manage_topics=True, # New feature for topic management
            )
        return None
    except TelegramError as e:
        logger.error(f"Could not get bot admin permissions in chat {chat_id}: {e}")
        return None

def require_admin_permission(permission: str):
    """
    Decorator to restrict command usage to group administrators with specific permissions.
    E.g., @require_admin_permission("can_restrict_members") for /ban, /mute.
    """
    def decorator(func: Callable[[Update, ContextTypes.DEFAULT_TYPE, Any], Any]) -> Callable[[Update, ContextTypes.DEFAULT_TYPE, Any], Any]:
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            if not update.effective_chat or not update.effective_user:
                return

            chat_id = update.effective_chat.id
            user_id = update.effective_user.id

            if update.effective_chat.type == ChatType.PRIVATE:
                await update.message.reply_text("این دستور فقط در گروه‌ها قابل استفاده است.")
                return

            # Check if bot has necessary permissions
            bot_perms = await get_bot_admin_permissions(chat_id, context)
            if not bot_perms or not getattr(bot_perms, permission, False):
                await update.message.reply_text(
                    f"من برای انجام این کار به دسترسی ادمین <b>'{permission}'</b> نیاز دارم. لطفاً مطمئن شوید که به من این دسترسی را داده‌اید.",
                    parse_mode=ParseMode.HTML
                )
                return

            # Check if user has necessary permissions
            member: ChatMember = await context.bot.get_chat_member(chat_id, user_id)
            if member.status == ChatMemberStatus.OWNER or user_id == OWNER_USER_ID:
                return await func(update, context, *args, **kwargs)
            
            if isinstance(member, ChatMemberAdministrator) and getattr(member, permission, False):
                return await func(update, context, *args, **kwargs)
            else:
                await update.message.reply_text("شما اجازه استفاده از این دستور را ندارید. فقط ادمین‌ها با دسترسی کافی می‌توانند.")
        return wrapper
    return decorator

def require_bot_owner_only(func: Callable[[Update, ContextTypes.DEFAULT_TYPE, Any], Any]) -> Callable[[Update, ContextTypes.DEFAULT_TYPE, Any], Any]:
    """
    Decorator to restrict command usage strictly to the bot's configured owner.
    """
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not update.effective_user:
            return

        user_id = update.effective_user.id
        if user_id == OWNER_USER_ID:
            return await func(update, context, *args, **kwargs)
        else:
            await update.message.reply_text("شما اجازه استفاده از این دستور را ندارید. فقط توسعه‌دهنده ربات می‌تواند.")
    return wrapper

async def extract_target_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE, args: List[str]) -> Optional[int]:
    """
    Extracts the target user ID from a reply to a message, a mention in text, or a numerical ID provided as an argument.
    Sends an informative message to the chat if no valid target is found.
    """
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user.id
    
    if args:
        arg_text = args[0]
        # Try to parse as user ID
        try:
            return int(arg_text)
        except ValueError:
            # Could be a username, but Telegram API doesn't directly resolve usernames to IDs
            # without a message from them. For now, we'll suggest reply or numerical ID.
            if arg_text.startswith('@'):
                await update.message.reply_text(
                    "برای شناسایی کاربر با نام کاربری، باید ابتدا پیامی از او در گروه وجود داشته باشد یا شناسه عددی او را وارد کنید.",
                    parse_mode=ParseMode.HTML
                )
                return None
            else:
                await update.message.reply_text(
                    "لطفاً شناسه عددی کاربر را وارد کنید یا به پیام او ریپلای کنید."
                )
                return None
    
    await update.message.reply_text(
        "لطفاً به پیام کاربر مورد نظر ریپلای کنید یا شناسه عددی او را پس از دستور وارد کنید.",
        parse_mode=ParseMode.HTML
    )
    return None

async def get_user_info_from_telegram(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> Optional[Dict[str, Any]]:
    """
    Fetches user information directly from the Telegram API.
    Returns a dictionary of user details or None if information cannot be retrieved.
    """
    try:
        # A trick to get public user info without being in a common chat: get_chat_member on self
        chat_member = await context.bot.get_chat_member(user_id, user_id) 
        user_info = chat_member.user
        return {
            'id': user_info.id,
            'first_name': user_info.first_name,
            'last_name': user_info.last_name,
            'username': user_info.username,
            'is_bot': user_info.is_bot
        }
    except Exception as e:
        logger.error(f"Could not get info for user {user_id} from Telegram API: {e}")
        return None

# --- [ 4. Command Handlers (Core Bot Functionality) ] ---
# These functions respond to specific commands issued by users.

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /start command. Sends a welcome message based on whether it's a private chat
    or a group chat. Also ensures the user and group are registered in the database.
    """
    if not update.effective_user or not update.effective_chat or not update.message:
        return

    user_data = update.effective_user.to_dict()
    session = get_session()
    try:
        # Ensure the interacting user is in the database
        db_user = await get_or_create_user(session, user_data)

        if update.effective_chat.type == ChatType.PRIVATE:
            message_text = (
                "👋 سلام! من یک ربات قدرتمند برای مدیریت گروه‌های تلگرام هستم.\n"
                "می‌توانید من را به گروه خود اضافه کرده و به من دسترسی ادمین بدهید تا بتوانم آنجا فعالیت کنم.\n"
                "برای دیدن قابلیت‌های کامل من، از دستور /help استفاده کنید."
            )
            await update.message.reply_text(message_text, parse_mode=ParseMode.HTML)
        else:
            # If in a group, register the group and send a group-specific welcome
            db_group = await get_or_create_group(session, update.effective_chat.id, update.effective_chat.title)
            message_text = (
                f"🎉 سلام به گروه <b>{db_group.title}</b>! من آماده خدمت هستم.\n"
                "لطفاً برای استفاده از تمام قابلیت‌های من، دسترسی ادمین کامل به من بدهید.\n"
                "برای دیدن لیست دستورات، /help را ارسال کنید."
            )
            await update.message.reply_text(message_text, parse_mode=ParseMode.HTML)
    except SQLAlchemyError as e:
        logger.error(f"Database error in start_command for chat {update.effective_chat.id}: {e}")
        await update.message.reply_text("❗️ خطایی در ارتباط با دیتابیس رخ داد. لطفاً بعداً امتحان کنید.")
    finally:
        session.close()

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /help command. Sends a comprehensive list of commands and their usage.
    Commands are categorized for better readability.
    """
    if not update.message:
        return
    
    help_text = (
        "<b>📚 راهنمای کامل دستورات ربات (Digi Anti) 📚</b>\n\n"
        "✨ <b>دستورات عمومی:</b>\n"
        "  • /start - شروع کار با ربات و پیام خوش‌آمدگویی.\n"
        "  • /help - نمایش این راهنمای جامع.\n"
        "  • /rules - نمایش قوانین گروه (در صورت تنظیم).\n"
        "  • /id - دریافت شناسه (ID) تلگرام شما یا پیام ریپلای شده.\n"
        "  • /info [ریپلای یا آیدی] - دریافت اطلاعات جزئی کاربر.\n"
        "  • /groupinfo - دریافت اطلاعات جزئی گروه.\n\n"

        "🛡️ <b>دستورات مدیریتی (فقط برای ادمین‌ها):</b>\n"
        "  • /settings - پنل تنظیمات پیشرفته گروه با دکمه‌های اینلاین.\n"
        "  • /setwelcome [متن] - تنظیم پیام خوش‌آمدگویی جدید. از <code>{user}</code> و <code>{group}</code> استفاده کنید.\n"
        "  • /delwelcome - حذف پیام خوش‌آمدگویی.\n"
        "  • /setrules [متن] - تنظیم پیام قوانین گروه. از <code>{group}</code> استفاده کنید.\n"
        "  • /delrules - حذف پیام قوانین گروه.\n"
        "  • /addfilter [کلمه] - اضافه کردن کلمه به لیست کلمات ممنوعه.\n"
        "  • /delfilter [کلمه] - حذف کلمه از لیست کلمات ممنوعه.\n"
        "  • /filters - نمایش لیست کلمات ممنوعه گروه.\n"
        "  • /warn [ریپلای یا آیدی] [دلیل اختیاری] - اخطار دادن به کاربر.\n"
        "  • /unwarn [ریپلای یا آیدی] - حذف یک اخطار از کاربر.\n"
        "  • /warns [ریپلای یا آیدی] - نمایش تعداد اخطارهای کاربر.\n"
        "  • /mute [ریپلای یا آیدی] [زمان_دقیقه/ساعت/روز] - میوت موقت یا دائم کاربر. مثال: `/mute @user 60m`, `/mute 123456789 3h`, `/mute @user 7d`.\n"
        "  • /unmute [ریپلای یا آیدی] - آن‌میوت کردن کاربر.\n"
        "  • /ban [ریپلای یا آیدی] - بن کردن دائم کاربر.\n"
        "  • /tempban [ریپلای یا آیدی] [زمان_دقیقه/ساعت/روز] - بن موقت کاربر. مثال: `/tempban @user 1d`.\n"
        "  • /kick [ریپلای یا آیدی] - کیک کردن کاربر (بن موقت بسیار کوتاه).\n"
        "  • /purge [تعداد] - حذف [تعداد] پیام آخر در گروه. (مثال: `/purge 10`).\n"
        "  • /del - ریپلای به پیام برای حذف آن.\n"
        "  • /pin [ریپلای] - ریپلای به پیام برای سنجاق کردن آن.\n"
        "  • /unpin - برداشتن سنجاق از پیام فعلی گروه.\n"
        "  • /lock [نوع_رسانه] - قفل کردن نوعی از رسانه. انواع: `photo`, `video`, `link`, `forward`, `sticker`, `gif`, `voice`, `document`, `videonote`, `poll`, `game`.\n"
        "  • /unlock [نوع_رسانه] - باز کردن قفل نوعی از رسانه.\n"
        "  • /reputation [ریپلای یا آیدی] [+ / -] - افزایش یا کاهش اعتبار کاربر.\n"
        "  • /checkrep [ریپلای یا آیدی] - بررسی اعتبار کاربر.\n\n"

        "👑 <b>دستورات مالک ربات (فقط برای توسعه‌دهنده):</b>\n"
        "  • /status - نمایش وضعیت داخلی ربات و آمار کلی.\n"
        "  • /broadcast [متن] - ارسال پیام به تمامی گروه‌های تحت پوشش ربات (با احتیاط استفاده شود!).\n"
        "  • /listgroups - نمایش لیست تمامی گروه‌هایی که ربات در آن‌ها فعال است.\n"
        "  • /leavegroup [chat_id] - خروج ربات از یک گروه خاص.\n"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)


@require_admin_permission("can_change_info") # Admin permission required for general settings management
async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /settings command. Sends an interactive settings panel to the group
    with inline keyboard buttons for toggling and configuring various features.
    """
    if not update.message:
        return

    session = get_session()
    try:
        db_group = await get_or_create_group(session, update.effective_chat.id, update.effective_chat.title)
        
        # Main settings keyboard
        keyboard = [
            [
                InlineKeyboardButton(
                    f"👋 خوش‌آمدگویی: {'✅ فعال' if db_group.welcome_enabled else '❌ غیرفعال'}",
                    callback_data="settings_toggle_welcome"
                ),
                InlineKeyboardButton(
                    f"📜 قوانین: {'✅ فعال' if db_group.rules_enabled else '❌ غیرفعال'}",
                    callback_data="settings_toggle_rules"
                )
            ],
            [
                InlineKeyboardButton(
                    f"🚫 ضد فلود: {'✅ فعال' if db_group.anti_flood_enabled else '❌ غیرفعال'}",
                    callback_data="settings_toggle_anti_flood"
                ),
                InlineKeyboardButton(
                    "⚙️ تنظیمات ضد فلود",
                    callback_data="settings_anti_flood_options"
                )
            ],
            [
                InlineKeyboardButton(
                    f"⚠️ اخطار تا میوت ({db_group.mute_on_warn_count})",
                    callback_data="settings_mute_warn_count"
                ),
                InlineKeyboardButton(
                    f"🚨 اخطار تا بن ({db_group.ban_on_warn_count})",
                    callback_data="settings_ban_warn_count"
                )
            ],
            [
                InlineKeyboardButton("🔠 نمایش کلمات ممنوعه", callback_data="settings_show_forbidden_words")
            ],
            [
                InlineKeyboardButton("🔒 قفل رسانه‌ها", callback_data="settings_media_locks")
            ],
            [
                InlineKeyboardButton(f"👶 محدودیت عضو جدید: {'✅ فعال' if db_group.restrict_new_members else '❌ غیرفعال'}",
                                   callback_data="settings_toggle_restrict_new_members"),
                InlineKeyboardButton("⏱️ مدت محدودیت عضو", callback_data="settings_restrict_duration")
            ],
            [
                InlineKeyboardButton("❌ بستن پنل", callback_data="settings_close")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Settings message
        settings_text = (
            f"<b>تنظیمات گروه {db_group.title}:</b>\n\n"
            f"  • 👋 پیام خوش‌آمدگویی: {'✅ فعال' if db_group.welcome_enabled else '❌ غیرفعال'}\n"
            f"  • 📜 پیام قوانین: {'✅ فعال' if db_group.rules_enabled else '❌ غیرفعال'}\n"
            f"  • 🚫 ضد فلود: {'✅ فعال' if db_group.anti_flood_enabled else '❌ غیرفعال'} "
            f"({db_group.anti_flood_limit} پیام در {db_group.anti_flood_time} ثانیه)\n"
            f"  • ⚠️ تعداد اخطار تا میوت: {db_group.mute_on_warn_count}\n"
            f"  • 🚨 تعداد اخطار تا بن: {db_group.ban_on_warn_count}\n"
            f"  • 👶 محدودیت عضو جدید: {'✅ فعال' if db_group.restrict_new_members else '❌ غیرفعال'} ({db_group.restrict_duration_minutes} دقیقه)\n"
            "\n"
            "برای تغییر تنظیمات از دکمه‌های زیر استفاده کنید."
        )
        await update.message.reply_text(settings_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

    except SQLAlchemyError as e:
        logger.error(f"Database error in settings_command for chat {update.effective_chat.id}: {e}")
        await update.message.reply_text("❗️ خطایی در ارتباط با دیتابیس رخ داد. لطفاً بعداً امتحان کنید.")
    finally:
        session.close()


@require_admin_permission("can_change_info")
async def set_welcome_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /setwelcome command. Allows administrators to set a custom welcome message for the group.
    Supports placeholders {user} for user mention and {group} for group title.
    """
    if not update.message or not update.effective_chat:
        return
    
    if not context.args:
        await update.message.reply_text(
            "لطفاً متن پیام خوش‌آمدگویی را پس از دستور وارد کنید.\n"
            "می‌توانید از <code>{user}</code> برای منشن کاربر و از <code>{group}</code> برای نام گروه استفاده کنید.",
            parse_mode=ParseMode.HTML
        )
        return

    welcome_text = " ".join(context.args)
    chat_id = update.effective_chat.id
    chat_title = update.effective_chat.title

    session = get_session()
    try:
        await get_or_create_group(session, chat_id, chat_title)
        await update_group_settings(session, chat_id, welcome_message=welcome_text, welcome_enabled=True)
        await update.message.reply_text("✅ پیام خوش‌آمدگویی با موفقیت تنظیم و فعال شد.")
        await log_admin_action(session, chat_id, update.effective_user.id, "set_welcome_message", reason=welcome_text)
    except SQLAlchemyError as e:
        logger.error(f"Database error in set_welcome_message for chat {chat_id}: {e}")
        await update.message.reply_text("❗️ خطایی در ارتباط با دیتابیس رخ داد. لطفاً بعداً امتحان کنید.")
    finally:
        session.close()

@require_admin_permission("can_change_info")
async def del_welcome_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /delwelcome command. Deletes the custom welcome message for the group
    and disables the welcome message feature.
    """
    if not update.message or not update.effective_chat:
        return

    chat_id = update.effective_chat.id

    session = get_session()
    try:
        db_group = await get_or_create_group(session, chat_id, update.effective_chat.title)
        if db_group.welcome_message:
            await update_group_settings(session, chat_id, welcome_message=None, welcome_enabled=False)
            await update.message.reply_text("❌ پیام خوش‌آمدگویی با موفقیت حذف و غیرفعال شد.")
            await log_admin_action(session, chat_id, update.effective_user.id, "del_welcome_message")
        else:
            await update.message.reply_text("⚠️ پیام خوش‌آمدگویی برای این گروه تنظیم نشده است.")
    except SQLAlchemyError as e:
        logger.error(f"Database error in del_welcome_message for chat {chat_id}: {e}")
        await update.message.reply_text("❗️ خطایی در ارتباط با دیتابیس رخ داد. لطفاً بعداً امتحان کنید.")
    finally:
        session.close()

@require_admin_permission("can_change_info")
async def set_rules_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /setrules command. Allows administrators to set a custom rules message for the group.
    Supports placeholder {group} for group title.
    """
    if not update.message or not update.effective_chat:
        return

    if not context.args:
        await update.message.reply_text(
            "لطفاً متن قوانین گروه را پس از دستور وارد کنید.\n"
            "می‌توانید از <code>{group}</code> برای نام گروه استفاده کنید.",
            parse_mode=ParseMode.HTML
        )
        return

    rules_text = " ".join(context.args)
    chat_id = update.effective_chat.id
    chat_title = update.effective_chat.title

    session = get_session()
    try:
        await get_or_create_group(session, chat_id, chat_title)
        await update_group_settings(session, chat_id, rules_message=rules_text, rules_enabled=True)
        await update.message.reply_text("✅ پیام قوانین با موفقیت تنظیم و فعال شد.")
        await log_admin_action(session, chat_id, update.effective_user.id, "set_rules_message", reason=rules_text)
    except SQLAlchemyError as e:
        logger.error(f"Database error in set_rules_message for chat {chat_id}: {e}")
        await update.message.reply_text("❗️ خطایی در ارتباط با دیتابیس رخ داد. لطفاً بعداً امتحان کنید.")
    finally:
        session.close()

@require_admin_permission("can_change_info")
async def del_rules_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /delrules command. Deletes the custom rules message for the group
    and disables the rules message feature.
    """
    if not update.message or not update.effective_chat:
        return

    chat_id = update.effective_chat.id

    session = get_session()
    try:
        db_group = await get_or_create_group(session, chat_id, update.effective_chat.title)
        if db_group.rules_message:
            await update_group_settings(session, chat_id, rules_message=None, rules_enabled=False)
            await update.message.reply_text("❌ پیام قوانین با موفقیت حذف و غیرفعال شد.")
            await log_admin_action(session, chat_id, update.effective_user.id, "del_rules_message")
        else:
            await update.message.reply_text("⚠️ پیام قوانینی برای این گروه تنظیم نشده است.")
    except SQLAlchemyError as e:
        logger.error(f"Database error in del_rules_message for chat {chat_id}: {e}")
        await update.message.reply_text("❗️ خطایی در ارتباط با دیتابیس رخ داد. لطفاً بعداً امتحان کنید.")
    finally:
        session.close()

async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /rules command. Displays the group rules message if it is set and enabled.
    Can only be used in group chats.
    """
    if not update.message or not update.effective_chat:
        return

    if update.effective_chat.type == ChatType.PRIVATE:
        await update.message.reply_text("این دستور فقط در گروه‌ها قابل استفاده است.")
        return

    session = get_session()
    try:
        db_group = await get_or_create_group(session, update.effective_chat.id, update.effective_chat.title)
        if db_group.rules_enabled and db_group.rules_message:
            rules_text = db_group.rules_message.format(group=db_group.title)
            await update.message.reply_text(f"<b>📜 قوانین گروه {db_group.title}:</b>\n\n{rules_text}", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text("⚠️ پیام قوانینی برای این گروه تنظیم نشده یا غیرفعال است.")
    except SQLAlchemyError as e:
        logger.error(f"Database error in rules_command for chat {update.effective_chat.id}: {e}")
        await update.message.reply_text("❗️ خطایی در ارتباط با دیتابیس رخ داد. لطفاً بعداً امتحان کنید.")
    finally:
        session.close()

@require_admin_permission("can_delete_messages") # Admin permission to delete messages
async def add_filter_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /addfilter command. Adds a word to the group's forbidden words list.
    Messages containing these words will be deleted.
    """
    if not update.message or not update.effective_chat:
        return

    if not context.args:
        await update.message.reply_text("لطفاً کلمه ممنوعه را پس از دستور وارد کنید.")
        return

    word = context.args[0].lower()
    chat_id = update.effective_chat.id

    session = get_session()
    try:
        if await add_forbidden_word(session, chat_id, word):
            await update.message.reply_text(f"✅ کلمه <code>{word}</code> با موفقیت به لیست کلمات ممنوعه اضافه شد.", parse_mode=ParseMode.HTML)
            await log_admin_action(session, chat_id, update.effective_user.id, "add_filter_word", reason=word)
        else:
            await update.message.reply_text(f"⚠️ کلمه <code>{word}</code> از قبل در لیست کلمات ممنوعه وجود داشت.", parse_mode=ParseMode.HTML)
    except SQLAlchemyError as e:
        logger.error(f"Database error in add_filter_command for chat {chat_id}: {e}")
        await update.message.reply_text("❗️ خطایی در ارتباط با دیتابیس رخ داد. لطفاً بعداً امتحان کنید.")
    finally:
        session.close()

@require_admin_permission("can_delete_messages")
async def del_filter_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /delfilter command. Removes a word from the group's forbidden words list.
    """
    if not update.message or not update.effective_chat:
        return

    if not context.args:
        await update.message.reply_text("لطفاً کلمه ممنوعه مورد نظر برای حذف را پس از دستور وارد کنید.")
        return

    word = context.args[0].lower()
    chat_id = update.effective_chat.id

    session = get_session()
    try:
        if await remove_forbidden_word(session, chat_id, word):
            await update.message.reply_text(f"✅ کلمه <code>{word}</code> با موفقیت از لیست کلمات ممنوعه حذف شد.", parse_mode=ParseMode.HTML)
            await log_admin_action(session, chat_id, update.effective_user.id, "del_filter_word", reason=word)
        else:
            await update.message.reply_text(f"⚠️ کلمه <code>{word}</code> در لیست کلمات ممنوعه یافت نشد.", parse_mode=ParseMode.HTML)
    except SQLAlchemyError as e:
        logger.error(f"Database error in del_filter_command for chat {chat_id}: {e}")
        await update.message.reply_text("❗️ خطایی در ارتباط با دیتابیس رخ داد. لطفاً بعداً امتحان کنید.")
    finally:
        session.close()

@require_admin_permission("can_delete_messages")
async def filters_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /filters command. Displays the current list of forbidden words for the group.
    """
    if not update.message or not update.effective_chat:
        return

    chat_id = update.effective_chat.id

    session = get_session()
    try:
        forbidden_words = await get_forbidden_words(session, chat_id)
        if forbidden_words:
            words_list = "\n".join([f"- <code>{word}</code>" for word in forbidden_words])
            await update.message.reply_text(
                f"<b>🔠 لیست کلمات ممنوعه در این گروه:</b>\n{words_list}",
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text("ℹ️ لیست کلمات ممنوعه برای این گروه خالی است.")
    except SQLAlchemyError as e:
        logger.error(f"Database error in filters_command for chat {chat_id}: {e}")
        await update.message.reply_text("❗️ خطایی در ارتباط با دیتابیس رخ داد. لطفاً بعداً امتحان کنید.")
    finally:
        session.close()

@require_admin_permission("can_restrict_members")
async def warn_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /warn command. Increases a user's warn count. If the warn count reaches
    the configured thresholds, the user is automatically muted or banned.
    """
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    admin_mention = get_user_mention(update.effective_user)

    target_user_id = await extract_target_user_id(update, context, context.args)
    if not target_user_id:
        return

    if target_user_id == admin_id:
        await update.message.reply_text("شما نمی‌توانید به خودتان اخطار بدهید! 🤦‍♂️")
        return
    if target_user_id == context.bot.id:
        await update.message.reply_text("شما نمی‌توانید به من اخطار بدهید! 🤖")
        return
    if await is_user_admin_or_owner(chat_id, target_user_id, context):
        await update.message.reply_text("نمی‌توان به ادمین‌ها یا مالک گروه اخطار داد.")
        return

    session = get_session()
    try:
        # Fetch target user info from Telegram and ensure they are in DB
        target_user_info = await get_user_info_from_telegram(context, target_user_id)
        if not target_user_info:
            await update.message.reply_text("🚫 اطلاعات کاربر مورد نظر یافت نشد. شاید از گروه خارج شده باشد.")
            return
        db_target_user = await get_or_create_user(session, target_user_info)
        target_mention = get_user_mention(db_target_user)
        
        # Get or create group-user association
        group_user = await get_or_create_group_user(session, chat_id, target_user_id)
        group_user.warns += 1
        
        # Fetch group settings for warn thresholds
        db_group = session.query(Group).filter_by(id=chat_id).first()
        mute_on_warn = db_group.mute_on_warn_count if db_group else 3
        ban_on_warn = db_group.ban_on_warn_count if db_group else 5

        reason = " ".join(context.args[1:]) if len(context.args) > 1 else "بدون دلیل"
        session.commit() # Commit warn count increase

        response_message = (
            f"⚠️ {target_mention} یک اخطار دریافت کرد.\n"
            f"تعداد اخطارهای فعلی: <b>{group_user.warns}</b>\n"
            f"دلیل: <i>{reason}</i>\n"
            f"توسط: {admin_mention}"
        )
        await log_admin_action(session, chat_id, admin_id, "warn", target_user_id, reason)

        # Check for automatic moderation actions
        if group_user.warns >= ban_on_warn:
            try:
                await context.bot.ban_chat_member(chat_id, target_user_id)
                session.delete(group_user) # Remove user's group data after ban
                session.commit()
                response_message += (
                    f"\n\n🚨 کاربر {target_mention} به دلیل رسیدن به <b>{ban_on_warn}</b> اخطار، از گروه <b>بن شد!</b>"
                )
                await log_admin_action(session, chat_id, admin_id, "auto_ban_on_warn", target_user_id, f"Reached {ban_on_warn} warns")
            except TelegramError as e:
                logger.error(f"Failed to auto-ban user {target_user_id} in chat {chat_id}: {e}")
                response_message += (
                    f"\n\n❌ ربات نتوانست کاربر را بن کند. (خطا: {e})"
                )
        elif group_user.warns >= mute_on_warn:
            try:
                mute_duration = timedelta(minutes=60) # Default mute for 60 minutes
                until_date = datetime.now() + mute_duration
                await context.bot.restrict_chat_member(
                    chat_id,
                    target_user_id,
                    permissions=constants.ChatPermissions(can_send_messages=False),
                    until_date=until_date
                )
                group_user.is_muted = True
                group_user.mute_until = until_date
                session.commit()
                response_message += (
                    f"\n\n🔇 کاربر {target_mention} به دلیل رسیدن به <b>{mute_on_warn}</b> اخطار، به مدت 60 دقیقه میوت شد."
                )
                await log_admin_action(session, chat_id, admin_id, "auto_mute_on_warn", target_user_id, f"Reached {mute_on_warn} warns")
            except TelegramError as e:
                logger.error(f"Failed to auto-mute user {target_user_id} in chat {chat_id}: {e}")
                response_message += (
                    f"\n\n❌ ربات نتوانست کاربر را میوت کند. (خطا: {e})"
                )
        
        await update.message.reply_text(response_message, parse_mode=ParseMode.HTML)

    except SQLAlchemyError as e:
        logger.error(f"Database error in warn_command for chat {chat_id}: {e}")
        await update.message.reply_text("❗️ خطایی در ارتباط با دیتابیس رخ داد. لطفاً بعداً امتحان کنید.")
    finally:
        session.close()

@require_admin_permission("can_restrict_members")
async def unwarn_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /unwarn command. Decreases a user's warn count by one.
    """
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id

    target_user_id = await extract_target_user_id(update, context, context.args)
    if not target_user_id:
        return

    session = get_session()
    try:
        target_user_info = await get_user_info_from_telegram(context, target_user_id)
        if not target_user_info:
            await update.message.reply_text("🚫 اطلاعات کاربر مورد نظر یافت نشد. شاید از گروه خارج شده باشد.")
            return
        db_target_user = await get_or_create_user(session, target_user_info)
        target_mention = get_user_mention(db_target_user)
        
        group_user = session.query(GroupUser).filter_by(group_id=chat_id, user_id=target_user_id).first()
        if group_user and group_user.warns > 0:
            group_user.warns -= 1
            session.commit()
            await update.message.reply_text(
                f"✅ یک اخطار از {target_mention} حذف شد. اخطارهای فعلی: <b>{group_user.warns}</b>",
                parse_mode=ParseMode.HTML
            )
            await log_admin_action(session, chat_id, admin_id, "unwarn", target_user_id)
        elif group_user:
            await update.message.reply_text(f"⚠️ {target_mention} هیچ اخطاری ندارد.", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(f"⚠️ {target_mention} در دیتابیس گروه یافت نشد.", parse_mode=ParseMode.HTML)

    except SQLAlchemyError as e:
        logger.error(f"Database error in unwarn_command for chat {chat_id}: {e}")
        await update.message.reply_text("❗️ خطایی در ارتباط با دیتابیس رخ داد. لطفاً بعداً امتحان کنید.")
    finally:
        session.close()

@require_admin_permission("can_restrict_members") # Admins can check warns for restriction purposes
async def warns_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /warns command. Displays a user's current warn count in the group.
    """
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    target_user_id = await extract_target_user_id(update, context, context.args)
    if not target_user_id:
        return

    session = get_session()
    try:
        target_user_info = await get_user_info_from_telegram(context, target_user_id)
        if not target_user_info:
            await update.message.reply_text("🚫 اطلاعات کاربر مورد نظر یافت نشد. شاید از گروه خارج شده باشد.")
            return
        db_target_user = await get_or_create_user(session, target_user_info)
        target_mention = get_user_mention(db_target_user)
        
        group_user = session.query(GroupUser).filter_by(group_id=chat_id, user_id=target_user_id).first()
        if group_user:
            await update.message.reply_text(
                f"ℹ️ کاربر {target_mention} در حال حاضر <b>{group_user.warns}</b> اخطار دارد.",
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                f"✅ کاربر {target_mention} هیچ اخطاری در این گروه ندارد.",
                parse_mode=ParseMode.HTML
            )
    except SQLAlchemyError as e:
        logger.error(f"Database error in warns_command for chat {chat_id}: {e}")
        await update.message.reply_text("❗️ خطایی در ارتباط با دیتابیس رخ داد. لطفاً بعداً امتحان کنید.")
    finally:
        session.close()

@require_admin_permission("can_restrict_members")
async def mute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /mute command. Mutes a user for a specified duration or indefinitely.
    Syntax: /mute [reply or ID] [duration (e.g., 30m, 1h, 7d)]
    """
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    admin_mention = get_user_mention(update.effective_user)

    target_user_id = await extract_target_user_id(update, context, context.args)
    if not target_user_id:
        return

    if target_user_id == admin_id:
        await update.message.reply_text("شما نمی‌توانید خودتان را میوت کنید! 🤫")
        return
    if target_user_id == context.bot.id:
        await update.message.reply_text("شما نمی‌توانید من را میوت کنید! 🔇")
        return
    if await is_user_admin_or_owner(chat_id, target_user_id, context):
        await update.message.reply_text("نمی‌توان ادمین‌ها یا مالک گروه را میوت کرد.")
        return

    try:
        duration_str: Optional[str] = None
        if len(context.args) > 1:
            duration_str = context.args[1]
        
        until_date: Optional[datetime] = None
        duration_text = "<b>برای همیشه</b>"
        reason = " ".join(context.args[2:]) if len(context.args) > 2 else "بدون دلیل"
        
        if duration_str:
            parsed_duration = parse_time_duration(duration_str)
            if parsed_duration:
                until_date = datetime.now() + parsed_duration
                duration_text = f"به مدت <b>{duration_str}</b>"
            else:
                reason = " ".join(context.args[1:]) # If duration is invalid, assume it's part of the reason
                await update.message.reply_text(
                    "⚠️ فرمت زمان نامعتبر است. مثال‌ها: `30m` (30 دقیقه), `1h` (1 ساعت), `7d` (7 روز).\n"
                    "کاربر به صورت دائم میوت می‌شود اگر زمان معتبری وارد نشود."
                )

        await context.bot.restrict_chat_member(
            chat_id,
            target_user_id,
            permissions=constants.ChatPermissions(can_send_messages=False),
            until_date=until_date
        )

        session = get_session()
        try:
            target_user_info = await get_user_info_from_telegram(context, target_user_id)
            db_target_user = await get_or_create_user(session, target_user_info)
            target_mention = get_user_mention(db_target_user)

            group_user = await get_or_create_group_user(session, chat_id, target_user_id)
            group_user.is_muted = True
            group_user.mute_until = until_date
            session.commit()

            await update.message.reply_text(
                f"🔇 {target_mention} {duration_text} میوت شد.\n"
                f"دلیل: <i>{reason}</i>\n"
                f"توسط: {admin_mention}",
                parse_mode=ParseMode.HTML
            )
            await log_admin_action(session, chat_id, admin_id, "mute", target_user_id, reason)

        except SQLAlchemyError as e:
            logger.error(f"Database error during mute_command update for chat {chat_id}: {e}")
            await update.message.reply_text("❗️ کاربر میوت شد، اما خطایی در ثبت دیتابیس رخ داد.")
        finally:
            session.close()

    except BadRequest as e:
        if "Can't remove chat owner" in str(e) or "Chat_admin_required" in str(e):
            await update.message.reply_text("🚫 من اجازه میوت کردن این کاربر را ندارم (شاید ادمین است یا خودم ادمین نیستم).")
        else:
            logger.error(f"Error muting user {target_user_id} in chat {chat_id}: {e}")
            await update.message.reply_text(f"❗️ خطایی در میوت کردن کاربر رخ داد: {e}")
    except TelegramError as e:
        logger.error(f"Telegram error in mute_command for chat {chat_id}: {e}")
        await update.message.reply_text(f"❗️ خطایی در تلگرام رخ داد: {e}")

@require_admin_permission("can_restrict_members")
async def unmute_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /unmute command. Unmutes a previously muted user, restoring their full permissions.
    """
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    admin_mention = get_user_mention(update.effective_user)

    target_user_id = await extract_target_user_id(update, context, context.args)
    if not target_user_id:
        return

    try:
        # Restore full permissions to send messages and other media
        # It's important to set all permissions to True, not just can_send_messages
        await context.bot.restrict_chat_member(
            chat_id,
            target_user_id,
            permissions=constants.ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_polls=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
                can_change_info=False, # These are admin-only, leave as false
                can_invite_users=True,
                can_pin_messages=False, # These are admin-only, leave as false
                can_manage_topics=False # These are admin-only, leave as false
            )
        )

        session = get_session()
        try:
            target_user_info = await get_user_info_from_telegram(context, target_user_id)
            db_target_user = await get_or_create_user(session, target_user_info)
            target_mention = get_user_mention(db_target_user)

            group_user = session.query(GroupUser).filter_by(group_id=chat_id, user_id=target_user_id).first()
            if group_user:
                group_user.is_muted = False
                group_user.mute_until = None
                session.commit()

            await update.message.reply_text(
                f"🔊 {target_mention} با موفقیت از حالت میوت خارج شد.\n"
                f"توسط: {admin_mention}",
                parse_mode=ParseMode.HTML
            )
            await log_admin_action(session, chat_id, admin_id, "unmute", target_user_id)

        except SQLAlchemyError as e:
            logger.error(f"Database error during unmute_command update for chat {chat_id}: {e}")
            await update.message.reply_text("❗️ کاربر آن‌میوت شد، اما خطایی در ثبت دیتابیس رخ داد.")
        finally:
            session.close()

    except BadRequest as e:
        if "Chat_admin_required" in str(e):
            await update.message.reply_text("🚫 من اجازه آن‌میوت کردن این کاربر را ندارم (شاید خودم ادمین نیستم).")
        else:
            logger.error(f"Error unmuting user {target_user_id} in chat {chat_id}: {e}")
            await update.message.reply_text(f"❗️ خطایی در آن‌میوت کردن کاربر رخ داد: {e}")
    except TelegramError as e:
        logger.error(f"Telegram error in unmute_command for chat {chat_id}: {e}")
        await update.message.reply_text(f"❗️ خطایی در تلگرام رخ داد: {e}")

@require_admin_permission("can_restrict_members")
async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /ban command. Permanently bans a user from the group.
    """
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    admin_mention = get_user_mention(update.effective_user)

    target_user_id = await extract_target_user_id(update, context, context.args)
    if not target_user_id:
        return

    if target_user_id == admin_id:
        await update.message.reply_text("شما نمی‌توانید خودتان را بن کنید! 🤦‍♂️")
        return
    if target_user_id == context.bot.id:
        await update.message.reply_text("شما نمی‌توانید من را بن کنید! 🤖")
        return
    if await is_user_admin_or_owner(chat_id, target_user_id, context):
        await update.message.reply_text("نمی‌توان ادمین‌ها یا مالک گروه را بن کرد.")
        return
    
    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "بدون دلیل"

    try:
        await context.bot.ban_chat_member(chat_id, target_user_id)

        session = get_session()
        try:
            target_user_info = await get_user_info_from_telegram(context, target_user_id)
            db_target_user = await get_or_create_user(session, target_user_info)
            target_mention = get_user_mention(db_target_user)

            # Optional: remove GroupUser entry on ban for a clean slate if they rejoin (which they shouldn't if permanently banned)
            group_user = session.query(GroupUser).filter_by(group_id=chat_id, user_id=target_user_id).first()
            if group_user:
                session.delete(group_user)
                session.commit()

            await update.message.reply_text(
                f"⛔️ {target_mention} با موفقیت از گروه بن شد.\n"
                f"دلیل: <i>{reason}</i>\n"
                f"توسط: {admin_mention}",
                parse_mode=ParseMode.HTML
            )
            await log_admin_action(session, chat_id, admin_id, "ban", target_user_id, reason)

        except SQLAlchemyError as e:
            logger.error(f"Database error during ban_command update for chat {chat_id}: {e}")
            await update.message.reply_text("❗️ کاربر بن شد، اما خطایی در ثبت دیتابیس رخ داد.")
        finally:
            session.close()

    except BadRequest as e:
        if "Can't remove chat owner" in str(e) or "Chat_admin_required" in str(e):
            await update.message.reply_text("🚫 من اجازه بن کردن این کاربر را ندارم (شاید ادمین است یا خودم ادمین نیستم).")
        else:
            logger.error(f"Error banning user {target_user_id} in chat {chat_id}: {e}")
            await update.message.reply_text(f"❗️ خطایی در بن کردن کاربر رخ داد: {e}")
    except TelegramError as e:
        logger.error(f"Telegram error in ban_command for chat {chat_id}: {e}")
        await update.message.reply_text(f"❗️ خطایی در تلگرام رخ داد: {e}")

@require_admin_permission("can_restrict_members")
async def tempban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /tempban command. Temporarily bans a user from the group for a specified duration.
    Syntax: /tempban [reply or ID] [duration (e.g., 30m, 1h, 7d)]
    """
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    admin_mention = get_user_mention(update.effective_user)

    target_user_id = await extract_target_user_id(update, context, context.args)
    if not target_user_id:
        return

    if target_user_id == admin_id:
        await update.message.reply_text("شما نمی‌توانید خودتان را بن موقت کنید! 😅")
        return
    if target_user_id == context.bot.id:
        await update.message.reply_text("شما نمی‌توانید من را بن موقت کنید! 🤖")
        return
    if await is_user_admin_or_owner(chat_id, target_user_id, context):
        await update.message.reply_text("نمی‌توان ادمین‌ها یا مالک گروه را بن موقت کرد.")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "لطفاً مدت زمان بن موقت را وارد کنید. مثال: `/tempban @user 1h` (1 ساعت), `/tempban 123456789 3d` (3 روز).",
            parse_mode=ParseMode.HTML
        )
        return
    
    duration_str = context.args[1]
    parsed_duration = parse_time_duration(duration_str)

    if not parsed_duration:
        await update.message.reply_text(
            "⚠️ فرمت زمان نامعتبر است. مثال‌ها: `30m` (30 دقیقه), `1h` (1 ساعت), `7d` (7 روز)."
        )
        return
    
    until_date = datetime.now() + parsed_duration
    reason = " ".join(context.args[2:]) if len(context.args) > 2 else "بدون دلیل"

    try:
        # Ban user until a specific date/time
        await context.bot.ban_chat_member(chat_id, target_user_id, until_date=until_date)

        session = get_session()
        try:
            target_user_info = await get_user_info_from_telegram(context, target_user_id)
            db_target_user = await get_or_create_user(session, target_user_info)
            target_mention = get_user_mention(db_target_user)

            # Update group_user info or delete if already banned and track temp ban
            group_user = session.query(GroupUser).filter_by(group_id=chat_id, user_id=target_user_id).first()
            if group_user:
                session.delete(group_user) # Remove for clean slate, let Telegram handle unban
            session.commit() # Commit deletion

            await update.message.reply_text(
                f"🚫 {target_mention} به مدت <b>{duration_str}</b> از گروه بن شد.\n"
                f"دلیل: <i>{reason}</i>\n"
                f"توسط: {admin_mention}",
                parse_mode=ParseMode.HTML
            )
            await log_admin_action(session, chat_id, admin_id, "temp_ban", target_user_id, reason)

        except SQLAlchemyError as e:
            logger.error(f"Database error during tempban_command update for chat {chat_id}: {e}")
            await update.message.reply_text("❗️ کاربر بن موقت شد، اما خطایی در ثبت دیتابیس رخ داد.")
        finally:
            session.close()

    except BadRequest as e:
        if "Can't remove chat owner" in str(e) or "Chat_admin_required" in str(e):
            await update.message.reply_text("🚫 من اجازه بن کردن این کاربر را ندارم (شاید ادمین است یا خودم ادمین نیستم).")
        else:
            logger.error(f"Error temp-banning user {target_user_id} in chat {chat_id}: {e}")
            await update.message.reply_text(f"❗️ خطایی در بن موقت کاربر رخ داد: {e}")
    except TelegramError as e:
        logger.error(f"Telegram error in tempban_command for chat {chat_id}: {e}")
        await update.message.reply_text(f"❗️ خطایی در تلگرام رخ داد: {e}")


@require_admin_permission("can_restrict_members")
async def kick_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /kick command. Kicks a user from the group by temporarily banning them for a very short period,
    after which they can rejoin.
    """
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    admin_mention = get_user_mention(update.effective_user)

    target_user_id = await extract_target_user_id(update, context, context.args)
    if not target_user_id:
        return

    if target_user_id == admin_id:
        await update.message.reply_text("شما نمی‌توانید خودتان را کیک کنید! 😅")
        return
    if target_user_id == context.bot.id:
        await update.message.reply_text("شما نمی‌توانید من را کیک کنید! 🤖")
        return
    if await is_user_admin_or_owner(chat_id, target_user_id, context):
        await update.message.reply_text("نمی‌توان ادمین‌ها یا مالک گروه را کیک کرد.")
        return

    reason = " ".join(context.args[1:]) if len(context.args) > 1 else "بدون دلیل"

    try:
        # Ban user for a very short period (e.g., 30 seconds) to simulate a kick.
        # Telegram automatically unbans after this period.
        await context.bot.ban_chat_member(chat_id, target_user_id, until_date=datetime.now() + timedelta(seconds=30))
        
        session = get_session()
        target_user_info = await get_user_info_from_telegram(context, target_user_id)
        db_target_user = await get_or_create_user(session, target_user_info)
        target_mention = get_user_mention(db_target_user)
        session.close() # Close session after db interaction

        await update.message.reply_text(
            f"👢 {target_mention} از گروه کیک شد.\n"
            f"دلیل: <i>{reason}</i>\n"
            f"توسط: {admin_mention}",
            parse_mode=ParseMode.HTML
        )
        await log_admin_action(session, chat_id, admin_id, "kick", target_user_id, reason)

    except BadRequest as e:
        if "Can't remove chat owner" in str(e) or "Chat_admin_required" in str(e):
            await update.message.reply_text("🚫 من اجازه کیک کردن این کاربر را ندارم (شاید ادمین است یا خودم ادمین نیستم).")
        else:
            logger.error(f"Error kicking user {target_user_id} in chat {chat_id}: {e}")
            await update.message.reply_text(f"❗️ خطایی در کیک کردن کاربر رخ داد: {e}")
    except TelegramError as e:
        logger.error(f"Telegram error in kick_command for chat {chat_id}: {e}")
        await update.message.reply_text(f"❗️ خطایی در تلگرام رخ داد: {e}")

@require_admin_permission("can_delete_messages")
async def purge_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /purge command. Deletes a specified number of recent messages from the group.
    Syntax: /purge [number]
    If replied to a message, deletes messages from the replied message up to the purge command message.
    """
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id

    if not update.message.reply_to_message and not context.args:
        await update.message.reply_text("لطفاً به پیامی ریپلای کنید یا تعداد پیام‌هایی که می‌خواهید حذف کنید را پس از دستور وارد کنید.")
        return
    
    # Determine the range of messages to delete
    message_ids_to_delete: List[int] = []
    
    if update.message.reply_to_message:
        start_message_id = update.message.reply_to_message.message_id
        end_message_id = update.message.message_id # Inclusive of the purge command message
        
        # Max 100 messages for purge to avoid excessive deletion
        if (end_message_id - start_message_id + 1) > 100:
            await update.message.reply_text("⚠️ شما می‌توانید حداکثر 100 پیام را با یک دستور /purge حذف کنید. پیام‌های زیادی انتخاب شده‌اند.")
            return

        for i in range(start_message_id, end_message_id + 1):
            message_ids_to_delete.append(i)
    elif context.args:
        try:
            count = int(context.args[0])
            if not (1 <= count <= 100):
                await update.message.reply_text("لطفاً عددی بین 1 تا 100 برای تعداد پیام‌های قابل حذف وارد کنید.")
                return
            
            # Delete `count` messages including the purge command itself
            for i in range(update.message.message_id - count + 1, update.message.message_id + 1):
                message_ids_to_delete.append(i)
        except ValueError:
            await update.message.reply_text("لطفاً یک عدد معتبر برای تعداد پیام‌ها وارد کنید.")
            return

    if not message_ids_to_delete:
        await update.message.reply_text("خطایی در تعیین پیام‌های قابل حذف رخ داد.")
        return

    try:
        await context.bot.delete_messages(chat_id, message_ids_to_delete)
        # We can't reply to a deleted message, so send a new message
        temp_msg = await update.effective_chat.send_message(
            f"🗑️ تعداد <b>{len(message_ids_to_delete)}</b> پیام حذف شد.",
            parse_mode=ParseMode.HTML
        )
        await asyncio.sleep(3) # Delete this confirmation message after 3 seconds
        await temp_msg.delete()
        await log_admin_action(session, chat_id, admin_id, "purge_messages", reason=f"Deleted {len(message_ids_to_delete)} messages")

    except BadRequest as e:
        if "message can't be deleted" in str(e).lower() or "message to delete not found" in str(e).lower():
            await update.message.reply_text("⚠️ برخی از پیام‌ها بسیار قدیمی هستند یا ربات اجازه حذف آن‌ها را ندارد.")
        elif "Chat_admin_required" in str(e):
            await update.message.reply_text("🚫 من برای حذف پیام‌ها به دسترسی ادمین 'Delete messages' نیاز دارم.")
        else:
            logger.error(f"Error purging messages in chat {chat_id}: {e}")
            await update.message.reply_text(f"❗️ خطایی در حذف پیام‌ها رخ داد: {e}")
    except Exception as e:
        logger.error(f"Unexpected error purging messages in chat {chat_id}: {e}")
        await update.message.reply_text(f"❗️ خطایی غیرمنتظره در حذف پیام‌ها رخ داد: {e}")
    finally:
        session.close() # Ensure session is closed

@require_admin_permission("can_delete_messages")
async def delete_message_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /del command. Deletes the message to which the command is replied.
    """
    if not update.message or not update.effective_chat or not update.effective_user:
        return
    
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id

    if not update.message.reply_to_message:
        await update.message.reply_text("لطفاً به پیامی که می‌خواهید حذف کنید، ریپلای کنید.")
        return
    
    message_to_delete_id = update.message.reply_to_message.message_id
    command_message_id = update.message.message_id

    try:
        # Delete the replied message and the command message itself
        await context.bot.delete_messages(chat_id, [message_to_delete_id, command_message_id])
        await log_admin_action(session, chat_id, admin_id, "delete_message", reason=f"Deleted message_id {message_to_delete_id}")

    except BadRequest as e:
        if "message can't be deleted" in str(e).lower() or "message to delete not found" in str(e).lower():
            await update.message.reply_text("⚠️ پیام مورد نظر بسیار قدیمی است یا ربات اجازه حذف آن را ندارد.")
        elif "Chat_admin_required" in str(e):
            await update.message.reply_text("🚫 من برای حذف پیام‌ها به دسترسی ادمین 'Delete messages' نیاز دارم.")
        else:
            logger.error(f"Error deleting message in chat {chat_id}: {e}")
            await update.message.reply_text(f"❗️ خطایی در حذف پیام رخ داد: {e}")
    except Exception as e:
        logger.error(f"Unexpected error deleting message in chat {chat_id}: {e}")
        await update.message.reply_text(f"❗️ خطایی غیرمنتظره در حذف پیام رخ داد: {e}")
    finally:
        session.close() # Ensure session is closed if it was opened somewhere for logging

@require_admin_permission("can_pin_messages")
async def pin_message_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /pin command. Pins the message to which the command is replied.
    """
    if not update.message or not update.effective_chat or not update.effective_user:
        return
    
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id

    if not update.message.reply_to_message:
        await update.message.reply_text("لطفاً به پیامی که می‌خواهید سنجاق کنید، ریپلای کنید.")
        return
    
    message_to_pin_id = update.message.reply_to_message.message_id

    try:
        await context.bot.pin_chat_message(chat_id, message_to_pin_id)
        await update.message.reply_text("📌 پیام با موفقیت سنجاق شد.")
        await log_admin_action(session, chat_id, admin_id, "pin_message", reason=f"Pinned message_id {message_to_pin_id}")

    except BadRequest as e:
        if "Chat_admin_required" in str(e) or "not enough rights to pin a message" in str(e):
            await update.message.reply_text("🚫 من برای سنجاق کردن پیام‌ها به دسترسی ادمین 'Pin messages' نیاز دارم.")
        else:
            logger.error(f"Error pinning message in chat {chat_id}: {e}")
            await update.message.reply_text(f"❗️ خطایی در سنجاق کردن پیام رخ داد: {e}")
    except TelegramError as e:
        logger.error(f"Telegram error in pin_message_command for chat {chat_id}: {e}")
        await update.message.reply_text(f"❗️ خطایی در تلگرام رخ داد: {e}")
    finally:
        session.close() # Ensure session is closed if it was opened somewhere for logging


@require_admin_permission("can_pin_messages")
async def unpin_message_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /unpin command. Unpins the last pinned message in the group.
    """
    if not update.message or not update.effective_chat or not update.effective_user:
        return
    
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id

    try:
        await context.bot.unpin_all_chat_messages(chat_id) # Unpins all messages, can modify to unpin specific
        await update.message.reply_text("🗑️ تمامی پیام‌های سنجاق شده برداشته شدند.")
        await log_admin_action(session, chat_id, admin_id, "unpin_all_messages")

    except BadRequest as e:
        if "Chat_admin_required" in str(e) or "not enough rights to pin a message" in str(e):
            await update.message.reply_text("🚫 من برای برداشتن سنجاق پیام‌ها به دسترسی ادمین 'Pin messages' نیاز دارم.")
        else:
            logger.error(f"Error unpinning message in chat {chat_id}: {e}")
            await update.message.reply_text(f"❗️ خطایی در برداشتن سنجاق پیام‌ها رخ داد: {e}")
    except TelegramError as e:
        logger.error(f"Telegram error in unpin_message_command for chat {chat_id}: {e}")
        await update.message.reply_text(f"❗️ خطایی در تلگرام رخ داد: {e}")
    finally:
        session.close() # Ensure session is closed if it was opened somewhere for logging

@require_admin_permission("can_delete_messages") # Deleting locked media requires this permission
async def lock_media_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /lock command. Locks a specific type of media (e.g., photos, links) in the group.
    Syntax: /lock [media_type]
    """
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    if not context.args:
        await update.message.reply_text(
            "لطفاً نوع رسانه‌ای که می‌خواهید قفل کنید را مشخص کنید. مثال: `/lock photo`\n"
            "انواع: `photo`, `video`, `link`, `forward`, `sticker`, `gif`, `voice`, `document`, `videonote`, `poll`, `game`",
            parse_mode=ParseMode.HTML
        )
        return

    media_type = context.args[0].lower()
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    
    # Map media type to corresponding Group model field
    media_field_map = {
        "photo": "lock_photos",
        "video": "lock_videos",
        "link": "lock_links",
        "forward": "lock_forwards",
        "sticker": "lock_stickers",
        "gif": "lock_gifs",
        "voice": "lock_voice",
        "document": "lock_documents",
        "videonote": "lock_videonotes",
        "poll": "lock_polls",
        "game": "lock_games",
    }

    if media_type not in media_field_map:
        await update.message.reply_text("⚠️ نوع رسانه نامعتبر است. لطفاً یکی از موارد مجاز را انتخاب کنید.")
        return
    
    field_to_update = media_field_map[media_type]
    
    session = get_session()
    try:
        db_group = await get_or_create_group(session, chat_id, update.effective_chat.title)
        
        # Check if it's already locked
        if getattr(db_group, field_to_update):
            await update.message.reply_text(f"ℹ️ {media_type} از قبل قفل بود.")
            return

        await update_group_settings(session, chat_id, **{field_to_update: True})
        await update.message.reply_text(f"✅ ارسال <b>{media_type}</b> در گروه قفل شد.", parse_mode=ParseMode.HTML)
        await log_admin_action(session, chat_id, admin_id, "lock_media", reason=media_type)

    except SQLAlchemyError as e:
        logger.error(f"Database error in lock_media_command for chat {chat_id}: {e}")
        await update.message.reply_text("❗️ خطایی در ارتباط با دیتابیس رخ داد. لطفاً بعداً امتحان کنید.")
    finally:
        session.close()

@require_admin_permission("can_delete_messages") # Unlocking doesn't require delete, but locking does, so keep consistent
async def unlock_media_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /unlock command. Unlocks a specific type of media in the group.
    Syntax: /unlock [media_type]
    """
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    if not context.args:
        await update.message.reply_text(
            "لطفاً نوع رسانه‌ای که می‌خواهید قفل آن را باز کنید را مشخص کنید. مثال: `/unlock photo`\n"
            "انواع: `photo`, `video`, `link`, `forward`, `sticker`, `gif`, `voice`, `document`, `videonote`, `poll`, `game`",
            parse_mode=ParseMode.HTML
        )
        return

    media_type = context.args[0].lower()
    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    
    media_field_map = {
        "photo": "lock_photos",
        "video": "lock_videos",
        "link": "lock_links",
        "forward": "lock_forwards",
        "sticker": "lock_stickers",
        "gif": "lock_gifs",
        "voice": "lock_voice",
        "document": "lock_documents",
        "videonote": "lock_videonotes",
        "poll": "lock_polls",
        "game": "lock_games",
    }

    if media_type not in media_field_map:
        await update.message.reply_text("⚠️ نوع رسانه نامعتبر است. لطفاً یکی از موارد مجاز را انتخاب کنید.")
        return
    
    field_to_update = media_field_map[media_type]
    
    session = get_session()
    try:
        db_group = await get_or_create_group(session, chat_id, update.effective_chat.title)
        
        # Check if it's already unlocked
        if not getattr(db_group, field_to_update):
            await update.message.reply_text(f"ℹ️ {media_type} از قبل قفل نبود.")
            return

        await update_group_settings(session, chat_id, **{field_to_update: False})
        await update.message.reply_text(f"✅ ارسال <b>{media_type}</b> در گروه باز شد.", parse_mode=ParseMode.HTML)
        await log_admin_action(session, chat_id, admin_id, "unlock_media", reason=media_type)

    except SQLAlchemyError as e:
        logger.error(f"Database error in unlock_media_command for chat {chat_id}: {e}")
        await update.message.reply_text("❗️ خطایی در ارتباط با دیتابیس رخ داد. لطفاً بعداً امتحان کنید.")
    finally:
        session.close()

@require_admin_permission("can_change_info") # Changing reputation is an admin-level action
async def reputation_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /reputation command. Allows admins to adjust a user's reputation score.
    Syntax: /reputation [reply or ID] [+ / -]
    """
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    admin_id = update.effective_user.id
    admin_mention = get_user_mention(update.effective_user)

    if len(context.args) < 2:
        await update.message.reply_text("لطفاً مشخص کنید که آیا اعتبار کاربر را افزایش یا کاهش می‌دهید. مثال: `/reputation @user +` یا `/reputation 123456789 -`")
        return

    target_user_id = await extract_target_user_id(update, context, context.args[:-1]) # last arg is +/-
    if not target_user_id:
        return

    if target_user_id == admin_id:
        await update.message.reply_text("شما نمی‌توانید اعتبار خودتان را تغییر دهید.")
        return
    if target_user_id == context.bot.id:
        await update.message.reply_text("شما نمی‌توانید اعتبار من را تغییر دهید. من یک رباتم!")
        return
    if await is_user_admin_or_owner(chat_id, target_user_id, context):
        await update.message.reply_text("نمی‌توان اعتبار ادمین‌ها یا مالک گروه را تغییر داد.")
        return

    action_symbol = context.args[-1]
    
    session = get_session()
    try:
        target_user_info = await get_user_info_from_telegram(context, target_user_id)
        if not target_user_info:
            await update.message.reply_text("🚫 اطلاعات کاربر مورد نظر یافت نشد. شاید از گروه خارج شده باشد.")
            return
        db_target_user = await get_or_create_user(session, target_user_info)
        target_mention = get_user_mention(db_target_user)

        if action_symbol == "+":
            db_target_user.reputation += 1
            action_type = "rep_up"
            change_text = "افزایش یافت"
        elif action_symbol == "-":
            db_target_user.reputation -= 1
            action_type = "rep_down"
            change_text = "کاهش یافت"
        else:
            await update.message.reply_text("⚠️ عملگر نامعتبر است. لطفاً از '+' برای افزایش یا '-' برای کاهش استفاده کنید.")
            return
        
        session.commit()
        await update.message.reply_text(
            f"📈 اعتبار {target_mention} به <b>{db_target_user.reputation}</b> {change_text}.\n"
            f"توسط: {admin_mention}",
            parse_mode=ParseMode.HTML
        )
        await log_admin_action(session, chat_id, admin_id, action_type, target_user_id)

    except SQLAlchemyError as e:
        logger.error(f"Database error in reputation_command for chat {chat_id}: {e}")
        await update.message.reply_text("❗️ خطایی در ارتباط با دیتابیس رخ داد. لطفاً بعداً امتحان کنید.")
    finally:
        session.close()

async def check_reputation_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /checkrep command. Displays a user's current reputation score.
    """
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    target_user_id = await extract_target_user_id(update, context, context.args)
    
    session = get_session()
    try:
        if not target_user_id:
            # If no target, check own reputation
            target_user_id = update.effective_user.id
            is_self_check = True
        else:
            is_self_check = False

        target_user_info = await get_user_info_from_telegram(context, target_user_id)
        if not target_user_info:
            await update.message.reply_text("🚫 اطلاعات کاربر مورد نظر یافت نشد. شاید از گروه خارج شده باشد.")
            return
        db_target_user = await get_or_create_user(session, target_user_info)
        target_mention = get_user_mention(db_target_user)
        
        await update.message.reply_text(
            f"📊 اعتبار {target_mention} در حال حاضر: <b>{db_target_user.reputation}</b>",
            parse_mode=ParseMode.HTML
        )
    except SQLAlchemyError as e:
        logger.error(f"Database error in check_reputation_command for chat {chat_id}: {e}")
        await update.message.reply_text("❗️ خطایی در ارتباط با دیتابیس رخ داد. لطفاً بعداً امتحان کنید.")
    finally:
        session.close()

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /id command. Responds with the user's ID and, if replied to a message, the replied user's ID
    and the chat ID.
    """
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    response_text = f"👤 <b>آیدی شما:</b> <code>{user_id}</code>\n"
    response_text += f"💬 <b>آیدی این چت:</b> <code>{chat_id}</code>\n"

    if update.message.reply_to_message:
        replied_user = update.message.reply_to_message.from_user
        if replied_user:
            response_text += f"↩️ <b>آیدی کاربر ریپلای شده:</b> <code>{replied_user.id}</code>\n"
        if update.message.reply_to_message.forward_from:
            forwarded_user = update.message.reply_to_message.forward_from
            response_text += f"➡️ <b>آیدی فرستنده اصلی (فوروارد):</b> <code>{forwarded_user.id}</code>\n"
        response_text += f"✉️ <b>آیدی پیام ریپلای شده:</b> <code>{update.message.reply_to_message.message_id}</code>"
    
    await update.message.reply_text(response_text, parse_mode=ParseMode.HTML)

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /info command. Provides detailed information about a target user.
    """
    if not update.message or not update.effective_chat or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    target_user_id = await extract_target_user_id(update, context, context.args)
    if not target_user_id:
        return

    session = get_session()
    try:
        telegram_user_info = await get_user_info_from_telegram(context, target_user_id)
        if not telegram_user_info:
            await update.message.reply_text("🚫 اطلاعات کاربر مورد نظر یافت نشد. شاید از گروه خارج شده باشد.")
            return

        db_user = await get_or_create_user(session, telegram_user_info)
        db_group_user = session.query(GroupUser).filter_by(group_id=chat_id, user_id=target_user_id).first()

        member_status: str = "عضو"
        try:
            chat_member: ChatMember = await context.bot.get_chat_member(chat_id, target_user_id)
            member_status = chat_member.status.value
            if member_status == ChatMemberStatus.ADMINISTRATOR:
                member_status = "مدیر"
            elif member_status == ChatMemberStatus.OWNER:
                member_status = "مالک"
            elif member_status == ChatMemberStatus.KICKED:
                member_status = "بن شده"
            elif member_status == ChatMemberStatus.LEFT:
                member_status = "ترک کرده"
            elif member_status == ChatMemberStatus.RESTRICTED:
                member_status = "محدود شده"
        except TelegramError:
            member_status = "ناشناخته/خارج شده"

        info_text = (
            f"👤 <b>اطلاعات کاربر:</b> {get_user_mention(db_user)}\n"
            f"  • شناسه (ID): <code>{db_user.id}</code>\n"
            f"  • نام: {db_user.first_name} {(db_user.last_name or '')}\n"
            f"  • نام کاربری: {f'@{db_user.username}' if db_user.username else 'ندارد'}\n"
            f"  • وضعیت در گروه: <b>{member_status}</b>\n"
            f"  • ربات: {'✅ بله' if db_user.is_bot else '❌ خیر'}\n"
            f"  • اعتبار (Reputation): <b>{db_user.reputation}</b>\n"
        )
        if db_group_user:
            info_text += (
                f"  • اخطارها: <b>{db_group_user.warns}</b>\n"
                f"  • میوت شده: {'✅ بله' if db_group_user.is_muted else '❌ خیر'}\n"
            )
            if db_group_user.mute_until and db_group_user.is_muted:
                info_text += f"    تا: {db_group_user.mute_until.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        
        await update.message.reply_text(info_text, parse_mode=ParseMode.HTML)

    except SQLAlchemyError as e:
        logger.error(f"Database error in info_command for chat {chat_id}: {e}")
        await update.message.reply_text("❗️ خطایی در ارتباط با دیتابیس رخ داد. لطفاً بعداً امتحان کنید.")
    finally:
        session.close()

async def group_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /groupinfo command. Provides detailed information about the current group.
    """
    if not update.message or not update.effective_chat or not update.effective_user:
        return
    if update.effective_chat.type == ChatType.PRIVATE:
        await update.message.reply_text("این دستور فقط در گروه‌ها قابل استفاده است.")
        return

    chat_id = update.effective_chat.id
    
    session = get_session()
    try:
        db_group = await get_or_create_group(session, chat_id, update.effective_chat.title)

        chat_obj = await context.bot.get_chat(chat_id)
        
        info_text = (
            f"🏙️ <b>اطلاعات گروه:</b>\n"
            f"  • نام گروه: <b>{db_group.title}</b>\n"
            f"  • شناسه (ID): <code>{db_group.id}</code>\n"
            f"  • نوع گروه: {chat_obj.type.value}\n"
            f"  • تعداد اعضا: <b>{chat_obj.get_member_count()}</b>\n"
            f"  • خوش‌آمدگویی: {'✅ فعال' if db_group.welcome_enabled else '❌ غیرفعال'}\n"
            f"  • قوانین: {'✅ فعال' if db_group.rules_enabled else '❌ غیرفعال'}\n"
            f"  • ضد فلود: {'✅ فعال' if db_group.anti_flood_enabled else '❌ غیرفعال'} "
            f"({db_group.anti_flood_limit} پیام در {db_group.anti_flood_time} ثانیه)\n"
            f"  • اخطار تا میوت: {db_group.mute_on_warn_count}\n"
            f"  • اخطار تا بن: {db_group.ban_on_warn_count}\n"
            f"  • قفل رسانه‌ها:\n"
            f"    - عکس: {'🔒 فعال' if db_group.lock_photos else '🔓 غیرفعال'}\n"
            f"    - ویدئو: {'🔒 فعال' if db_group.lock_videos else '🔓 غیرفعال'}\n"
            f"    - لینک: {'🔒 فعال' if db_group.lock_links else '🔓 غیرفعال'}\n"
            f"    - فوروارد: {'🔒 فعال' if db_group.lock_forwards else '🔓 غیرفعال'}\n"
            f"    - استیکر: {'🔒 فعال' if db_group.lock_stickers else '🔓 غیرفعال'}\n"
            f"    - گیف: {'🔒 فعال' if db_group.lock_gifs else '🔓 غیرفعال'}\n"
            f"    - ویس: {'🔒 فعال' if db_group.lock_voice else '🔓 غیرفعال'}\n"
            f"    - سند: {'🔒 فعال' if db_group.lock_documents else '🔓 غیرفعال'}\n"
            f"    - پیام ویدیویی: {'🔒 فعال' if db_group.lock_videonotes else '🔓 غیرفعال'}\n"
            f"    - نظرسنجی: {'🔒 فعال' if db_group.lock_polls else '🔓 غیرفعال'}\n"
            f"    - بازی: {'🔒 فعال' if db_group.lock_games else '🔓 غیرفعال'}\n"
            f"  • محدودیت عضو جدید: {'✅ فعال' if db_group.restrict_new_members else '❌ غیرفعال'} ({db_group.restrict_duration_minutes} دقیقه)"
        )
        await update.message.reply_text(info_text, parse_mode=ParseMode.HTML)

    except SQLAlchemyError as e:
        logger.error(f"Database error in group_info_command for chat {chat_id}: {e}")
        await update.message.reply_text("❗️ خطایی در ارتباط با دیتابیس رخ داد. لطفاً بعداً امتحان کنید.")
    except TelegramError as e:
        logger.error(f"Telegram API error in group_info_command for chat {chat_id}: {e}")
        await update.message.reply_text("❗️ خطایی در دریافت اطلاعات گروه از تلگرام رخ داد. آیا ربات ادمین است؟")
    finally:
        session.close()

@require_bot_owner_only
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /status command (bot owner only). Provides internal bot status information,
    including database statistics.
    """
    if not update.message:
        return

    session = get_session()
    try:
        total_groups = session.query(Group).count()
        total_users = session.query(User).count()
        total_group_users = session.query(GroupUser).count()
        total_forbidden_words = session.query(ForbiddenWord).count()
        total_admin_logs = session.query(AdminLog).count()
        
        status_text = (
            "<b>📊 وضعیت ربات Digi Anti:</b>\n"
            f"  • گروه‌های تحت پوشش: <b>{total_groups}</b>\n"
            f"  • کل کاربران ذخیره شده: <b>{total_users}</b>\n"
            f"  • ارتباطات گروه-کاربر: <b>{total_group_users}</b>\n"
            f"  • کلمات ممنوعه ثبت شده: <b>{total_forbidden_words}</b>\n"
            f"  • لاگ‌های ادمین: <b>{total_admin_logs}</b>\n"
            "ربات در حال اجرا است و به پیام‌ها پاسخ می‌دهد. ✅"
        )
        await update.message.reply_text(status_text, parse_mode=ParseMode.HTML)
    except SQLAlchemyError as e:
        logger.error(f"Database error in status_command: {e}")
        await update.message.reply_text("❗️ خطایی در ارتباط با دیتابیس رخ داد. لطفاً بعداً امتحان کنید.")
    finally:
        session.close()

@require_bot_owner_only
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /broadcast command (bot owner only). Sends a message to all groups
    the bot is currently managing.
    """
    if not update.message:
        return

    if not context.args:
        await update.message.reply_text("لطفاً متن پیامی که می‌خواهید به گروه‌ها ارسال کنید را وارد کنید.")
        return

    message_to_send = " ".join(context.args)
    session = get_session()
    sent_count = 0
    failed_count = 0
    
    try:
        groups = session.query(Group).all()
        for group in groups:
            try:
                await context.bot.send_message(group.id, message_to_send, parse_mode=ParseMode.HTML)
                sent_count += 1
                await asyncio.sleep(0.1) # Small delay to avoid hitting flood limits on Telegram API
            except Forbidden:
                logger.warning(f"Bot was blocked by group {group.id} ('{group.title}').")
                failed_count += 1
                # Optional: Consider removing group from DB if bot is blocked
                # session.delete(group)
                # session.commit()
            except TelegramError as e:
                logger.error(f"Failed to send broadcast to group {group.id} ('{group.title}'): {e}")
                failed_count += 1
        
        await update.message.reply_text(
            f"✅ پیام به <b>{sent_count}</b> گروه ارسال شد.\n"
            f"❌ ارسال به <b>{failed_count}</b> گروه ناموفق بود.",
            parse_mode=ParseMode.HTML
        )

    except SQLAlchemyError as e:
        logger.error(f"Database error in broadcast_command: {e}")
        await update.message.reply_text("❗️ خطایی در ارتباط با دیتابیس رخ داد. لطفاً بعداً امتحان کنید.")
    finally:
        session.close()

@require_bot_owner_only
async def list_groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /listgroups command (bot owner only). Lists all groups the bot is currently in.
    """
    if not update.message:
        return

    session = get_session()
    try:
        groups = session.query(Group).all()
        if not groups:
            await update.message.reply_text("ربات در حال حاضر در هیچ گروهی نیست.")
            return

        group_list_text = "<b>لیست گروه‌های تحت مدیریت:</b>\n\n"
        for group in groups:
            group_list_text += f"• <b>{group.title}</b> (<code>{group.id}</code>)\n"
        
        await update.message.reply_text(group_list_text, parse_mode=ParseMode.HTML)
    except SQLAlchemyError as e:
        logger.error(f"Database error in list_groups_command: {e}")
        await update.message.reply_text("❗️ خطایی در ارتباط با دیتابیس رخ داد. لطفاً بعداً امتحان کنید.")
    finally:
        session.close()

@require_bot_owner_only
async def leave_group_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles the /leavegroup command (bot owner only). Makes the bot leave a specified group.
    Syntax: /leavegroup [chat_id]
    """
    if not update.message:
        return

    if not context.args:
        await update.message.reply_text("لطفاً شناسه گروهی که می‌خواهید ربات از آن خارج شود را وارد کنید.")
        return

    try:
        chat_id_to_leave = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ شناسه گروه نامعتبر است. لطفاً یک عدد وارد کنید.")
        return

    session = get_session()
    try:
        db_group = session.query(Group).filter_by(id=chat_id_to_leave).first()
        if not db_group:
            await update.message.reply_text(f"⚠️ گروه با شناسه <code>{chat_id_to_leave}</code> در دیتابیس یافت نشد.", parse_mode=ParseMode.HTML)
            return

        await context.bot.leave_chat(chat_id_to_leave)
        
        # Remove group and related data from DB after leaving
        session.delete(db_group)
        session.query(GroupUser).filter_by(group_id=chat_id_to_leave).delete()
        session.query(ForbiddenWord).filter_by(group_id=chat_id_to_leave).delete()
        session.query(AdminLog).filter_by(group_id=chat_id_to_leave).delete()
        session.commit()

        await update.message.reply_text(f"✅ ربات با موفقیت از گروه <b>{db_group.title}</b> (<code>{chat_id_to_leave}</code>) خارج شد و اطلاعات آن حذف گردید.", parse_mode=ParseMode.HTML)
        logger.info(f"Bot left group {chat_id_to_leave} and its data was cleaned.")

    except Forbidden:
        await update.message.reply_text(f"⚠️ ربات امکان خروج از گروه <code>{chat_id_to_leave}</code> را ندارد (شاید قبلاً خارج شده یا بن شده است).", parse_mode=ParseMode.HTML)
    except TelegramError as e:
        logger.error(f"Telegram error in leave_group_command for chat {chat_id_to_leave}: {e}")
        await update.message.reply_text(f"❗️ خطایی در خروج از گروه رخ داد: {e}")
    except SQLAlchemyError as e:
        logger.error(f"Database error in leave_group_command for chat {chat_id_to_leave}: {e}")
        await update.message.reply_text("❗️ خطایی در ارتباط با دیتابیس هنگام خروج از گروه رخ داد.")
    finally:
        session.close()


# --- [ 5. Callback Query Handlers (for Inline Keyboards) ] ---
# These functions respond to button presses from inline keyboards, primarily for the /settings panel.

async def settings_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles callbacks generated by inline keyboard buttons from the /settings command.
    Manages various group settings based on the button pressed.
    """
    query = update.callback_query
    if not query or not query.message or not query.from_user:
        return

    await query.answer() # Acknowledge the query to prevent "Loading" state on the button
    
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    data = query.data

    session = get_session()
    try:
        db_group = session.query(Group).filter_by(id=chat_id).first()
        if not db_group:
            await query.edit_message_text("❗️ خطا: تنظیمات گروه یافت نشد. لطفاً ربات را دوباره اضافه کنید.")
            return
        
        # Security check: ensure the user interacting with settings is an admin
        if not await is_user_admin_or_owner(chat_id, user_id, context):
            await query.answer("🚫 شما اجازه تغییر تنظیمات را ندارید. فقط ادمین‌ها می‌توانند.", show_alert=True)
            return

        # Handle different callback data
        if data == "settings_toggle_welcome":
            db_group.welcome_enabled = not db_group.welcome_enabled
            await update_group_settings(session, chat_id, welcome_enabled=db_group.welcome_enabled)
            await query.edit_message_text(f"✅ پیام خوش‌آمدگویی: {'فعال شد' if db_group.welcome_enabled else 'غیرفعال شد'}!")
            await log_admin_action(session, chat_id, user_id, "toggle_welcome", reason=f"Set to {db_group.welcome_enabled}")
            await refresh_settings_panel(query, context, session)
        
        elif data == "settings_toggle_rules":
            db_group.rules_enabled = not db_group.rules_enabled
            await update_group_settings(session, chat_id, rules_enabled=db_group.rules_enabled)
            await query.edit_message_text(f"✅ پیام قوانین: {'فعال شد' if db_group.rules_enabled else 'غیرفعال شد'}!")
            await log_admin_action(session, chat_id, user_id, "toggle_rules", reason=f"Set to {db_group.rules_enabled}")
            await refresh_settings_panel(query, context, session)

        elif data == "settings_toggle_anti_flood":
            db_group.anti_flood_enabled = not db_group.anti_flood_enabled
            await update_group_settings(session, chat_id, anti_flood_enabled=db_group.anti_flood_enabled)
            await query.edit_message_text(f"✅ ضد فلود: {'فعال شد' if db_group.anti_flood_enabled else 'غیرفعال شد'}!")
            await log_admin_action(session, chat_id, user_id, "toggle_anti_flood", reason=f"Set to {db_group.anti_flood_enabled}")
            await refresh_settings_panel(query, context, session)

        elif data == "settings_anti_flood_options":
            # Sub-menu for anti-flood settings
            keyboard = [
                [InlineKeyboardButton("کمتر (3 پیام / 5 ثانیه)", callback_data="set_flood_3_5")],
                [InlineKeyboardButton("متوسط (5 پیام / 10 ثانیه)", callback_data="set_flood_5_10")],
                [InlineKeyboardButton("زیاد (7 پیام / 15 ثانیه)", callback_data="set_flood_7_15")],
                [InlineKeyboardButton("بازگشت ⬅️", callback_data="settings_back")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "لطفاً سطح حساسیت ضد فلود را انتخاب کنید:",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        
        elif data.startswith("set_flood_"):
            parts = data.split('_')
            if len(parts) == 4:
                limit = int(parts[2])
                time = int(parts[3])
                await update_group_settings(session, chat_id, anti_flood_limit=limit, anti_flood_time=time)
                await query.edit_message_text(f"✅ تنظیمات ضد فلود به {limit} پیام در {time} ثانیه تغییر یافت.")
                await log_admin_action(session, chat_id, user_id, "set_anti_flood", reason=f"{limit} msg/{time}s")
                await refresh_settings_panel(query, context, session)

        elif data == "settings_mute_warn_count":
            # Sub-menu for mute-on-warn count
            keyboard = [
                [InlineKeyboardButton("2 اخطار", callback_data="set_mute_warn_2"), InlineKeyboardButton("3 اخطار", callback_data="set_mute_warn_3")],
                [InlineKeyboardButton("4 اخطار", callback_data="set_mute_warn_4"), InlineKeyboardButton("5 اخطار", callback_data="set_mute_warn_5")],
                [InlineKeyboardButton("بازگشت ⬅️", callback_data="settings_back")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "تعداد اخطار لازم برای میوت شدن را انتخاب کنید:",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        
        elif data.startswith("set_mute_warn_"):
            count = int(data.split('_')[-1])
            await update_group_settings(session, chat_id, mute_on_warn_count=count)
            await query.edit_message_text(f"✅ تعداد اخطار قبل از میوت به {count} تغییر یافت.")
            await log_admin_action(session, chat_id, user_id, "set_mute_warn_count", reason=f"{count} warns")
            await refresh_settings_panel(query, context, session)

        elif data == "settings_ban_warn_count":
            # Sub-menu for ban-on-warn count
            keyboard = [
                [InlineKeyboardButton("3 اخطار", callback_data="set_ban_warn_3"), InlineKeyboardButton("5 اخطار", callback_data="set_ban_warn_5")],
                [InlineKeyboardButton("7 اخطار", callback_data="set_ban_warn_7"), InlineKeyboardButton("10 اخطار", callback_data="set_ban_warn_10")],
                [InlineKeyboardButton("بازگشت ⬅️", callback_data="settings_back")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "تعداد اخطار لازم برای بن شدن را انتخاب کنید:",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )

        elif data.startswith("set_ban_warn_"):
            count = int(data.split('_')[-1])
            await update_group_settings(session, chat_id, ban_on_warn_count=count)
            await query.edit_message_text(f"✅ تعداد اخطار قبل از بن به {count} تغییر یافت.")
            await log_admin_action(session, chat_id, user_id, "set_ban_warn_count", reason=f"{count} warns")
            await refresh_settings_panel(query, context, session)

        elif data == "settings_show_forbidden_words":
            # Display forbidden words
            forbidden_words = await get_forbidden_words(session, chat_id)
            if forbidden_words:
                words_list = "\n".join([f"- <code>{word}</code>" for word in forbidden_words])
                await query.edit_message_text(
                    f"<b>🔠 لیست کلمات ممنوعه در این گروه:</b>\n{words_list}\n\n"
                    "برای اضافه یا حذف کردن از دستورات /addfilter و /delfilter استفاده کنید.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت ⬅️", callback_data="settings_back")]])
                )
            else:
                await query.edit_message_text(
                    "ℹ️ لیست کلمات ممنوعه برای این گروه خالی است.\n\n"
                    "برای اضافه کردن از دستور /addfilter استفاده کنید.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("بازگشت ⬅️", callback_data="settings_back")]])
                )
        
        elif data == "settings_media_locks":
            # Sub-menu for media lock settings
            keyboard = [
                [InlineKeyboardButton(f"عکس: {'🔒' if db_group.lock_photos else '🔓'}", callback_data="toggle_lock_photos"),
                 InlineKeyboardButton(f"ویدئو: {'🔒' if db_group.lock_videos else '🔓'}", callback_data="toggle_lock_videos")],
                [InlineKeyboardButton(f"لینک: {'🔒' if db_group.lock_links else '🔓'}", callback_data="toggle_lock_links"),
                 InlineKeyboardButton(f"فوروارد: {'🔒' if db_group.lock_forwards else '🔓'}", callback_data="toggle_lock_forwards")],
                [InlineKeyboardButton(f"استیکر: {'🔒' if db_group.lock_stickers else '🔓'}", callback_data="toggle_lock_stickers"),
                 InlineKeyboardButton(f"گیف: {'🔒' if db_group.lock_gifs else '🔓'}", callback_data="toggle_lock_gifs")],
                [InlineKeyboardButton(f"ویس: {'🔒' if db_group.lock_voice else '🔓'}", callback_data="toggle_lock_voice"),
                 InlineKeyboardButton(f"سند: {'🔒' if db_group.lock_documents else '🔓'}", callback_data="toggle_lock_documents")],
                [InlineKeyboardButton(f"ویدئو نوت: {'🔒' if db_group.lock_videonotes else '🔓'}", callback_data="toggle_lock_videonotes"),
                 InlineKeyboardButton(f"نظرسنجی: {'🔒' if db_group.lock_polls else '🔓'}", callback_data="toggle_lock_polls")],
                [InlineKeyboardButton(f"بازی: {'🔒' if db_group.lock_games else '🔓'}", callback_data="toggle_lock_games")],
                [InlineKeyboardButton("بازگشت ⬅️", callback_data="settings_back")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "<b>🔒 تنظیمات قفل رسانه‌ها:</b>\n"
                "رسانه‌های قفل شده توسط ربات حذف خواهند شد.",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        
        elif data.startswith("toggle_lock_"):
            field_name = data[len("toggle_"):].replace("lock_", "lock_") # e.g., 'lock_photos'
            current_status = getattr(db_group, field_name, False)
            new_status = not current_status
            await update_group_settings(session, chat_id, **{field_name: new_status})
            await query.edit_message_text(f"✅ {field_name.replace('lock_', '').replace('_', ' ').capitalize()}: {'قفل شد' if new_status else 'باز شد'}!")
            await log_admin_action(session, chat_id, user_id, "toggle_media_lock", reason=f"{field_name} set to {new_status}")
            await refresh_media_locks_panel(query, context, session) # Refresh media lock sub-menu
        
        elif data == "settings_toggle_restrict_new_members":
            db_group.restrict_new_members = not db_group.restrict_new_members
            await update_group_settings(session, chat_id, restrict_new_members=db_group.restrict_new_members)
            await query.edit_message_text(f"✅ محدودیت عضو جدید: {'فعال شد' if db_group.restrict_new_members else 'غیرفعال شد'}!")
            await log_admin_action(session, chat_id, user_id, "toggle_restrict_new_members", reason=f"Set to {db_group.restrict_new_members}")
            await refresh_settings_panel(query, context, session)

        elif data == "settings_restrict_duration":
            # Sub-menu for new member restriction duration
            keyboard = [
                [InlineKeyboardButton("1 دقیقه", callback_data="set_restrict_duration_1"), InlineKeyboardButton("5 دقیقه", callback_data="set_restrict_duration_5")],
                [InlineKeyboardButton("10 دقیقه", callback_data="set_restrict_duration_10"), InlineKeyboardButton("30 دقیقه", callback_data="set_restrict_duration_30")],
                [InlineKeyboardButton("بازگشت ⬅️", callback_data="settings_back")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(
                "مدت زمان محدودیت برای اعضای جدید را انتخاب کنید (فقط متن می‌توانند بفرستند):",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )

        elif data.startswith("set_restrict_duration_"):
            duration = int(data.split('_')[-1])
            await update_group_settings(session, chat_id, restrict_duration_minutes=duration)
            await query.edit_message_text(f"✅ مدت زمان محدودیت عضو جدید به {duration} دقیقه تغییر یافت.")
            await log_admin_action(session, chat_id, user_id, "set_restrict_duration", reason=f"{duration} minutes")
            await refresh_settings_panel(query, context, session)

        elif data == "settings_back":
            # Go back to main settings panel (refresh it)
            await refresh_settings_panel(query, context, session)

        elif data == "settings_close":
            await query.edit_message_text("❌ پنل تنظیمات بسته شد.")
    
    except BadRequest as e:
        if "Message is not modified" in str(e):
            logger.debug(f"Callback query for chat {chat_id}, user {user_id}: Message not modified, no need to update.")
        else:
            logger.error(f"BadRequest in settings_callback_handler for chat {chat_id}: {e}")
            await query.edit_message_text("❗️ خطایی در به‌روزرسانی پیام رخ داد.")
    except SQLAlchemyError as e:
        logger.error(f"Database error in settings_callback_handler for chat {chat_id}: {e}")
        await query.edit_message_text("❗️ خطایی در ارتباط با دیتابیس رخ داد. لطفاً بعداً امتحان کنید.")
    finally:
        session.close()

async def refresh_settings_panel(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, session) -> None:
    """Helper function to refresh the main settings panel message."""
    chat_id = query.message.chat_id
    db_group = session.query(Group).filter_by(id=chat_id).first()
    if not db_group:
        return # Should not happen if called after initial checks

    keyboard = [
        [
            InlineKeyboardButton(
                f"👋 خوش‌آمدگویی: {'✅ فعال' if db_group.welcome_enabled else '❌ غیرفعال'}",
                callback_data="settings_toggle_welcome"
            ),
            InlineKeyboardButton(
                f"📜 قوانین: {'✅ فعال' if db_group.rules_enabled else '❌ غیرفعال'}",
                callback_data="settings_toggle_rules"
            )
        ],
        [
            InlineKeyboardButton(
                f"🚫 ضد فلود: {'✅ فعال' if db_group.anti_flood_enabled else '❌ غیرفعال'}",
                callback_data="settings_toggle_anti_flood"
            ),
            InlineKeyboardButton(
                "⚙️ تنظیمات ضد فلود",
                callback_data="settings_anti_flood_options"
            )
        ],
        [
            InlineKeyboardButton(
                f"⚠️ اخطار تا میوت ({db_group.mute_on_warn_count})",
                callback_data="settings_mute_warn_count"
            ),
            InlineKeyboardButton(
                f"🚨 اخطار تا بن ({db_group.ban_on_warn_count})",
                callback_data="settings_ban_warn_count"
            )
        ],
        [
            InlineKeyboardButton("🔠 نمایش کلمات ممنوعه", callback_data="settings_show_forbidden_words")
        ],
        [
            InlineKeyboardButton("🔒 قفل رسانه‌ها", callback_data="settings_media_locks")
        ],
        [
            InlineKeyboardButton(f"👶 محدودیت عضو جدید: {'✅ فعال' if db_group.restrict_new_members else '❌ غیرفعال'}",
                               callback_data="settings_toggle_restrict_new_members"),
            InlineKeyboardButton("⏱️ مدت محدودیت عضو", callback_data="settings_restrict_duration")
        ],
        [
            InlineKeyboardButton("❌ بستن پنل", callback_data="settings_close")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    settings_text = (
        f"<b>تنظیمات گروه {db_group.title}:</b>\n\n"
        f"  • 👋 پیام خوش‌آمدگویی: {'✅ فعال' if db_group.welcome_enabled else '❌ غیرفعال'}\n"
        f"  • 📜 پیام قوانین: {'✅ فعال' if db_group.rules_enabled else '❌ غیرفعال'}\n"
        f"  • 🚫 ضد فلود: {'✅ فعال' if db_group.anti_flood_enabled else '❌ غیرفعال'} "
        f"({db_group.anti_flood_limit} پیام در {db_group.anti_flood_time} ثانیه)\n"
        f"  • ⚠️ تعداد اخطار تا میوت: {db_group.mute_on_warn_count}\n"
        f"  • 🚨 تعداد اخطار تا بن: {db_group.ban_on_warn_count}\n"
        f"  • 👶 محدودیت عضو جدید: {'✅ فعال' if db_group.restrict_new_members else '❌ غیرفعال'} ({db_group.restrict_duration_minutes} دقیقه)\n"
        "\n"
        "برای تغییر تنظیمات از دکمه‌های زیر استفاده کنید."
    )
    await query.edit_message_text(settings_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def refresh_media_locks_panel(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE, session) -> None:
    """Helper function to refresh the media locks sub-panel message."""
    chat_id = query.message.chat_id
    db_group = session.query(Group).filter_by(id=chat_id).first()
    if not db_group:
        return

    keyboard = [
        [InlineKeyboardButton(f"عکس: {'🔒' if db_group.lock_photos else '🔓'}", callback_data="toggle_lock_photos"),
            InlineKeyboardButton(f"ویدئو: {'🔒' if db_group.lock_videos else '🔓'}", callback_data="toggle_lock_videos")],
        [InlineKeyboardButton(f"لینک: {'🔒' if db_group.lock_links else '🔓'}", callback_data="toggle_lock_links"),
            InlineKeyboardButton(f"فوروارد: {'🔒' if db_group.lock_forwards else '🔓'}", callback_data="toggle_lock_forwards")],
        [InlineKeyboardButton(f"استیکر: {'🔒' if db_group.lock_stickers else '🔓'}", callback_data="toggle_lock_stickers"),
            InlineKeyboardButton(f"گیف: {'🔒' if db_group.lock_gifs else '🔓'}", callback_data="toggle_lock_gifs")],
        [InlineKeyboardButton(f"ویس: {'🔒' if db_group.lock_voice else '🔓'}", callback_data="toggle_lock_voice"),
            InlineKeyboardButton(f"سند: {'🔒' if db_group.lock_documents else '🔓'}", callback_data="toggle_lock_documents")],
        [InlineKeyboardButton(f"ویدئو نوت: {'🔒' if db_group.lock_videonotes else '🔓'}", callback_data="toggle_lock_videonotes"),
            InlineKeyboardButton(f"نظرسنجی: {'🔒' if db_group.lock_polls else '🔓'}", callback_data="toggle_lock_polls")],
        [InlineKeyboardButton(f"بازی: {'🔒' if db_group.lock_games else '🔓'}", callback_data="toggle_lock_games")],
        [InlineKeyboardButton("بازگشت ⬅️", callback_data="settings_back")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        "<b>🔒 تنظیمات قفل رسانه‌ها:</b>\n"
        "رسانه‌های قفل شده توسط ربات حذف خواهند شد.",
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )


# --- [ 6. Message Handlers (General Message Processing) ] ---
# These functions process different types of messages, including new member joins,
# left members, and all other text/media messages for moderation.

async def handle_new_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles new members joining the group. Sends a welcome message if enabled,
    registers the user and group in the database, and applies new member restrictions.
    """
    if not update.message or not update.message.new_chat_members or not update.effective_chat:
        return

    chat_id = update.effective_chat.id
    chat_title = update.effective_chat.title
    
    session = get_session()
    try:
        db_group = await get_or_create_group(session, chat_id, chat_title)

        for member in update.message.new_chat_members:
            if member.id == context.bot.id:
                # Bot itself was added to the group
                await update.message.reply_text(
                    "🎉 سلام! ممنون که من رو به گروهتون اضافه کردید.\n"
                    "لطفاً برای اینکه بتونم وظایف مدیریتی‌ام رو انجام بدم، من رو ادمین کامل کنید.\n"
                    "برای شروع، از دستور /start استفاده کنید."
                )
                continue # Don't process other members for bot's own join message

            user_data = member.to_dict()
            db_user = await get_or_create_user(session, user_data)
            await get_or_create_group_user(session, chat_id, member.id)

            # Send welcome message
            if db_group.welcome_enabled and db_group.welcome_message:
                welcome_text = db_group.welcome_message.format(
                    user=get_user_mention(member),
                    group=db_group.title
                )
                try:
                    await update.message.reply_text(welcome_text, parse_mode=ParseMode.HTML)
                except Exception as e:
                    logger.error(f"Error sending custom welcome message in chat {chat_id}: {e}")
                    # Fallback to a generic welcome if custom message fails
                    await update.message.reply_text(
                        f"👋 سلام {get_user_mention(member)} به گروه <b>{db_group.title}</b> خوش آمدید!",
                        parse_mode=ParseMode.HTML
                    )
            else:
                await update.message.reply_text(
                    f"👋 سلام {get_user_mention(member)} به گروه <b>{db_group.title}</b> خوش آمدید!",
                    parse_mode=ParseMode.HTML
                )
            
            # Apply new member restrictions if enabled
            if db_group.restrict_new_members and await is_bot_admin(chat_id, context):
                try:
                    # Restrict to only sending text messages for a defined duration
                    restrict_until = datetime.now() + timedelta(minutes=db_group.restrict_duration_minutes)
                    await context.bot.restrict_chat_member(
                        chat_id,
                        member.id,
                        permissions=constants.ChatPermissions(can_send_messages=True), # Only text messages allowed
                        until_date=restrict_until
                    )
                    await update.message.reply_text(
                        f"🔒 {get_user_mention(member)} شما به مدت <b>{db_group.restrict_duration_minutes} دقیقه</b> فقط می‌توانید پیام متنی ارسال کنید. لطفاً قوانین گروه را مطالعه کنید.",
                        parse_mode=ParseMode.HTML
                    )
                    logger.info(f"New member {member.id} restricted in chat {chat_id}.")
                except Exception as e:
                    logger.error(f"Failed to restrict new member {member.id} in chat {chat_id}: {e}")
                    # Bot might not have permissions, or user is already restricted/admin.
    except SQLAlchemyError as e:
        logger.error(f"Database error in handle_new_chat_members for chat {chat_id}: {e}")
        await update.message.reply_text("❗️ خطایی در ارتباط با دیتابیس رخ داد هنگام خوش‌آمدگویی.")
    except TelegramError as e:
        logger.error(f"Telegram API error in handle_new_chat_members for chat {chat_id}: {e}")
        # Log, but don't stop the bot.
    finally:
        session.close()


async def handle_left_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles members leaving the group. Optionally removes their group-specific data from the database.
    Sends a farewell message.
    """
    if not update.message or not update.message.left_chat_member or not update.effective_chat:
        return

    left_member = update.message.left_chat_member
    chat_id = update.effective_chat.id
    
    session = get_session()
    try:
        # If the bot itself leaves, remove all group data
        if left_member.id == context.bot.id:
            await update.message.reply_text("👋 متاسفم که باید گروه را ترک کنم. خداحافظ!")
            db_group = session.query(Group).filter_by(id=chat_id).first()
            if db_group:
                session.delete(db_group)
                session.query(GroupUser).filter_by(group_id=chat_id).delete()
                session.query(ForbiddenWord).filter_by(group_id=chat_id).delete()
                session.query(AdminLog).filter_by(group_id=chat_id).delete()
                session.commit()
                logger.info(f"Bot left group {chat_id}, deleted all associated group data.")
            return

        # Remove group_user data for the leaving member
        group_user = session.query(GroupUser).filter_by(group_id=chat_id, user_id=left_member.id).first()
        if group_user:
            session.delete(group_user)
            session.commit()
            logger.info(f"Removed GroupUser entry for {left_member.id} from group {chat_id} as they left.")
        
        await update.message.reply_text(
            f"👋 {get_user_mention(left_member)} گروه را ترک کرد. امیدواریم دوباره ببینیمش.",
            parse_mode=ParseMode.HTML
        )
    except SQLAlchemyError as e:
        logger.error(f"Database error in handle_left_chat_members for chat {chat_id}: {e}")
    finally:
        session.close()

async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    The main message handler for all incoming messages (text, media, etc.).
    This function orchestrates anti-spam, anti-flood, forbidden word filtering,
    media locking, and general user activity tracking.
    """
    if not update.message or not update.effective_chat or not update.effective_user:
        return
    
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    # Ignore messages from bot itself or admins (can be adjusted if admins should also be filtered)
    if user_id == context.bot.id or await is_user_admin_or_owner(chat_id, user_id, context):
        return

    session = get_session()
    try:
        db_group = await get_or_create_group(session, chat_id, update.effective_chat.title)
        db_user = await get_or_create_user(session, update.effective_user.to_dict())
        group_user = await get_or_create_group_user(session, chat_id, user_id)
        
        # --- 6.1. System Message Cleanup ---
        # Deletes default Telegram "user joined" or "user left" messages
        if update.message.new_chat_members or update.message.left_chat_member:
            try:
                await update.message.delete()
                logger.debug(f"Deleted system message for chat {chat_id}.")
                return # Stop processing, as this message is handled and deleted.
            except BadRequest as e:
                logger.warning(f"Could not delete system message in chat {chat_id}: {e}")
        
        # --- 6.2. Check for Muted User Status (Database-managed mutes) ---
        if group_user.is_muted:
            if group_user.mute_until and group_user.mute_until < datetime.utcnow():
                # Mute expired, unmute the user in Telegram and DB
                if await is_bot_admin(chat_id, context):
                    try:
                        await context.bot.restrict_chat_member(
                            chat_id,
                            user_id,
                            permissions=constants.ChatPermissions(
                                can_send_messages=True, can_send_media_messages=True,
                                can_send_polls=True, can_send_other_messages=True,
                                can_add_web_page_previews=True, can_change_info=False,
                                can_invite_users=True, can_pin_messages=False,
                                can_manage_topics=False
                            )
                        )
                        group_user.is_muted = False
                        group_user.mute_until = None
                        session.commit()
                        await update.message.reply_text(f"✅ {get_user_mention(db_user)} میوت شما به پایان رسید و آزاد شدید.", parse_mode=ParseMode.HTML)
                        logger.info(f"User {user_id} unmuted in group {chat_id} due to expired mute.")
                        await log_admin_action(session, chat_id, context.bot.id, "auto_unmute", user_id, "Mute expired")
                    except Exception as e:
                        logger.error(f"Failed to auto-unmute user {user_id} in group {chat_id} after mute expired: {e}")
                        # Even if Telegram API fails, update DB to reflect mute has expired
                        group_user.is_muted = False
                        group_user.mute_until = None
                        session.commit()
                else:
                    logger.warning(f"Bot is not admin in {chat_id}, cannot unmute {user_id} after mute expired.")
            else:
                # User is still muted, delete their message
                if await is_bot_admin(chat_id, context):
                    try:
                        await update.message.delete()
                        logger.info(f"Deleted message from muted user {user_id} in chat {chat_id}.")
                        return # Stop processing, message deleted
                    except BadRequest as e:
                        logger.warning(f"Could not delete message from muted user {user_id} in chat {chat_id}: {e}")
                    except Exception as e:
                        logger.error(f"Unexpected error deleting message from muted user {user_id}: {e}")
                else:
                    logger.warning(f"Bot is not admin in {chat_id}, cannot delete message from muted user {user_id}.")
                return # Stop processing to prevent further actions on a muted user's message

        # --- 6.3. Anti-Flood System (for text messages) ---
        # Note: A more robust anti-flood would track all message types separately.
        if db_group.anti_flood_enabled and update.message.text:
            current_time = datetime.utcnow()
            if group_user.last_message_time and \
               (current_time - group_user.last_message_time).total_seconds() < db_group.anti_flood_time:
                group_user.message_count_in_interval += 1
            else:
                group_user.message_count_in_interval = 1
            
            group_user.last_message_time = current_time
            session.commit() # Commit here to save flood data frequently

            if group_user.message_count_in_interval > db_group.anti_flood_limit:
                if await is_bot_admin(chat_id, context):
                    try:
                        await update.message.delete()
                        logger.info(f"Deleted message from user {user_id} in chat {chat_id} due to flood.")
                        # Warn the user for flooding
                        group_user.warns += 1
                        session.commit()
                        await context.bot.send_message(
                            chat_id,
                            f"⚠️ {get_user_mention(db_user)}! شما به دلیل فلود کردن یک اخطار دریافت کردید. "
                            f"تعداد اخطارهای فعلی: <b>{group_user.warns}</b>",
                            parse_mode=ParseMode.HTML
                        )
                        await log_admin_action(session, chat_id, context.bot.id, "auto_warn_flood", user_id, "Flooding detected")
                        
                        # Check for mute/ban after warn due to flood
                        if group_user.warns >= db_group.ban_on_warn_count:
                            await context.bot.ban_chat_member(chat_id, user_id)
                            session.delete(group_user) # Remove user's data after ban
                            session.commit()
                            await context.bot.send_message(
                                chat_id,
                                f"🚨 کاربر {get_user_mention(db_user)} به دلیل رسیدن به <b>{db_group.ban_on_warn_count}</b> اخطار، از گروه <b>بن شد!</b>",
                                parse_mode=ParseMode.HTML
                            )
                            await log_admin_action(session, chat_id, context.bot.id, "auto_ban_flood", user_id, f"Reached {db_group.ban_on_warn_count} warns from flooding")
                            logger.info(f"User {user_id} banned for flooding in group {chat_id}.")
                        elif group_user.warns >= db_group.mute_on_warn_count:
                            mute_duration = timedelta(minutes=60)
                            until_date = datetime.now() + mute_duration
                            await context.bot.restrict_chat_member(
                                chat_id,
                                user_id,
                                permissions=constants.ChatPermissions(can_send_messages=False),
                                until_date=until_date
                            )
                            group_user.is_muted = True
                            group_user.mute_until = until_date
                            session.commit()
                            await context.bot.send_message(
                                chat_id,
                                f"🔇 کاربر {get_user_mention(db_user)} به دلیل رسیدن به <b>{db_group.mute_on_warn_count}</b> اخطار، به مدت 60 دقیقه میوت شد.",
                                parse_mode=ParseMode.HTML
                            )
                            await log_admin_action(session, chat_id, context.bot.id, "auto_mute_flood", user_id, f"Reached {db_group.mute_on_warn_count} warns from flooding")
                            logger.info(f"User {user_id} muted for flooding in group {chat_id}.")
                        return # Stop processing, message deleted and action taken
                    except BadRequest as e:
                        logger.warning(f"Could not delete flood message from user {user_id} in chat {chat_id}: {e}")
                    except Exception as e:
                        logger.error(f"Unexpected error handling flood for user {user_id}: {e}")
                return # Stop processing if bot is not admin or message deleted

        # --- 6.4. Forbidden Words Filter (for text messages) ---
        if update.message.text:
            message_text_lower = update.message.text.lower()
            forbidden_words = await get_forbidden_words(session, chat_id)
            for word in forbidden_words:
                if word in message_text_lower:
                    if await is_bot_admin(chat_id, context):
                        try:
                            await update.message.delete()
                            logger.info(f"Deleted message from user {user_id} in chat {chat_id} due to forbidden word '{word}'.")
                            # Warn the user for forbidden word
                            group_user.warns += 1
                            session.commit()
                            await context.bot.send_message(
                                chat_id,
                                f"⚠️ {get_user_mention(db_user)}! شما به دلیل استفاده از کلمه ممنوعه (<code>{word}</code>) یک اخطار دریافت کردید. "
                                f"تعداد اخطارهای فعلی: <b>{group_user.warns}</b>",
                                parse_mode=ParseMode.HTML
                            )
                            await log_admin_action(session, chat_id, context.bot.id, "auto_warn_forbidden_word", user_id, f"Used forbidden word: {word}")

                            # Check for mute/ban after warn for forbidden word
                            if group_user.warns >= db_group.ban_on_warn_count:
                                await context.bot.ban_chat_member(chat_id, user_id)
                                session.delete(group_user) # Remove user's data after ban
                                session.commit()
                                await context.bot.send_message(
                                    chat_id,
                                    f"🚨 کاربر {get_user_mention(db_user)} به دلیل رسیدن به <b>{db_group.ban_on_warn_count}</b> اخطار، از گروه <b>بن شد!</b>",
                                    parse_mode=ParseMode.HTML
                                )
                                await log_admin_action(session, chat_id, context.bot.id, "auto_ban_forbidden_word", user_id, f"Reached {db_group.ban_on_warn_count} warns from forbidden words")
                                logger.info(f"User {user_id} banned for forbidden word in group {chat_id}.")
                            elif group_user.warns >= db_group.mute_on_warn_count:
                                mute_duration = timedelta(minutes=60)
                                until_date = datetime.now() + mute_duration
                                await context.bot.restrict_chat_member(
                                    chat_id,
                                    user_id,
                                    permissions=constants.ChatPermissions(can_send_messages=False),
                                    until_date=until_date
                                )
                                group_user.is_muted = True
                                group_user.mute_until = until_date
                                session.commit()
                                await context.bot.send_message(
                                    chat_id,
                                    f"🔇 کاربر {get_user_mention(db_user)} به دلیل رسیدن به <b>{db_group.mute_on_warn_count}</b> اخطار، به مدت 60 دقیقه میوت شد.",
                                    parse_mode=ParseMode.HTML
                                )
                                await log_admin_action(session, chat_id, context.bot.id, "auto_mute_forbidden_word", user_id, f"Reached {db_group.mute_on_warn_count} warns from forbidden words")
                                logger.info(f"User {user_id} muted for forbidden word in group {chat_id}.")
                            return # Stop processing, message deleted and action taken
                        except BadRequest as e:
                            logger.warning(f"Could not delete message with forbidden word from user {user_id} in chat {chat_id}: {e}")
                        except Exception as e:
                            logger.error(f"Unexpected error handling forbidden word for user {user_id}: {e}")
                    return # Stop processing if bot is not admin or message deleted
        
        # --- 6.5. Media Locks ---
        if await is_bot_admin(chat_id, context):
            message_deleted = False
            # Check for specific media types based on group settings
            if db_group.lock_photos and update.message.photo:
                await update.message.delete()
                message_deleted = True
                await log_admin_action(session, chat_id, context.bot.id, "delete_locked_media", user_id, "Photo locked")
            elif db_group.lock_videos and update.message.video:
                await update.message.delete()
                message_deleted = True
                await log_admin_action(session, chat_id, context.bot.id, "delete_locked_media", user_id, "Video locked")
            elif db_group.lock_links and (update.message.entities or update.message.caption_entities) and any(e.url for e in (update.message.entities or []) + (update.message.caption_entities or []) if e.url):
                await update.message.delete()
                message_deleted = True
                await log_admin_action(session, chat_id, context.bot.id, "delete_locked_media", user_id, "Link locked")
            elif db_group.lock_forwards and update.message.forward_from or update.message.forward_from_chat:
                await update.message.delete()
                message_deleted = True
                await log_admin_action(session, chat_id, context.bot.id, "delete_locked_media", user_id, "Forward locked")
            elif db_group.lock_stickers and update.message.sticker:
                await update.message.delete()
                message_deleted = True
                await log_admin_action(session, chat_id, context.bot.id, "delete_locked_media", user_id, "Sticker locked")
            elif db_group.lock_gifs and update.message.animation: # GIFs are animations in Telegram
                await update.message.delete()
                message_deleted = True
                await log_admin_action(session, chat_id, context.bot.id, "delete_locked_media", user_id, "GIF locked")
            elif db_group.lock_voice and update.message.voice:
                await update.message.delete()
                message_deleted = True
                await log_admin_action(session, chat_id, context.bot.id, "delete_locked_media", user_id, "Voice locked")
            elif db_group.lock_documents and update.message.document:
                await update.message.delete()
                message_deleted = True
                await log_admin_action(session, chat_id, context.bot.id, "delete_locked_media", user_id, "Document locked")
            elif db_group.lock_videonotes and update.message.video_note:
                await update.message.delete()
                message_deleted = True
                await log_admin_action(session, chat_id, context.bot.id, "delete_locked_media", user_id, "Video note locked")
            elif db_group.lock_polls and update.message.poll:
                await update.message.delete()
                message_deleted = True
                await log_admin_action(session, chat_id, context.bot.id, "delete_locked_media", user_id, "Poll locked")
            elif db_group.lock_games and update.message.game:
                await update.message.delete()
                message_deleted = True
                await log_admin_action(session, chat_id, context.bot.id, "delete_locked_media", user_id, "Game locked")
            
            if message_deleted:
                # Optionally warn user if their message was deleted due to a lock
                # (Be careful not to spam if a user sends many locked items)
                # For now, just log and delete.
                return # Stop processing, message deleted
        
        # --- 6.6. Update User Last Activity (if message was not deleted) ---
        db_user.last_activity = datetime.utcnow()
        session.commit()

    except SQLAlchemyError as e:
        logger.error(f"Database error in handle_all_messages for chat {chat_id}: {e}")
        # In case of DB error, don't stop message processing, just log the error.
    except BadRequest as e:
        logger.warning(f"Telegram API error (BadRequest) in handle_all_messages for chat {chat_id}, user {user_id}: {e}")
    except Exception as e:
        logger.critical(f"Unhandled exception in handle_all_messages for chat {chat_id}, user {user_id}: {e}", exc_info=True)
    finally:
        session.close()


# --- [ 7. Background Job Functions ] ---
# Functions for JobQueue to run periodically.

async def check_expired_mutes(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Periodically checks the database for expired mutes and unmutes users in Telegram.
    This job runs in the background using the JobQueue.
    """
    logger.debug("Running check_expired_mutes job...")
    session = get_session()
    try:
        now = datetime.utcnow()
        # Find GroupUser entries where is_muted is True and mute_until has passed
        expired_mutes = session.query(GroupUser).filter(
            and_(GroupUser.is_muted == True, GroupUser.mute_until < now)
        ).all()

        for group_user in expired_mutes:
            chat_id = group_user.group_id
            user_id = group_user.user_id
            
            if not await is_bot_admin(chat_id, context):
                logger.warning(f"Bot is not admin in chat {chat_id}, cannot unmute user {user_id} automatically.")
                continue

            try:
                # Attempt to unmute the user in Telegram
                await context.bot.restrict_chat_member(
                    chat_id,
                    user_id,
                    permissions=constants.ChatPermissions(
                        can_send_messages=True, can_send_media_messages=True,
                        can_send_polls=True, can_send_other_messages=True,
                        can_add_web_page_previews=True, can_change_info=False,
                        can_invite_users=True, can_pin_messages=False,
                        can_manage_topics=False
                    )
                )
                # Update database status
                group_user.is_muted = False
                group_user.mute_until = None
                session.commit()
                logger.info(f"Automatically unmuted user {user_id} in group {chat_id}.")
                await log_admin_action(session, chat_id, context.bot.id, "auto_unmute_expired", user_id, "Mute expired")
            except BadRequest as e:
                # User might have left or already been unmuted by an admin
                logger.warning(f"Could not unmute user {user_id} in chat {chat_id} (BadRequest): {e}")
                group_user.is_muted = False # Still update DB to prevent repeated attempts
                group_user.mute_until = None
                session.commit()
            except TelegramError as e:
                logger.error(f"Telegram error unmuting user {user_id} in chat {chat_id}: {e}")
            except SQLAlchemyError as e:
                session.rollback()
                logger.error(f"Database error during mute expiration check for {chat_id}/{user_id}: {e}")
        session.commit() # Commit any remaining changes (e.g., if a user left but mute status updated)
    except SQLAlchemyError as e:
        logger.error(f"Main database error in check_expired_mutes job: {e}")
    finally:
        session.close()

# --- [ 8. Error Handler ] ---

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Logs all errors originating from the Telegram Bot API and sends a detailed traceback
    to the bot's configured owner for debugging.
    """
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

    # Prepare traceback string for sending to owner
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = "".join(tb_list)
    
    # Construct error message for the owner
    error_message_for_owner = (
        f"❗️ <b>یک خطا در ربات رخ داد!</b> ❗️\n\n"
        f"<b>Update:</b> <code>{update}</code>\n"
        f"<b>Error:</b> <code>{context.error}</code>\n\n"
        f"<b>Traceback:</b>\n"
        f"<pre>{tb_string}</pre>"
    )
    
    # Send error details to the bot owner
    try:
        # Telegram messages have a length limit (4096 characters).
        # Split the message if it's too long.
        if len(error_message_for_owner) > constants.MAX_MESSAGE_LENGTH:
            for x in range(0, len(error_message_for_owner), constants.MAX_MESSAGE_LENGTH):
                await context.bot.send_message(
                    chat_id=OWNER_USER_ID,
                    text=error_message_for_owner[x:x+constants.MAX_MESSAGE_LENGTH],
                    parse_mode=ParseMode.HTML
                )
        else:
            await context.bot.send_message(
                chat_id=OWNER_USER_ID,
                text=error_message_for_owner,
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logger.error(f"Failed to send error traceback to owner {OWNER_USER_ID}: {e}")
        # Log to console as a fallback if sending to owner fails
        print(error_message_for_owner)

# --- [ 9. Main Function to Run the Bot ] ---

def main() -> None:
    """
    The main function that initializes and runs the Telegram bot.
    Sets up the Application, registers all command and message handlers,
    and starts the polling mechanism.
    """
    logger.info("Starting bot application...")
    application = Application.builder().token(BOT_TOKEN).build()

    # Get the JobQueue instance from the application
    job_queue: JobQueue = application.job_queue

    # --- Register Command Handlers ---
    logger.info("Registering command handlers...")
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("rules", rules_command))
    application.add_handler(CommandHandler("id", id_command))
    application.add_handler(CommandHandler("info", info_command))
    application.add_handler(CommandHandler("groupinfo", group_info_command))

    # Admin commands (require specific permissions)
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("setwelcome", set_welcome_message))
    application.add_handler(CommandHandler("delwelcome", del_welcome_message))
    application.add_handler(CommandHandler("setrules", set_rules_message))
    application.add_handler(CommandHandler("delrules", del_rules_message))
    application.add_handler(CommandHandler("addfilter", add_filter_command))
    application.add_handler(CommandHandler("delfilter", del_filter_command))
    application.add_handler(CommandHandler("filters", filters_command))
    application.add_handler(CommandHandler("warn", warn_command))
    application.add_handler(CommandHandler("unwarn", unwarn_command))
    application.add_handler(CommandHandler("warns", warns_command))
    application.add_handler(CommandHandler("mute", mute_command))
    application.add_handler(CommandHandler("unmute", unmute_command))
    application.add_handler(CommandHandler("ban", ban_command))
    application.add_handler(CommandHandler("tempban", tempban_command))
    application.add_handler(CommandHandler("kick", kick_command))
    application.add_handler(CommandHandler("purge", purge_command))
    application.add_handler(CommandHandler("del", delete_message_command))
    application.add_handler(CommandHandler("pin", pin_message_command))
    application.add_handler(CommandHandler("unpin", unpin_message_command))
    application.add_handler(CommandHandler("lock", lock_media_command))
    application.add_handler(CommandHandler("unlock", unlock_media_command))
    application.add_handler(CommandHandler("reputation", reputation_command))
    application.add_handler(CommandHandler("checkrep", check_reputation_command))

    # Bot owner commands (strictly for the developer)
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("listgroups", list_groups_command))
    application.add_handler(CommandHandler("leavegroup", leave_group_command))

    # --- Register Message Handlers ---
    logger.info("Registering message handlers...")
    # New members joining the chat
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_chat_members))
    # Members leaving the chat
    application.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, handle_left_chat_members))
    # All other messages (text, photo, video, etc. that are not commands)
    # This handler must be registered after all command handlers to avoid intercepting commands.
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_all_messages))

    # --- Register Callback Query Handler ---
    logger.info("Registering callback query handler...")
    application.add_handler(CallbackQueryHandler(settings_callback_handler))

    # --- Register Error Handler ---
    logger.info("Registering error handler...")
    application.add_error_handler(error_handler)

    # --- Schedule Background Jobs ---
    logger.info("Scheduling background jobs...")
    # Schedule the mute expiration check to run every 5 minutes
    job_queue.run_repeating(check_expired_mutes, interval=timedelta(minutes=5), first=0)

    # --- Start the Bot ---
    logger.info("Bot started successfully. Polling for updates...")
    # Start polling for updates from Telegram. allowed_updates=Update.ALL_TYPES
    # ensures that the bot receives all types of updates.
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

```
