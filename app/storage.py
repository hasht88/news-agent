import json
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from sqlalchemy import select, delete, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import AgentSettings
from app.db_models import (
    User,
    UserSettings,
    UserCrawledNews,
    UserSelectedStory,
    UserSavedStory
)

DEFAULT_SOURCE_LANGUAGES: List[str] = ["English", "Urdu", "Arabic"]
DEFAULT_TARGET_LANGUAGES: List[str] = ["Urdu", "English"]
DEFAULT_URLS: List[str] = []
DEFAULT_KEYWORDS: List[str] = []

def get_default_settings() -> AgentSettings:
    return AgentSettings(
        source_languages=list(DEFAULT_SOURCE_LANGUAGES),
        target_languages=list(DEFAULT_TARGET_LANGUAGES),
        sources=list(DEFAULT_URLS),
        keywords=list(DEFAULT_KEYWORDS)
    )

# -------------------------------------------------------------
# User Settings DB Services
# -------------------------------------------------------------

async def get_user_settings(db: AsyncSession, user_id: int) -> AgentSettings:
    result = await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
    record = result.scalar_one_or_none()
    if not record:
        record = UserSettings(
            user_id=user_id,
            source_languages=list(DEFAULT_SOURCE_LANGUAGES),
            target_languages=list(DEFAULT_TARGET_LANGUAGES),
            sources=list(DEFAULT_URLS),
            keywords=list(DEFAULT_KEYWORDS)
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)

    return AgentSettings(
        source_languages=record.source_languages or list(DEFAULT_SOURCE_LANGUAGES),
        target_languages=record.target_languages or list(DEFAULT_TARGET_LANGUAGES),
        sources=record.sources or [],
        keywords=record.keywords or []
    )

async def save_user_settings(db: AsyncSession, user_id: int, settings: AgentSettings) -> bool:
    try:
        result = await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
        record = result.scalar_one_or_none()
        if not record:
            record = UserSettings(user_id=user_id)
            db.add(record)
        
        record.source_languages = settings.source_languages
        record.target_languages = settings.target_languages
        record.sources = settings.sources
        record.keywords = settings.keywords
        record.updated_at = datetime.now(timezone.utc)
        await db.commit()
        return True
    except Exception as e:
        print(f"Error saving user settings to db: {e}")
        await db.rollback()
        return False

# -------------------------------------------------------------
# Crawled News DB Services
# -------------------------------------------------------------

async def get_user_crawled_news(db: AsyncSession, user_id: int) -> List[Dict[str, Any]]:
    result = await db.execute(
        select(UserCrawledNews)
        .where(UserCrawledNews.user_id == user_id)
        .order_by(UserCrawledNews.order_index.asc(), UserCrawledNews.id.asc())
    )
    records = result.scalars().all()
    return [{"id": r.order_index, "headline": r.headline, "url": r.url} for r in records]

async def save_user_crawled_news(db: AsyncSession, user_id: int, items: List[Dict[str, Any]]) -> bool:
    try:
        await db.execute(delete(UserCrawledNews).where(UserCrawledNews.user_id == user_id))
        for idx, it in enumerate(items):
            entry = UserCrawledNews(
                user_id=user_id,
                order_index=idx,
                headline=it.get("headline", ""),
                url=it.get("url", "")
            )
            db.add(entry)
        await db.commit()
        return True
    except Exception as e:
        print(f"Error saving crawled news to db: {e}")
        await db.rollback()
        return False

# -------------------------------------------------------------
# Selected News / Stories DB Services
# -------------------------------------------------------------

async def get_user_selected_stories(db: AsyncSession, user_id: int) -> List[Dict[str, Any]]:
    result = await db.execute(
        select(UserSelectedStory)
        .where(UserSelectedStory.user_id == user_id)
        .order_by(UserSelectedStory.order_index.asc(), UserSelectedStory.id.asc())
    )
    records = result.scalars().all()
    return [r.story_data for r in records]

async def save_user_selected_stories(db: AsyncSession, user_id: int, stories: List[Dict[str, Any]]) -> bool:
    try:
        await db.execute(delete(UserSelectedStory).where(UserSelectedStory.user_id == user_id))
        for idx, story in enumerate(stories):
            entry = UserSelectedStory(
                user_id=user_id,
                order_index=idx,
                story_data=story,
                updated_at=datetime.now(timezone.utc)
            )
            db.add(entry)
        await db.commit()
        return True
    except Exception as e:
        print(f"Error saving selected stories to db: {e}")
        await db.rollback()
        return False

# -------------------------------------------------------------
# Saved Stories DB Services
# -------------------------------------------------------------

async def get_user_saved_stories(db: AsyncSession, user_id: int) -> List[Dict[str, Any]]:
    result = await db.execute(
        select(UserSavedStory)
        .where(UserSavedStory.user_id == user_id)
        .order_by(desc(UserSavedStory.saved_at), desc(UserSavedStory.id))
    )
    records = result.scalars().all()
    return [r.story_data for r in records]

