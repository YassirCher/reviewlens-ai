# ReviewLens Backend

FastAPI backend for YouTube discovery, transcript extraction, optional comment retrieval, structured AI review analysis, and final consensus.

## Run

From the project root create `.env`, then:

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

API docs: `http://localhost:8000/docs`

Main endpoints:
- `GET /health`
- `GET /api/config`
- `POST /api/analyze`
- `POST /api/analyze/stream` (Server-Sent Events)
