import re, os, json
import httpx
from bs4 import BeautifulSoup, SoupStrainer
from fastapi import FastAPI, Request, HTTPException, Form, status, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from vercel.blob import BlobClient
from urllib.parse import urljoin
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
from app.storage import load_settings, save_settings, is_blob_configured
from curl_cffi import requests
from collections import defaultdict
from lingua import Language, LanguageDetectorBuilder
from trafilatura import extract, extract_metadata

client = BlobClient()
if os.environ.get("VERCEL"):
    DATA_DIR = Path("data")
else:
    DATA_DIR = Path(__file__).resolve().parent.parent / "data"

DATA_FILE = DATA_DIR / "links.json"
SELECTED_NEWS_FILE = DATA_DIR / "selected_news.json"
BASE_DIR = Path(__file__).resolve().parent.parent

def ensure_data_dir():
    if not os.environ.get("VERCEL"):
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
def detect_lang(items):
    if not items:
        return items
    settings = load_settings()
    source_languages = settings.source_languages or []
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
app = FastAPI(
    title="News Curation Agent",
    description="Configure Scope URLs, Track Keywords and Curate News",
    version="1.0.0"
)

custom_headers = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36',
                  'Accept-Language': 'da, en-gb, en'}

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
            contained = [other for other in seen if other != h and other in h]
            if contained:
                result = h
                for sub in contained:
                    idx = result.find(sub)
                    if idx > 0:
                        before = result[:idx]
                        after = result[idx:]
                        if not before.rstrip().endswith(('.', '!', '?', ':')):
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

        merged_headline = ". ".join(final) if len(final) > 1 else final[0]

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
                        print("***************************************")
                        continue
                except Exception as e:
                    print(f"curl_cffi error on {source}: {e}")
                    failure_reason = "Connection Error"
                    crawl_stats["failed"].append({"source": source, "reason": failure_reason})
                    print("***************************************")
                    continue
        except Exception as e:
            print(f"httpx error on {source}: {e}")
            failure_reason = "Connection Error"
            crawl_stats["failed"].append({"source": source, "reason": failure_reason})
            print("***************************************")
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
        print("***************************************")
    # deduping
    href_list = [dict(t) for t in {tuple(d.items()) for d in href_list}]
    href_list = merge_by_url(href_list)
    ensure_data_dir()
    if os.environ.get("VERCEL"):
        if not is_blob_configured():
            print("Notice: Vercel Blob token not configured. Skipping upload to blob.")
        else:
            try:
                client.put(
                    "data/links.json",
                    json.dumps(href_list),
                    access="private",  # or "public" — now required
                    content_type="application/json",
                    overwrite=True,
                    cache_control_max_age=0)
            except Exception as e:
                print(f"Error encountered: {e}")
    else:
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(href_list, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error encountered: {e}")

    for index, item in enumerate(href_list):
        item["id"] = index
    return {"news": href_list, "stats": crawl_stats}

def load_news_data():
    if os.environ.get("VERCEL"):
        if not is_blob_configured():  # <--- MISSING CHECK
            print("Error: Vercel Blob token not configured. Cannot load news data.")
            return []

        try:
            data = client.get("data/links.json", access='private', use_cache=False)
            data = json.loads(data.content)
            for index, item in enumerate(data):
                item["id"] = index
            return data

        except Exception as e:
            print(f"Error loading candidates: {e}")
            return []

    else:
        try:
            ensure_data_dir()
            if not DATA_FILE.exists():
                return []
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for index, item in enumerate(data):
                item["id"] = index
            return data
        except Exception as e:
            print(f"Error loading candidates: {e}")
            return []


templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

@app.get("/", response_class=HTMLResponse)
async def home_page(request: Request):
    news = load_news_data()
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={"news": news}
    )

@app.get("/setting", response_class=HTMLResponse)
async def setting_page(request: Request):
    settings = load_settings()
    return templates.TemplateResponse(
        request=request,
        name="setting.html",
        context={"settings": settings}
    )

@app.post("/crawl", response_class=HTMLResponse)
@app.get("/crawl", response_class=HTMLResponse)
async def crawl_news(request: Request):
    settings = load_settings()
    sources = settings.sources
    crawl_result = crawl(sources, custom_headers)
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "news": crawl_result["news"],
            "crawl_stats": crawl_result["stats"],
            "status_msg": "Crawled and refreshed news sources!"
        }
    )


@app.post("/filter", response_class=HTMLResponse)
@app.get("/filter", response_class=HTMLResponse)
async def filter_news(request: Request):
    settings = load_settings()
    news = load_news_data()
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
            "is_filtered": True
        }
    )

