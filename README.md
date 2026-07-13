# Moat

A value-investing intelligence platform: automated fundamental analysis,
value metrics, ML ranking, and LLM-powered filing analysis for any ticker.

## Status
Early build — FastAPI + PostgreSQL foundation with Alembic migrations.

## Setup
```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn main:app --reload
```