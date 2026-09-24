"""Time one uncached report end to end, stage by stage.

Searching a ticker Moat has not seen is the slowest thing the application
does, and before this existed nothing said where the time went. Run it against
a scratch database so the ticker really is cold:

    createdb moat_perf
    DATABASE_URL=postgresql+psycopg://USER@localhost:5432/moat_perf \
        alembic upgrade head
    DATABASE_URL=postgresql+psycopg://USER@localhost:5432/moat_perf \
        python scripts/measure_cold_report.py NVDA

Costs one or two model calls. Pass --keep to leave the company in place; by
default the company, its financials and any cached report are deleted first so
the run is genuinely cold.
"""

import argparse
import logging
import sys
import time

sys.path.insert(0, ".")

from fastapi.testclient import TestClient  # noqa: E402

from database import SessionLocal  # noqa: E402
from main import app  # noqa: E402
from models import Company, Financials, Report  # noqa: E402


def make_cold(ticker: str) -> None:
    """Remove every trace of this ticker so the next request rebuilds it."""
    db = SessionLocal()
    try:
        company = db.query(Company).filter(Company.ticker == ticker.upper()).first()
        if company is None:
            return
        db.query(Report).filter(Report.company_id == company.id).delete()
        db.query(Financials).filter(Financials.company_id == company.id).delete()
        db.delete(company)
        db.commit()
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticker", nargs="?", default="NVDA")
    parser.add_argument("--keep", action="store_true",
                        help="do not clear the ticker first (measures a warm path)")
    args = parser.parse_args()

    logging.getLogger().setLevel(logging.INFO)
    ticker = args.ticker.upper()
    if not args.keep:
        make_cold(ticker)

    client = TestClient(app)
    started = time.perf_counter()
    resp = client.get(f"/company/{ticker}/report")
    wall = time.perf_counter() - started

    body = resp.json() if resp.status_code == 200 else {}
    narrative = body.get("narrative")
    print()
    print(f"=== {ticker}: uncached /report ===")
    print(f"  HTTP {resp.status_code}    wall clock {wall:.2f}s")
    if narrative:
        print(f"  narrative present: verdict={narrative.get('verdict')} "
              f"grounding={narrative.get('grounding_rate')}")
    else:
        print("  narrative ABSENT - report degraded to computed figures only")
    # The stage breakdown itself is logged by timing.track at INFO.
    return 0 if resp.status_code == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
