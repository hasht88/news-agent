import re, os, json
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Optional, List, Dict, Any
import httpx
from bs4 import BeautifulSoup, SoupStrainer
from fastapi import FastAPI, Request, HTTPException, Form, status, BackgroundTasks, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from urllib.parse import urljoin
from curl_cffi import requests
from collections import defaultdict
from lingua import Language, LanguageDetectorBuilder
from trafilatura import extract, extract_metadata
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, init_db, AsyncSessionLocal
from app.db_models import User, UserSettings, UserSelectedStory, UserSavedStory, UserCrawledNews
from app.auth import (
    hash_password,
    verify_password,
    create_session_token,
    get_current_user,
    get_current_user_optional,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE
)
from app.storage import (
    get_default_settings,
    get_user_settings,
    save_user_settings,
    get_user_crawled_news,
    save_user_crawled_news,
    get_user_selected_stories,
    save_user_selected_stories,
    get_user_saved_stories,
    save_user_saved_story,
    migrate_legacy_data_to_user
)
from app.models import (
    AgentSettings,
    StoryUpdateRequest,
    StoryItem,
    StoryFetchRequest,
    StoryTransformRequest,
    StoryBatchSaveRequest,
    ALLOWED_AI_MODELS,
    DEFAULT_AI_MODEL,
)

BASE_DIR = Path(__file__).resolve().parent.parent
INVITE_CODE_SECRET = os.environ.get("INVITE_CODE", "newsroom2026")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables on startup
    await init_db()
    yield

app = FastAPI(
    title="News Curation Agent",
    description="Configure Scope URLs, Track Keywords, Curate News and Compose Stories",
    version="2.0.0",
    lifespan=lifespan
)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

custom_headers = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36',
    'Accept-Language': 'da, en-gb, en'
}

dawn_headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

def detect_lang(items: list[dict], source_languages: list[str] = None):
    if not items:
        return items
    source_languages = source_languages or ["English", "Urdu", "Arabic"]
    languages = []
    for lang in source_languages:
        lang_enum = getattr(Language, str(lang).upper(), None)
        if lang_enum and lang_enum not in languages:
            languages.append(lang_enum)
    if not languages:
        languages = [Language.ENGLISH, Language.URDU, Language.ARABIC]

    try:
        detector = LanguageDetectorBuilder.from_languages(*languages).build()
    except Exception as e:
        print(f"Error building LanguageDetector: {e}")
        detector = None

    items_lang = []
    for item in items:
        text = (item.get("heading") or item.get("headline") or "").strip()
        detected_code = ""
        if detector and text:
            try:
                detected = detector.detect_language_of(text)
                if detected and hasattr(detected, "iso_code_639_1"):
                    detected_code = detected.iso_code_639_1.name.lower()
            except Exception as e:
                print(f"Language detection failed for text '{text[:30]}': {e}")
        item["source_language"] = detected_code
        items_lang.append(item)
    return items_lang

def merge_by_url(data):
    groups = defaultdict(list)
    for item in data:
        groups[item['url']].append(item['headline'])

    merged = []
    for url, headlines in groups.items():
        seen = []
        for h in headlines:
            if h not in seen:
                seen.append(h)

        kept = []
        for h in seen:
            words = h.split()
            if len(words) > 5 and len(words) % 2 == 0:
                mid = len(words) // 2
                first_half = " ".join(words[:mid])
                second_half = " ".join(words[mid:])
                if first_half == second_half:
                    h = first_half

            tokens = h.split()
            overlap_found = False
            for span in range(4, 15):
                for i in range(len(tokens) - span * 2 + 1):
                    phrase1 = " ".join(tokens[i:i + span])
                    phrase2 = " ".join(tokens[i + span:i + span * 2])
                    if phrase1 == phrase2 and len(phrase1) > 15:
                        h = " ".join(tokens[:i + span] + tokens[i + span * 2:])
                        overlap_found = True
                        break
                if overlap_found:
                    break

            if len(h) >= 60:
                midpoint = len(h) // 2
                search_start = max(0, midpoint - 20)
                search_end = min(len(h), midpoint + 20)
                window = h[search_start:search_end]
                result = h
                for punct in ['. ', ' - ', ' | ']:
                    pos = window.find(punct)
                    if pos != -1:
                        split_idx = search_start + pos + len(punct)
                        before = h[:split_idx].strip()
                        after = h[split_idx:].strip()
                        if before and after and before[0].isupper() and after[0].isupper():
                            before = before.rstrip() + '. '
                            result = before + after
                kept.append(result)
            else:
                if not any(h != other and h in other for other in seen):
                    kept.append(h)

        final = []
        for k in kept:
            if k not in final:
                final.append(k)

        merged_headline = ". ".join(final) if len(final) > 1 else (final[0] if final else "")
        merged.append({'headline': merged_headline, 'url': url})
    return merged

