import re, os, json
import httpx
from bs4 import BeautifulSoup, SoupStrainer
from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import pickle
from vercel.blob import BlobClient
from urllib.parse import urljoin
from app.models import AgentSettings
from app.storage import load_settings, save_settings, is_blob_configured
from curl_cffi import requests
client = BlobClient()
if os.environ.get("VERCEL"):
    DATA_DIR = Path("data")
else:
    DATA_DIR = Path(__file__).resolve().parent.parent / "data"

DATA_FILE = DATA_DIR / "links.pkl"
BASE_DIR = Path(__file__).resolve().parent.parent

def ensure_data_dir():
    if not os.environ.get("VERCEL"):
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

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

def crawl(sources, header):
    href_list = []
    for source in sources:
        resps = httpx.get(source, headers=header, timeout=30)
        if not resps.status_code == 200:
            print(f"source: {source} | status_code: {resps.status_code}. Using curl_cffi")
            try:
                resps = requests.get(source, impersonate="chrome124", timeout=30, headers=dawn_headers)
                print(f"source: {source} | curl_cffi status: {resps.status_code}")
                print(f"curl_cffi error on {source}: {e}")
            except Exception as e:
                print(f"curl_cffi error on {source}: {e}")
                continue

        soup = BeautifulSoup(resps.text, 'html.parser', parse_only=SoupStrainer('a'))
        for link in soup.find_all('a'):
            if link.get_text(strip=True):
                if link.find_parent(["figure", "figcaption"]):
                    continue
                title =link.get_text(strip=False)
                title = re.sub(r'\s+', ' ', title).strip()
                title = re.sub(r'^\d{1,2}:\d{2}\s*', '', title)
                title = title.replace('“', '"').replace('”', '"').replace("’", "'").replace("‘", "'")
                title = re.sub(r'[\u064B-\u065F\u0670]', '', title)
                title = re.sub(r'[\.\u2026\s]+$', '', title)
                if len(title) < 30:
                    continue
                href = link.get('href')
                href = urljoin(source, href)
                source = source.rstrip('/')
                href = href.rstrip('/')
                if href == source:
                    continue
                href_list.append({'headline': title, 'url': href})
        print(f"{source} crawled")
        print("***************************************")
    print("Removing duplicates")
    href_list = [dict(t) for t in {tuple(d.items()) for d in href_list}]
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
            with open(DATA_FILE, 'wb') as f:
                pickle.dump(href_list, f)
        except Exception as e:
            print(f"Error encountered: {e}")

    for index, item in enumerate(href_list):
        item["id"] = index
    return href_list

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
            with open(DATA_FILE, "rb") as f:
                data = pickle.load(f)
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
    news = crawl(sources, custom_headers)
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "news": news,
            "status_msg": "Crawled and refreshed news sources successfully!"
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

@app.post("/process", response_class=HTMLResponse)
async def process_news(
    request: Request,
    selected_news: list[int] = Form(default=[])):
    news = load_news_data()
    selected_items = []
    for index in selected_news:
        matched = next((item for item in news if item.get("id") == index), None)
        if matched:
            selected_items.append(matched)

    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "news": news,
            "selected_items": selected_items,
            "status_msg": f"Processed {len(selected_items)} selected news article(s)."
        }
    )

@app.get("/api/settings", response_model=AgentSettings)
async def get_settings():
    return load_settings()

@app.post("/api/settings")
async def save_all_settings(settings: AgentSettings):
    clean_sources = [s.strip() for s in settings.sources if s.strip()]
    clean_keywords = [k.strip() for k in settings.keywords if k.strip()]
    
    # Deduplicate preserving order
    seen_s = set()
    dedup_sources = [s for s in clean_sources if not (s.lower() in seen_s or seen_s.add(s.lower()))]
    
    seen_k = set()
    dedup_keywords = [k for k in clean_keywords if not (k.lower() in seen_k or seen_k.add(k.lower()))]

    updated = AgentSettings(sources=dedup_sources, keywords=dedup_keywords)
    success = save_settings(updated)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to save settings.")
    return {"status": "success", "message": "Settings saved successfully!", "settings": updated}
