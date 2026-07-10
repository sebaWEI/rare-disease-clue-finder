# Rare Disease Pre-diagnosis Clue Finder

iGEM 2026 · PekingHSC — patient-facing rare-disease pre-diagnosis clue finder.

## Stack

| Layer | Tech |
|-------|------|
| Frontend | React + TypeScript + Vite (`frontend/`) |
| Backend | FastAPI + NetworkX + FAISS (`main.py`) |
| Data | HPO × Orphanet knowledge graph |

## Quick start

```bash
# 1. Backend
pip install -r requirements.txt
python3 main.py          # → http://localhost:8000

# 2. Frontend (dev, with API proxy)
cd frontend
npm install
npm run dev              # → http://localhost:5173

# 3. Production frontend build (served by FastAPI at /)
cd frontend && npm run build
python3 main.py          # serves frontend/dist + /api/*
```

## Frontend structure

```
frontend/src/
  api/           # typed fetch wrappers
  components/    # layout + shared UI
  features/      # intake + report flows
  hooks/         # HPO selection state
  i18n/          # EN / 中文
  styles/        # design tokens + global CSS
```

Patient-only UI: free-text → auto-diagnose **or** manual HPO pick → clue report.