def crawl(sources, header):
    href_list = []
    crawl_stats = {
        "successful": [],
        "failed": []
    }
    for source in sources:
        base_source = source.rstrip('/')
        source_count = 0
        failure_reason = None

        try:
            resps = httpx.get(source, headers=header, timeout=30)
            if not resps.status_code == 200:
                print(f"source: {source} | status_code: {resps.status_code}. Using curl_cffi for crawling")
                try:
                    resps = requests.get(source, impersonate="chrome124", timeout=30, headers=dawn_headers)
                    if not resps.status_code == 200:
                        print(f"source: {source} | curl_cffi status: {resps.status_code}")
                        print(f"source: {source} not been able to crawl")
                        failure_reason = f"{resps.status_code} Error"
                        crawl_stats["failed"].append({"source": source, "reason": failure_reason})
                        continue
                except Exception as e:
                    print(f"source: {source} failed with: {e}")
                    failure_reason = str(e)
                    crawl_stats["failed"].append({"source": source, "reason": failure_reason})
                    continue
        except Exception as e:
            print(f"source: {source} failed with: {e}")
            failure_reason = str(e)
            crawl_stats["failed"].append({"source": source, "reason": failure_reason})
            continue

        soup = BeautifulSoup(resps.text, 'html.parser', parse_only=SoupStrainer('a'))
        for link in soup.find_all('a'):
            if link.get_text(strip=True):
                if link.find_parent(["figure", "figcaption"]):
                    continue
                title = link.get_text(strip=False)
                title = re.sub(r'\s+', ' ', title).strip()
                title = re.sub(r'^\d{1,2}:\d{2}\s*', '', title)
                title = title.replace('“', '"').replace('”', '"').replace("’", "'").replace("‘", "'")
                title = re.sub(r'[\u064B-\u065F\u0670]', '', title)
                title = re.sub(r'[\.\u2026\s]+$', '', title)
                if len(title) < 30:
                    continue
                href = link.get('href')
                href = urljoin(base_source, href).rstrip('/')
                if href == base_source:
                    continue
                href_list.append({'headline': title, 'url': href})
                source_count += 1

        if resps.status_code == 200:
            print(f"{base_source} crawled ({source_count} items)")
            crawl_stats["successful"].append({"source": source, "count": source_count})

    href_list = [dict(t) for t in {tuple(d.items()) for d in href_list}]
    href_list = merge_by_url(href_list)
    for index, item in enumerate(href_list):
        item["id"] = index
    return {"news": href_list, "stats": crawl_stats}

# =============================================================
# AUTHENTICATION ROUTES
# =============================================================

@app.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    next: Optional[str] = "/",
    db: AsyncSession = Depends(get_db)
):
    current_user = await get_current_user_optional(request, db)
    if current_user:
        return RedirectResponse(url=next or "/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"next_url": next, "error": None}
    )

@app.post("/login", response_class=HTMLResponse)
async def handle_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: Optional[str] = "/",
    db: AsyncSession = Depends(get_db)
):
    clean_email = email.strip().lower()
    result = await db.execute(select(User).where(User.email == clean_email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "next_url": next,
                "email": clean_email,
                "error": "Invalid email or password."
            },
            status_code=400
        )

    if not user.is_active:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "next_url": next,
                "email": clean_email,
                "error": "This account is inactive. Please contact an administrator."
            },
            status_code=403
        )

    response = RedirectResponse(url=next or "/", status_code=status.HTTP_303_SEE_OTHER)
    token = create_session_token(user.id)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https"
    )
    return response

