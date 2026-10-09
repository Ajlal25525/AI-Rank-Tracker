# SERP Tracker Pro

A Streamlit app that checks where a domain ranks on Google for a list of keywords, using the Serper.dev API.

## Set up on a new computer

1. Install **Python 3.11 or newer** from https://www.python.org/downloads/
   - Windows: tick **"Add Python to PATH"** during install.
2. Install **Git** from https://git-scm.com/downloads
3. Open a terminal (Windows: PowerShell, Mac: Terminal) and run:

```bash
git clone https://github.com/ajlal25525/AI-Rank-Tracker.git
cd AI-Rank-Tracker
python -m venv .venv
```

4. Turn on the virtual environment:
   - Windows: `.venv\Scripts\activate`
   - Mac / Linux: `source .venv/bin/activate`
5. Install the packages and start the app:

```bash
pip install -r requirements.txt
streamlit run app.py
```

The app opens at http://localhost:8501.

## Running it again later

```bash
cd AI-Rank-Tracker
.venv\Scripts\activate        # Windows  (Mac: source .venv/bin/activate)
streamlit run app.py
```

## What you need to bring over from the old computer

- **Serper.dev API key** — the app does not save it. Get it again from your Serper.dev dashboard.
- **Keyword CSV files** — any keyword lists you uploaded (needs a `Keyword` column).
- **Exported ranking reports** — the CSVs you downloaded from the Export tab.

The app does **not** save ranking results to disk. Results only live in the open browser tab and are lost when you close or refresh it. Download the CSV report after each scan if you want to keep history.
