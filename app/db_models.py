from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    ForeignKey,
    Text,
    JSON,
    UniqueConstraint,
    Index
)
from sqlalchemy.orm import relationship
from app.database import Base

def utc_now():
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    is_admin = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    # Relationships
    settings = relationship("UserSettings", back_populates="user", uselist=False, cascade="all, delete-orphan")
    crawled_news = relationship("UserCrawledNews", back_populates="user", cascade="all, delete-orphan")
    selected_stories = relationship("UserSelectedStory", back_populates="user", cascade="all, delete-orphan")
    saved_stories = relationship("UserSavedStory", back_populates="user", cascade="all, delete-orphan")


class UserSettings(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    source_languages = Column(JSON, default=lambda: ["English", "Urdu", "Arabic"], nullable=False)
    target_languages = Column(JSON, default=lambda: ["Urdu", "English"], nullable=False)
    sources = Column(JSON, default=list, nullable=False)
    keywords = Column(JSON, default=list, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    user = relationship("User", back_populates="settings")


class UserCrawledNews(Base):
    __tablename__ = "user_crawled_news"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    headline = Column(Text, nullable=False)
    url = Column(Text, nullable=False)
    order_index = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    user = relationship("User", back_populates="crawled_news")

    __table_args__ = (
        Index("idx_crawled_user_order", "user_id", "order_index"),
    )


class UserSelectedStory(Base):
    __tablename__ = "user_selected_stories"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    order_index = Column(Integer, default=0, nullable=False)
    story_data = Column(JSON, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    user = relationship("User", back_populates="selected_stories")

    __table_args__ = (
        Index("idx_selected_user_order", "user_id", "order_index"),
    )


class UserSavedStory(Base):
    __tablename__ = "user_saved_stories"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    heading = Column(Text, nullable=False)
    url = Column(Text, nullable=True)
    image = Column(Text, nullable=True)
    sitename = Column(String(255), nullable=True)
    source_language = Column(String(32), nullable=True)
    story_data = Column(JSON, nullable=False)
    saved_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    user = relationship("User", back_populates="saved_stories")

    __table_args__ = (
        Index("idx_saved_user_date", "user_id", "saved_at"),
    )
