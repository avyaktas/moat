# owns endpoints

from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from models import Company, Financials
from database import get_db
from metrics import debt_to_equity, fcf_margin, net_margin, roe

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "Hello from Moat"}

@app.get("/health")
def read_health():
    return {"status": "ok"}

@app.get("/companies")
def list_companies(db: Session = Depends(get_db)):
    rows = db.query(Company).all()
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

@app.get("/company/{ticker}/financials")
def get_financials(ticker: str, db: Session = Depends(get_db)):
    ticker = ticker.upper()
    company = db.query(Company).filter(Company.ticker == ticker).first()
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    rows = (
        db.query(Financials)
        .filter(Financials.company_id == company.id)
        .order_by(Financials.period_end.desc())
        .all()
    )
    return [
    {
        "period_end": r.period_end,
        "revenue": r.revenue,
        "net_income": r.net_income,
        "free_cash_flow": r.free_cash_flow,
        "total_debt": r.total_debt,
        "shareholders_equity": r.shareholders_equity,
    }
    for r in rows
]

@app.get("/company/{ticker}/metrics")
def get_metrics(ticker: str, db: Session = Depends(get_db)):
    ticker = ticker.upper()
    company = db.query(Company).filter(Company.ticker == ticker).first()
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")

    rows = (
        db.query(Financials)
        .filter(Financials.company_id == company.id)
        .order_by(Financials.period_end.desc())
        .all()
    )

    return [
        {
            "period_end": r.period_end,
            "net_margin": net_margin(r.revenue, r.net_income),
            "fcf_margin": fcf_margin(r.revenue, r.free_cash_flow),
            "roe": roe(r.net_income, r.shareholders_equity),
            "debt_to_equity": debt_to_equity(r.total_debt, r.shareholders_equity),
        }
        for r in rows
    ]
