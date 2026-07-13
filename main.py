# owns endpoints

from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session
from models import Company
from database import get_db

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "Hello from Moat"}

@app.get("/health")
def read_health():
    return {"status": "ok"}

@app.get("/companies")
def list_companies(db: Session = Depends(get_db)):
    rows = db.execute(text("SELECT id, ticker, name, sector FROM companies")).all()
    return [
        {"id": r.id, "ticker": r.ticker, "name": r.name, "sector": r.sector}
        for r in rows
    ]

@app.get("/company/{ticker}")
def get_ticker(ticker: str, db: Session = Depends(get_db)):
    ticker = ticker.upper()
    row = db.query(Company).filter(Company.ticker == ticker).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return {"id": row.id, "ticker": row.ticker, "name": row.name, "sector": row.sector}

