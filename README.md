# 📰 News Agent Settings & Control Panel

A sleek, modern, and reactive UI built with **FastAPI**, **Tailwind CSS**, and **Jinja2** to manage news ingestion sources, keyword tracking rules, exclusion filters, and agent execution parameters.

---

## 🚀 Features

- **📰 News Source URLs List**:
  - Add single URLs or use the **Bulk Line-by-Line Editor** to paste dozens of URLs at once.
  - Direct links to open sources, 1-click URL removal, and **Live Connectivity Tester** to check status codes.
  - **Quick-add Presets**: 1-click add popular feeds (TechCrunch, Hacker News, MIT Tech Review, Nature, Reuters, etc.).

- **🏷️ Keywords List**:
  - Interactive keyword chips with fast removal.
  - Fast comma/newline separated multi-keyword input.
  - Quick-add trending topic suggestions.

- **💾 Persistence & JSON Portability**:
  - Auto-saved and stored in `data/settings.json`.
  - Export and Import JSON configuration backups.
  - Reset to sample defaults.

---

## 🛠️ Quick Start

### 1. Activate Environment & Install Dependencies
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Start the FastAPI Server
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 3. Open in Browser
- **Settings UI**: [http://localhost:8000](http://localhost:8000)
- **Interactive Swagger API Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## 📁 Directory Structure
```
news-agent/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI application & REST endpoints
│   ├── models.py        # Pydantic schema models
│   └── storage.py       # JSON persistence & presets
├── data/
│   └── settings.json    # Persisted settings (auto-generated)
├── static/
│   └── js/
│       └── app.js       # Client-side reactivity & simulator
├── templates/
│   └── index.html       # Tailwind CSS UI template
├── requirements.txt
└── README.md
```