async def save_user_saved_story(db: AsyncSession, user_id: int, story_dict: Dict[str, Any]) -> Dict[str, Any]:
    story_copy = dict(story_dict)
    story_copy["saved_at"] = datetime.now(timezone.utc).isoformat()

    # Backfill image from blocks if missing
    if not (story_copy.get("image") or "").strip() and story_copy.get("blocks"):
        for b in story_copy["blocks"]:
            if isinstance(b, dict) and b.get("type") == "image" and (b.get("url") or "").strip():
                story_copy["image"] = b["url"].strip()
                break

    story_url = (story_copy.get("url") or "").strip()
    story_heading = (story_copy.get("heading") or story_copy.get("headline") or "").strip()
    image = (story_copy.get("image") or "").strip()
    sitename = (story_copy.get("sitename") or "").strip()
    source_language = (story_copy.get("source_language") or "").strip()

    # Find existing story by user and (url or heading)
    result = await db.execute(
        select(UserSavedStory).where(UserSavedStory.user_id == user_id)
    )
    existing_records = result.scalars().all()
    matched_record: Optional[UserSavedStory] = None

    for rec in existing_records:
        rec_url = (rec.url or "").strip()
        rec_heading = (rec.heading or "").strip()
        if story_url and rec_url and story_url == rec_url:
            matched_record = rec
            break
        elif not story_url and story_heading and rec_heading and story_heading == rec_heading:
            matched_record = rec
            break

    if matched_record:
        matched_record.heading = story_heading
        matched_record.url = story_url
        matched_record.image = image
        matched_record.sitename = sitename
        matched_record.source_language = source_language
        matched_record.story_data = story_copy
        matched_record.saved_at = datetime.now(timezone.utc)
    else:
        new_entry = UserSavedStory(
            user_id=user_id,
            heading=story_heading,
            url=story_url,
            image=image,
            sitename=sitename,
            source_language=source_language,
            story_data=story_copy,
            saved_at=datetime.now(timezone.utc)
        )
        db.add(new_entry)

    await db.commit()
    return story_copy

# -------------------------------------------------------------
# Migration from legacy JSON files to initial user
# -------------------------------------------------------------

async def migrate_legacy_data_to_user(db: AsyncSession, user_id: int):
    """Import existing data from data/ folder into the user's DB account if empty."""
    data_dir = Path(__file__).resolve().parent.parent / "data"

    # 1. Migrate settings
    existing_settings = await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
    if not existing_settings.scalar_one_or_none():
        settings_file = data_dir / "settings.json"
        if settings_file.exists():
            try:
                with open(settings_file, "r", encoding="utf-8") as f:
                    s_data = json.load(f)
                new_s = UserSettings(
                    user_id=user_id,
                    source_languages=s_data.get("source_languages", list(DEFAULT_SOURCE_LANGUAGES)),
                    target_languages=s_data.get("target_languages", list(DEFAULT_TARGET_LANGUAGES)),
                    sources=s_data.get("sources", []),
                    keywords=s_data.get("keywords", [])
                )
                db.add(new_s)
                await db.commit()
            except Exception as e:
                print(f"Notice: failed migrating legacy settings: {e}")

    # 2. Migrate saved stories
    existing_saved = await db.execute(select(UserSavedStory).where(UserSavedStory.user_id == user_id))
    if not existing_saved.scalars().first():
        saved_file = data_dir / "saved_news.json"
        if saved_file.exists():
            try:
                with open(saved_file, "r", encoding="utf-8") as f:
                    saved_items = json.load(f)
                for item in saved_items:
                    await save_user_saved_story(db, user_id, item)
            except Exception as e:
                print(f"Notice: failed migrating legacy saved stories: {e}")

    # 3. Migrate selected news
    existing_selected = await db.execute(select(UserSelectedStory).where(UserSelectedStory.user_id == user_id))
    if not existing_selected.scalars().first():
        selected_file = data_dir / "selected_news.json"
        if selected_file.exists():
            try:
                with open(selected_file, "r", encoding="utf-8") as f:
                    selected_items = json.load(f)
                if selected_items:
                    await save_user_selected_stories(db, user_id, selected_items)
            except Exception as e:
                print(f"Notice: failed migrating legacy selected stories: {e}")

    # 4. Migrate crawled links
    existing_links = await db.execute(select(UserCrawledNews).where(UserCrawledNews.user_id == user_id))
    if not existing_links.scalars().first():
        links_file = data_dir / "links.json"
        if links_file.exists():
            try:
                with open(links_file, "r", encoding="utf-8") as f:
                    links_items = json.load(f)
                if links_items:
                    await save_user_crawled_news(db, user_id, links_items)
            except Exception as e:
                print(f"Notice: failed migrating legacy crawled links: {e}")
