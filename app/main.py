import re, os, json
import httpx
from bs4 import BeautifulSoup, SoupStrainer
from fastapi import FastAPI, Request, HTTPException, Form, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from vercel.blob import BlobClient
from urllib.parse import urljoin
from app.models import AgentSettings
from app.storage import load_settings, save_settings, is_blob_configured
from curl_cffi import requests
from collections import defaultdict
from lingua import Language, LanguageDetectorBuilder


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
                    overwrite=True)
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
            data = client.get("data/links.json", access='private')
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

def save_selected_news(items: list[dict]):
    ensure_data_dir()
    clean_items = []
    for it in items:
        clean_it = {
            "heading": it.get("headline", ""),
            "url": it.get("url", ""),
            "subheading": "",
            "summary": "",
            "image": "",
            "content": "",
            "author": "",
            "date": "",
            "sitename":"",
            "source_language":"",
        }
        if "tags" in it and it.get("tags"):
            clean_it["tags"] = it["tags"]
        clean_items.append(clean_it)
    clean_items = detect_lang(clean_items)
    if os.environ.get("VERCEL"):
        if not is_blob_configured():
            print("Notice: Vercel Blob token not configured. Skipping upload to blob.")
        else:
            try:
                client.put(
                    "data/selected_news.json",
                    json.dumps(clean_items, ensure_ascii=False),
                    access="private",
                    content_type="application/json",
                    overwrite=True
                )
            except Exception as e:
                print(f"Error saving selected news to blob: {e}")
    else:
        try:
            with open(SELECTED_NEWS_FILE, "w", encoding="utf-8") as f:
                json.dump(clean_items, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving selected news to file: {e}")

def load_selected_news():
    if os.environ.get("VERCEL"):
        if not is_blob_configured():
            return []
        try:
            data = client.get("data/selected_news.json", access="private")
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
    selected_news: list[int] = Form(default=[])):
    news = load_news_data()
    selected_items = []
    for index in selected_news:
        matched = next((item for item in news if item.get("id") == index), None)
        if matched:
            selected_items.append(matched)

    save_selected_news(selected_items)
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