@app.get("/register", response_class=HTMLResponse)
async def register_page(
    request: Request,
    next: Optional[str] = "/",
    db: AsyncSession = Depends(get_db)
):
    current_user = await get_current_user_optional(request, db)
    if current_user:
        return RedirectResponse(url=next or "/", status_code=status.HTTP_303_SEE_OTHER)

    user_count = (await db.execute(select(func.count(User.id)))).scalar_one()
    is_first_user = (user_count == 0)

    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context={
            "next_url": next,
            "is_first_user": is_first_user,
            "error": None
        }
    )

@app.post("/register", response_class=HTMLResponse)
async def handle_register(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    invite_code: Optional[str] = Form(None),
    next: Optional[str] = "/",
    db: AsyncSession = Depends(get_db)
):
    clean_email = email.strip().lower()
    clean_name = full_name.strip()

    user_count = (await db.execute(select(func.count(User.id)))).scalar_one()
    is_first_user = (user_count == 0)

    if not is_first_user:
        if not invite_code or invite_code.strip() != INVITE_CODE_SECRET:
            return templates.TemplateResponse(
                request=request,
                name="register.html",
                context={
                    "next_url": next,
                    "is_first_user": False,
                    "full_name": clean_name,
                    "email": clean_email,
                    "invite_code": invite_code,
                    "error": "Invalid or missing invite/registration code."
                },
                status_code=403
            )

    if len(password) < 6:
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "next_url": next,
                "is_first_user": is_first_user,
                "full_name": clean_name,
                "email": clean_email,
                "error": "Password must be at least 6 characters."
            },
            status_code=400
        )

    # Check for existing email
    existing = (await db.execute(select(User).where(User.email == clean_email))).scalar_one_or_none()
    if existing:
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={
                "next_url": next,
                "is_first_user": is_first_user,
                "full_name": clean_name,
                "email": clean_email,
                "error": "An account with this email already exists."
            },
            status_code=400
        )

    # Create new user
    new_user = User(
        email=clean_email,
        full_name=clean_name,
        hashed_password=hash_password(password),
        is_admin=is_first_user,
        is_active=True
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    # If first user, migrate legacy file data into this account
    if is_first_user:
        await migrate_legacy_data_to_user(db, new_user.id)

    # Auto sign in
    response = RedirectResponse(url=next or "/", status_code=status.HTTP_303_SEE_OTHER)
    token = create_session_token(new_user.id)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https"
    )
    return response

@app.post("/logout")
async def handle_logout(request: Request):
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(key=SESSION_COOKIE_NAME)
    return response

# =============================================================
# APPLICATION PAGES (AUTHENTICATED)
# =============================================================

@app.get("/", response_class=HTMLResponse)
async def home_page(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    news = await get_user_crawled_news(db, current_user.id)
    settings = await get_user_settings(db, current_user.id)
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={"news": news, "settings": settings, "user": current_user}
    )

@app.get("/setting", response_class=HTMLResponse)
async def setting_page(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    settings = await get_user_settings(db, current_user.id)
    return templates.TemplateResponse(
        request=request,
        name="setting.html",
        context={"settings": settings, "user": current_user}
    )

@app.post("/crawl", response_class=HTMLResponse)
@app.get("/crawl", response_class=HTMLResponse)
async def crawl_news(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    settings = await get_user_settings(db, current_user.id)
    sources = settings.sources
    crawl_result = crawl(sources, custom_headers)
    await save_user_crawled_news(db, current_user.id, crawl_result["news"])
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "news": crawl_result["news"],
            "crawl_stats": crawl_result["stats"],
            "status_msg": "Crawled and refreshed news sources!",
            "settings": settings,
            "user": current_user
        }
    )

@app.post("/filter", response_class=HTMLResponse)
@app.get("/filter", response_class=HTMLResponse)
async def filter_news(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    settings = await get_user_settings(db, current_user.id)
    news = await get_user_crawled_news(db, current_user.id)
    keywords = [k.lower().strip() for k in settings.keywords if k.strip()]
    
    if not keywords:
        filtered = news
        status_msg = "No keywords configured in Settings. Displaying all news items."
    else:
        filtered = []
        for h in news:
            matched = [k for k in keywords if k in h['headline'].lower()]
            if matched:
                filtered.append({"tags": matched} | h)
        status_msg = f"Filtered {len(filtered)} news item(s) matching keyword(s): {'| '.join(settings.keywords)}"
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "news": filtered,
            "status_msg": status_msg,
            "is_filtered": True,
            "settings": settings,
            "user": current_user
        }
    )