def save_raw_selected_news(items: list[dict]):
    ensure_data_dir()
    if os.environ.get("VERCEL"):
        if not is_blob_configured():
            print("Notice: Vercel Blob token not configured. Skipping upload to blob.")
        else:
            try:
                client.put(
                    "data/selected_news.json",
                    json.dumps(items, ensure_ascii=False),
                    access="private",
                    content_type="application/json",
                    overwrite=True,
                    cache_control_max_age=0
                )
            except Exception as e:
                print(f"Error saving raw selected news to blob: {e}")
    else:
        try:
            with open(SELECTED_NEWS_FILE, "w", encoding="utf-8") as f:
                json.dump(items, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving raw selected news to file: {e}")

def save_selected_news(items: list[dict]):
    ensure_data_dir()
    clean_items = []
    for it in items:
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
    clean_items = detect_lang(clean_items)
    save_raw_selected_news(clean_items)

def load_selected_news():
    if os.environ.get("VERCEL"):
        if not is_blob_configured():
            return []
        try:
            data = client.get("data/selected_news.json", access="private", use_cache=False)
            return json.loads(data.content)
        except Exception as e:
            print(f"Error loading selected news from blob: {e}")
            return []
    else:
        try:
            ensure_data_dir()
            if not SELECTED_NEWS_FILE.exists():
                return []
            with open(SELECTED_NEWS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading selected news from file: {e}")
            return []

@app.get("/story", response_class=HTMLResponse)
async def story_page(request: Request):
    stories = load_selected_news()
    return templates.TemplateResponse(
        request=request,
        name="story.html",
        context={"stories": stories}
    )

@app.post("/process", response_class=RedirectResponse)
async def process_news(
    request: Request,
    background_tasks: BackgroundTasks,
    selected_news: list[int] = Form(default=[])):
    news = load_news_data()
    selected_items = []
    for index in selected_news:
        matched = next((item for item in news if item.get("id") == index), None)
        if matched:
            selected_items.append(matched)

    save_selected_news(selected_items)
    # Only run server-side background fetching in non-serverless environments.
    # In Vercel serverless functions, background tasks freeze on redirect;
    # the client-side progressive fetcher on /story handles fetching reliably.
    if not os.environ.get("VERCEL"):
        background_tasks.add_task(background_fetch_all_stories)
    return RedirectResponse(url="/story", status_code=status.HTTP_303_SEE_OTHER)

@app.get("/api/settings", response_model=AgentSettings)
async def get_settings():
    return load_settings()

@app.post("/api/settings")
async def save_all_settings(settings: AgentSettings):
    clean_source_languages = [l.strip() for l in settings.source_languages if l.strip()]
    clean_target_languages = [l.strip() for l in settings.target_languages if l.strip()]
    clean_sources = [s.strip() for s in settings.sources if s.strip()]
    clean_keywords = [k.strip() for k in settings.keywords if k.strip()]
    
    # Deduplicate preserving order
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
    success = save_settings(updated)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save settings.")
    return {"status": "success", "message": "Settings saved successfully!", "settings": updated}

@app.post("/api/story/update")
async def update_story_item(payload: StoryUpdateRequest):
    stories = load_selected_news()
    if payload.index < 0 or payload.index >= len(stories):
        raise HTTPException(status_code=404, detail="Story index out of range.")
    
    updated_dict = payload.story.model_dump()
    stories[payload.index] = updated_dict

    # Save back to storage
    ensure_data_dir()
    if os.environ.get("VERCEL"):
        if is_blob_configured():
            try:
                client.put(
                    "data/selected_news.json",
                    json.dumps(stories, ensure_ascii=False),
                    access="private",
                    content_type="application/json",
                    overwrite=True,
                    cache_control_max_age=0
                )
            except Exception as e:
                print(f"Error updating selected news to blob: {e}")
                raise HTTPException(status_code=500, detail="Failed to save story to blob.")
    else:
        try:
            with open(SELECTED_NEWS_FILE, "w", encoding="utf-8") as f:
                json.dump(stories, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error updating selected news file: {e}")
            raise HTTPException(status_code=500, detail="Failed to save story to file.")

    return {"status": "success", "message": "Story saved successfully!", "story": updated_dict}

@app.post("/api/story/save-all")
async def save_all_stories(payload: StoryBatchSaveRequest):
    items = [s.model_dump() for s in payload.stories]

    # Auto-detect language only for stories where source_language is missing
    missing_lang = [it for it in items if not (it.get("source_language") or "").strip()]
    if missing_lang:
        detected = detect_lang(missing_lang)
        for target, src in zip(missing_lang, detected):
            target["source_language"] = src.get("source_language", "")

    ensure_data_dir()
    if os.environ.get("VERCEL"):
        if is_blob_configured():
            try:
                client.put(
                    "data/selected_news.json",
                    json.dumps(items, ensure_ascii=False),
                    access="private",
                    content_type="application/json",
                    overwrite=True,
                    cache_control_max_age=0
                )
            except Exception as e:
                print(f"Error saving batch selected news to blob: {e}")
                raise HTTPException(status_code=500, detail="Failed to save stories to blob.")
        else:
            raise HTTPException(status_code=500, detail="Vercel Blob token not configured.")
    else:
        try:
            with open(SELECTED_NEWS_FILE, "w", encoding="utf-8") as f:
                json.dump(items, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving batch selected news to file: {e}")
            raise HTTPException(status_code=500, detail="Failed to save stories to file.")

    return {"status": "success", "count": len(items)}

story_fetch_state = {
    "status": "idle",
    "total": 0,
    "completed": 0
}

def scrape_article_from_url(url: str) -> dict:
    url = (url or "").strip()
    if not url or not url.startswith("http"):
        return {}

    html_content = ""
    # Try httpx first
    try:
        resp = httpx.get(url, headers=custom_headers, timeout=20, follow_redirects=True)
        if resp.status_code == 200 and len(resp.text) > 500:
            html_content = resp.text
    except Exception as e:
        print(f"httpx fetch failed for {url}: {e}")

    # Fallback to curl_cffi with chrome impersonation
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
               (og_site.get("content") if og_site else "")

    return {
        "heading": heading,
        "subheading": subheading,
        "summary": "",
        "content": body,
        "author": author,
        "date": date_str,
        "image": image,
        "sitename": sitename,
        "blocks": []
    }

def background_fetch_all_stories():
    global story_fetch_state
    try:
        stories = load_selected_news()
        if not stories:
            story_fetch_state = {"status": "completed", "total": 0, "completed": 0}
            return

        story_fetch_state = {
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
            story_fetch_state["completed"] = idx + 1
            # Save progress as each story is fetched
            save_raw_selected_news(stories)

        story_fetch_state["status"] = "completed"
    except Exception as outer_e:
        print(f"Fatal error in background_fetch_all_stories: {outer_e}")
        story_fetch_state["status"] = "completed"

@app.post("/api/story/fetch")
async def fetch_story_from_url(payload: StoryFetchRequest):
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
async def get_story_fetch_status():
    stories = load_selected_news()
    return {
        "status": story_fetch_state.get("status", "idle"),
        "total": story_fetch_state.get("total", len(stories)),
        "completed": story_fetch_state.get("completed", 0),
        "stories": stories
    }


# ==========================================
# LLM STORY TRANSFORMATION ENDPOINT
# ==========================================

def _get_gemini_client():
    """Lazily initialize Gemini client."""
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
async def transform_story(payload: StoryTransformRequest):
    """Transform a story using Gemini LLM based on user chat instructions."""
    gemini_client = _get_gemini_client()
    if not gemini_client:
        raise HTTPException(
            status_code=503,
            detail="Gemini API key not configured. Please add GEMINI_API_KEY to your .env.local file."
        )

    # Build conversation contents for multi-turn
    contents = []

    # Add conversation history (last 10 messages)
    history = payload.messages[-10:] if len(payload.messages) > 10 else payload.messages
    for msg in history:
        contents.append({
            "role": "user" if msg.role == "user" else "model",
            "parts": [{"text": msg.content}]
        })

    # Build current user message with story context
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
        response = gemini_client.models.generate_content(
            model=selected_model,
            contents=contents,
            config={
                "system_instruction": TRANSFORM_SYSTEM_PROMPT,
                "temperature": 0.7,
                "max_output_tokens": 8192,
            }
        )

        response_text = response.text.strip()

        # Try to extract JSON from the response (handle markdown code blocks)
        json_text = response_text
        if json_text.startswith("```"):
            # Remove markdown code block wrappers
            lines = json_text.split("\n")
            # Remove first line (```json or ```) and last line (```)
            lines = [l for l in lines if not l.strip().startswith("```")]
            json_text = "\n".join(lines).strip()

        try:
            result = json.loads(json_text)
        except json.JSONDecodeError:
            # If JSON parsing fails, return the raw text as an error
            raise HTTPException(
                status_code=422,
                detail=f"LLM returned invalid JSON. Raw response: {response_text[:500]}"
            )

        # Validate expected fields
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
