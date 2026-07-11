from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "Hello from Moat"}

@app.get("/health")
def read_health():
    return {"status": "ok"}

@app.get("/company/{ticker}")
def read_company(ticker: str):
    return {"ticker": ticker, "message": f"You asked about {ticker}"}