@app.get("/saved", response_class=HTMLResponse)
async def saved_stories_page(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stories = await get_user_saved_stories(db, current_user.id)
    return templates.TemplateResponse(
        request=request,
        name="saved_stories.html",
        context={"stories": stories, "user": current_user}
    )

@app.delete("/api/story/saved/{index}")
async def delete_saved_story(
    index: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stories = await get_user_saved_stories(db, current_user.id)
    if index < 0 or index >= len(stories):
        raise HTTPException(status_code=404, detail="Saved story not found.")
    
    target = stories[index]
    target_heading = (target.get("heading") or target.get("headline") or "").strip()
    target_url = (target.get("url") or "").strip()

    # Find and delete row from DB
    result = await db.execute(
        select(UserSavedStory).where(UserSavedStory.user_id == current_user.id)
    )
    records = result.scalars().all()
    for rec in records:
        if (target_url and rec.url == target_url) or (not target_url and rec.heading == target_heading):
            await db.delete(rec)
            await db.commit()
            break

    return {"status": "success", "message": "Saved story deleted.", "removed": target}

@app.get("/story", response_class=HTMLResponse)
async def story_page(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stories = await get_user_selected_stories(db, current_user.id)
    saved_idx = request.query_params.get("saved_index")
    if saved_idx is not None:
        try:
            s_idx = int(saved_idx)
            saved_stories = await get_user_saved_stories(db, current_user.id)
            if 0 <= s_idx < len(saved_stories):
                chosen_saved = saved_stories[s_idx]
                matched_in_selected = next(
                    (i for i, s in enumerate(stories) 
                     if (s.get("url") and s.get("url") == chosen_saved.get("url")) or 
                        ((s.get("heading") or s.get("headline")) == (chosen_saved.get("heading") or chosen_saved.get("headline")))),
                    None
                )
                if matched_in_selected is None:
                    stories.insert(0, chosen_saved)
                    await save_user_selected_stories(db, current_user.id, stories)
        except Exception as e:
            print(f"Error loading saved story into studio: {e}")

    return templates.TemplateResponse(
        request=request,
        name="story.html",
        context={"stories": stories, "user": current_user}
    )

@app.post("/process", response_class=RedirectResponse)
async def process_news(
    request: Request,
    background_tasks: BackgroundTasks,
    selected_news: list[int] = Form(default=[]),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    news = await get_user_crawled_news(db, current_user.id)
    selected_items = []
    for index in selected_news:
        matched = next((item for item in news if item.get("id") == index), None)
        if matched:
            selected_items.append(matched)

    clean_items = []
    for it in selected_items:
        clean_it = {
            "heading": it.get("heading") or it.get("headline", ""),
            "url": it.get("url", ""),
            "subheading": it.get("subheading", ""),
            "summary": it.get("summary", ""),
            "image": it.get("image", ""),
            "content": it.get("content", ""),
            "author": it.get("author", ""),
            "date": it.get("date", ""),
            "sitename": it.get("sitename", ""),
            "source_language": it.get("source_language", ""),
            "blocks": it.get("blocks", []),
        }
        if "tags" in it and it.get("tags"):
            clean_it["tags"] = it["tags"]
        clean_items.append(clean_it)

    user_settings = await get_user_settings(db, current_user.id)
    clean_items = detect_lang(clean_items, user_settings.source_languages)
    await save_user_selected_stories(db, current_user.id, clean_items)

    # Server-side progressive background fetcher per user
    background_tasks.add_task(background_fetch_user_stories, current_user.id)
    return RedirectResponse(url="/story", status_code=status.HTTP_303_SEE_OTHER)

# =============================================================
# API ENDPOINTS (AUTHENTICATED)
# =============================================================

@app.get("/api/settings", response_model=AgentSettings)
async def get_settings_api(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    return await get_user_settings(db, current_user.id)

@app.post("/api/settings")
async def save_settings_api(
    settings: AgentSettings,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    clean_source_languages = [l.strip() for l in settings.source_languages if l.strip()]
    clean_target_languages = [l.strip() for l in settings.target_languages if l.strip()]
    clean_sources = [s.strip() for s in settings.sources if s.strip()]
    clean_keywords = [k.strip() for k in settings.keywords if k.strip()]
    
    seen_sl = set()
    dedup_source_languages = [l for l in clean_source_languages if not (l.lower() in seen_sl or seen_sl.add(l.lower()))]

    seen_tl = set()
    dedup_target_languages = [l for l in clean_target_languages if not (l.lower() in seen_tl or seen_tl.add(l.lower()))]

    seen_s = set()
    dedup_sources = [s for s in clean_sources if not (s.lower() in seen_s or seen_s.add(s.lower()))]
    
    seen_k = set()
    dedup_keywords = [k for k in clean_keywords if not (k.lower() in seen_k or seen_k.add(k.lower()))]

    updated = AgentSettings(
        source_languages=dedup_source_languages,
        target_languages=dedup_target_languages,
        sources=dedup_sources,
        keywords=dedup_keywords
    )
    success = await save_user_settings(db, current_user.id, updated)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save settings.")
    return {"status": "success", "message": "Settings saved successfully!", "settings": updated}

@app.post("/api/story/update")
async def update_story_item(
    payload: StoryUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stories = await get_user_selected_stories(db, current_user.id)
    if payload.index < 0 or payload.index >= len(stories):
        raise HTTPException(status_code=404, detail="Story index out of range.")
    
    updated_dict = payload.story.model_dump()
    stories[payload.index] = updated_dict

    # 1. Update in active studio selection
    await save_user_selected_stories(db, current_user.id, stories)

    # 2. Update/append in user's saved stories archive
    saved_entry = await save_user_saved_story(db, current_user.id, updated_dict)

    return {"status": "success", "message": "Story saved successfully!", "story": saved_entry}

@app.post("/api/story/save-all")
async def save_all_stories(
    payload: StoryBatchSaveRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    items = [s.model_dump() for s in payload.stories]
    user_settings = await get_user_settings(db, current_user.id)

    missing_lang = [it for it in items if not (it.get("source_language") or "").strip()]
    if missing_lang:
        detected = detect_lang(missing_lang, user_settings.source_languages)
        for target, src in zip(missing_lang, detected):
            target["source_language"] = src.get("source_language", "")

    await save_user_selected_stories(db, current_user.id, items)
    return {"status": "success", "count": len(items)}

# =============================================================
# STORY SCRAPING & LLM TRANSFORMATION
# =============================================================

story_fetch_state = defaultdict(lambda: {"status": "idle", "total": 0, "completed": 0})

def scrape_article_from_url(url: str) -> dict:
    url = (url or "").strip()
    if not url or not url.startswith("http"):
        return {}

    html_content = ""
    try:
        resp = httpx.get(url, headers=custom_headers, timeout=20, follow_redirects=True)
        if resp.status_code == 200 and len(resp.text) > 500:
            html_content = resp.text
    except Exception as e:
        print(f"httpx fetch failed for {url}: {e}")

    if not html_content:
        try:
            resp = requests.get(url, impersonate="chrome124", headers=dawn_headers, timeout=25)
            if resp.status_code == 200:
                html_content = resp.text
        except Exception as e:
            print(f"curl_cffi fetch failed for {url}: {e}")

    if not html_content:
        return {}

    soup = BeautifulSoup(html_content, "html.parser")
    try:
        text_json = extract(html_content, output_format="json")
        body = json.loads(text_json)['text'] if text_json else ""
    except Exception as e:
        print(f"trafilatura extract failed for {url}: {e}")
        body = ""

    try:
        meta = extract_metadata(html_content)
    except Exception as e:
        print(f"trafilatura extract_metadata failed for {url}: {e}")
        meta = None

    og_title = soup.find("meta", property="og:title")
    tw_title = soup.find("meta", attrs={"name": "twitter:title"})
    h1 = soup.find("h1")
    title_tag = soup.find("title")
    heading = (meta.title if meta and meta.title else None) or \
              (og_title.get("content") if og_title else None) or \
              (tw_title.get("content") if tw_title else None) or \
              (h1.get_text(strip=True) if h1 else None) or \
              (title_tag.get_text(strip=True) if title_tag else "")

    og_desc = soup.find("meta", property="og:description")
    meta_desc = soup.find("meta", attrs={"name": "description"})
    tw_desc = soup.find("meta", attrs={"name": "twitter:description"})
    subheading = (meta.description if meta and meta.description else None) or \
                 (og_desc.get("content") if og_desc else None) or \
                 (meta_desc.get("content") if meta_desc else None) or \
                 (tw_desc.get("content") if tw_desc else "")

    og_img = soup.find("meta", property="og:image")
    tw_img = soup.find("meta", attrs={"name": "twitter:image"})
    image = (meta.image if meta and meta.image else None) or \
            (og_img.get("content") if og_img else None) or \
            (tw_img.get("content") if tw_img else "")
    if image:
        image = urljoin(url, image)

    meta_author = soup.find("meta", attrs={"name": "author"}) or soup.find("meta", property="article:author")
    author_el = soup.find(class_=lambda c: c and any(k in c.lower() for k in ["author__name", "byline", "author-name", "author"]))
    author = (meta.author if meta and meta.author else None) or \
             (meta_author.get("content") if meta_author and meta_author.get("content") else None) or \
             (author_el.get_text(strip=True) if author_el else "")

    date_meta = soup.find("meta", property="article:published_time") or \
                soup.find("meta", attrs={"name": "publish-date"}) or \
                soup.find("meta", attrs={"name": "pubdate"})
    time_el = soup.find("time")
    date_str = (meta.date if meta and meta.date else None) or \
               (date_meta.get("content") if date_meta and date_meta.get("content") else None) or \
               (time_el.get_text(strip=True) if time_el else "")

    og_site = soup.find("meta", property="og:site_name")
    sitename = (meta.sitename if meta and meta.sitename else None) or \
               (og_site.get("content") if og_site else None) or \
               ""
    if not sitename:
        try:
            from urllib.parse import urlparse
            netloc = urlparse(url).netloc
            sitename = netloc.replace("www.", "")
        except Exception:
            sitename = ""

    return {
        "url": url,
        "heading": heading or "",
        "subheading": subheading or "",
        "content": body or "",
        "image": image or "",
        "author": author or "",
        "date": date_str or "",
        "sitename": sitename,
        "blocks": []
    }

async def background_fetch_user_stories(user_id: int):
    async with AsyncSessionLocal() as session:
        try:
            stories = await get_user_selected_stories(session, user_id)
            if not stories:
                story_fetch_state[user_id] = {"status": "completed", "total": 0, "completed": 0}
                return

            story_fetch_state[user_id] = {
                "status": "fetching",
                "total": len(stories),
                "completed": 0
            }

            for idx, story in enumerate(stories):
                url = story.get("url", "").strip()
                if url and url.startswith("http"):
                    try:
                        fetched = scrape_article_from_url(url)
                        if fetched:
                            if fetched.get("heading"):
                                story["heading"] = fetched["heading"]
                            if fetched.get("subheading"):
                                story["subheading"] = fetched["subheading"]
                            if fetched.get("content"):
                                story["content"] = fetched["content"]
                            if fetched.get("author"):
                                story["author"] = fetched["author"]
                            if fetched.get("date"):
                                story["date"] = fetched["date"]
                            if fetched.get("sitename"):
                                story["sitename"] = fetched["sitename"]
                            if fetched.get("image"):
                                story["image"] = fetched["image"]

                            img_url = (story.get("image") or "").strip()
                            if img_url:
                                if not isinstance(story.get("blocks"), list):
                                    story["blocks"] = []
                                existing_img = next((b for b in story["blocks"] if isinstance(b, dict) and b.get("type") == "image" and b.get("url") == img_url), None)
                                if not existing_img:
                                    import time
                                    story["blocks"].append({
                                        "id": f"block_{int(time.time() * 1000)}_{idx}",
                                        "type": "image",
                                        "url": img_url,
                                        "caption": story.get("subheading") or story.get("heading") or "News story visual coverage",
                                        "source": story.get("sitename") or story.get("author") or "Source Wire / Photo"
                                    })
                    except Exception as e:
                        print(f"Error background fetching story {idx} ({url}): {e}")
                story_fetch_state[user_id]["completed"] = idx + 1
                await save_user_selected_stories(session, user_id, stories)

            story_fetch_state[user_id]["status"] = "completed"
        except Exception as outer_e:
            print(f"Fatal error in background_fetch_user_stories: {outer_e}")
            story_fetch_state[user_id]["status"] = "completed"

@app.post("/api/story/fetch")
async def fetch_story_from_url(
    payload: StoryFetchRequest,
    current_user: User = Depends(get_current_user)
):
    url = (payload.url or "").strip()
    if not url or not url.startswith("http"):
        raise HTTPException(status_code=400, detail="A valid HTTP/HTTPS URL is required.")

    fetched = scrape_article_from_url(url)
    if not fetched:
        raise HTTPException(status_code=502, detail="Unable to retrieve or parse article content from URL.")

    return {
        "status": "success",
        "data": fetched
    }

@app.get("/api/story/status")
async def get_story_fetch_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stories = await get_user_selected_stories(db, current_user.id)
    u_state = story_fetch_state[current_user.id]
    return {
        "status": u_state.get("status", "idle"),
        "total": u_state.get("total", len(stories)),
        "completed": u_state.get("completed", 0),
        "stories": stories
    }

def _get_gemini_client():
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None
    from google import genai
    return genai.Client(api_key=api_key)

TRANSFORM_SYSTEM_PROMPT = """You are an expert editorial assistant for a professional news agency.
You transform news stories based on the user's instructions.

You will receive a news story with three fields: heading, subheading, and body.
The user will give you instructions on how to transform the story (e.g., translate, rewrite, shorten, change tone, summarize, expand, etc.).

You MUST respond with valid JSON containing exactly these three fields:
{
  "heading": "transformed heading text",
  "subheading": "transformed subheading text",
  "body": "transformed body text"
}

Rules:
- Always return ALL three fields, even if only one changed.
- If the user's instruction only applies to one field, return the others unchanged.
- Preserve the journalistic accuracy and factual content of the original story.
- Follow the user's transformation instructions precisely.
- Do NOT add any text outside the JSON object — no markdown, no explanation, just the JSON.
- If the user asks you to translate, translate ALL three fields to the target language.
- Maintain proper formatting and paragraph structure in the body field."""

@app.post("/api/story/transform")
async def transform_story(
    payload: StoryTransformRequest,
    current_user: User = Depends(get_current_user)
):
    gemini_client = _get_gemini_client()
    if not gemini_client:
        raise HTTPException(
            status_code=503,
            detail="Gemini API key not configured. Please add GEMINI_API_KEY to your .env.local file."
        )

    contents = []
    history = payload.messages[-10:] if len(payload.messages) > 10 else payload.messages
    for msg in history:
        contents.append({
            "role": "user" if msg.role == "user" else "model",
            "parts": [{"text": msg.content}]
        })

    story_context = f"""Here is the current story:

HEADING: {payload.heading}

SUBHEADING: {payload.subheading}

BODY: {payload.body}

---
User instruction: {payload.user_message}

Respond with ONLY a JSON object containing the transformed "heading", "subheading", and "body" fields."""

    contents.append({
        "role": "user",
        "parts": [{"text": story_context}]
    })

    requested_model = (payload.model or "").strip()
    selected_model = requested_model if requested_model in ALLOWED_AI_MODELS else DEFAULT_AI_MODEL

    try:
        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=TRANSFORM_SYSTEM_PROMPT,
            temperature=0.7,
            max_output_tokens=8192,
            response_mime_type="application/json",
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
        )

        response = gemini_client.models.generate_content(
            model=selected_model,
            contents=contents,
            config=config
        )

        response_text = response.text.strip()
        json_text = response_text
        if json_text.startswith("```"):
            lines = json_text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            json_text = "\n".join(lines).strip()

        try:
            result = json.loads(json_text)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=422,
                detail=f"LLM returned invalid JSON. Raw response: {response_text[:500]}"
            )

        transformed = {
            "heading": result.get("heading", payload.heading),
            "subheading": result.get("subheading", payload.subheading),
            "body": result.get("body", payload.body),
        }

        return {
            "status": "success",
            "model": selected_model,
            "transformed": transformed,
            "assistant_message": payload.user_message
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"Gemini API error: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"LLM transformation failed: {str(e)}"
        )
