# Moat

Type in any US-listed ticker and get back a full analyst report on the company:
computed financials from their SEC filings, a scorecard against value investing
criteria, the real risks pulled from their 10-K, and a verdict on whether the
business is worth owning.

**Live:** https://moat-production-a6c2.up.railway.app/view

Every claim in the analysis comes with a quote from the filing, and the code
checks that the quote is actually there.

> **Why "Moat"?** An economic moat is a durable competitive advantage that keeps
> competitors from eating into a company's profits. Sustained high returns on
> invested capital are the tell. In a market with no barriers, money floods
> toward high returns and drags them back to average, so a company earning 27% on
> capital year after year has something protecting it. The scorecard's main check
> is basically a moat detector.


```
GET /company/MSFT/report
```

## What you get

Numbers computed from filed data, never from the model:

```json
"ttm": {
  "revenue": 318273000000, "net_income": 125216000000,
  "net_margin": 0.393, "roic": 0.275, "revenue_growth": 0.179
}
```

A scorecard against explicit thresholds:

```
ROIC              PASS   TTM ROIC 27.5% vs threshold 15%
Net margin        PASS   TTM net margin 39.3% vs floor 10%
Margin stability  PASS   Margin range 6.1% across 5 periods (max 15%)
FCF conversion    FAIL   TTM FCF is 0.58x net income (floor 0.80x)
Leverage          PASS   Debt/equity 0.10 - conservative
Rule of 40        PASS   Growth 17.9% + FCF margin 22.9% = 40.8
```

A verdict that says what would change it:

> **WATCH-CASE.** At a P/E of 23.4x and P/FCF of over 40x, the market is already
> pricing in continued strong execution. Track FCF conversion each quarter. This
> becomes a BUY-CASE if conversion trends back toward 0.80x, which would confirm
> the current capex is a reinvestment cycle rather than a permanent drag.

And risks with sell triggers, each quote checked against the 10-K:

> **Risk:** Heavy AI and cloud capex is compressing free cash flow relative to net
> income.
> **Quote (verified):** *"We are incurring significant costs to build and maintain
> infrastructure to support cloud-based and AI services, reducing operating margins."*
> **Sell trigger:** FCF conversion stays well below 0.80x for multiple years with
> no acceleration in revenue growth.

## How I know it works

Most LLM projects can't answer that question. This one has an evaluation harness:
24 questions across three categories, including traps where the model knows the
answer from training but the document doesn't contain it.

For example, Microsoft's risk factors never name a single competitor. Ask "does
the filing name Google or Amazon?" and a system running on world knowledge says
yes. The right answer is no.

Every quote gets string-matched against the source, so fabrication is caught
mechanically. No LLM grading another LLM.

The harness has caught three real problems, and none of them were the model:

**My answer key was wrong.** A question I'd marked "should abstain" failed. The
model had correctly found Microsoft's $28.9B IRS transfer pricing contingency,
which proved my assumption that risk factors are purely qualitative was false.

**My grounding checker had false positives.** Report grounding sat at 60%. I
bisected a supposedly fabricated quote character by character and found the whole
failure was one trailing period, where the model closed a sentence that the filing
continued. Forgiving terminal punctuation while staying strict about everything
else took grounding from 60% to 100%.

**The metric isn't deterministic.** Re-running the same question gives different
quotes. Grounding rate is a sample, not a fixed property, so the honest number is a
range across runs.

## Architecture

```
                    SEC EDGAR                     Market data
         ┌──────────────┴──────────────┐               │
  companyfacts API              submissions +      yfinance
  (XBRL numbers)                filing HTML       (price only)
         │                              │               │
   ingest.py                       filings.py       prices.py
 - ticker → CIK                - 8MB HTML → 69K
 - Q4 derivation               - Item 1A extraction
 - tag fallbacks
         │                              │
         ▼                              ▼
   PostgreSQL                      analysis.py
 companies · financials        - grounded prompting
 briefs · reports              - quote verification
         │                              │
   metrics.py → scoring.py              │
 - null-safe ratios  - 6 checks         │
 - TTM aggregation   - PASS/FAIL/UNKNOWN│
         └──────────────┬───────────────┘
                        ▼
                    report.py
        computed figures + filing → LLM synthesis
                        ▼
                     FastAPI
```

Plus Alembic migrations, environment-based config, ~90 tests, Docker Compose, and
GitHub Actions CI.

## Some decisions worth explaining

**Missing data is never zero.** It's `None` all the way through: nullable columns,
ratios that return nothing when inputs are absent, an UNKNOWN state on the
scorecard that's separate from FAIL, and a model required to say "not addressed."
A made-up number looks exactly like a real one downstream. A null doesn't.

**The model never does arithmetic.** Every figure is computed in code before the
LLM is called, and the prompt tells it not to calculate. If you hand a model raw
filings and ask for ROIC, you get a plausible number with no provenance. Math
belongs in code. Judgment about what the math means is what the model is for.

**No embeddings for this part.** 10-K sections are required by regulation, so Item
1A is always the risk factors. I slice it directly instead of embedding 8MB and
hoping vector search lands on the right passage. That's a 99% context reduction and
there's no retrieval step to go wrong. Embeddings make sense for free-form
questions across a whole filing, which is the next layer.

**Every verdict names what would change it.** A rating with no falsification
condition is a horoscope.

## Endpoints

| Endpoint | Returns |
|---|---|
| `/company/{ticker}/report` | Full analysis with verdict |
| `/company/{ticker}/metrics` | Quarterly ratios and TTM aggregates |
| `/company/{ticker}/brief` | Grounded answer to any question about the 10-K |
| `/company/{ticker}/financials` | Raw quarterly data |
| `/company/{ticker}` | Company record |
| `/companies` | Everything ingested so far |

A ticker you've never requested gets ingested live and served instantly after
that. Reports cache for 7 days (they include a live price) with `?refresh=true` to
force a rebuild. Briefs cache forever, since the 10-K they analyze doesn't change.

## Running it

```bash
docker compose up --build
docker compose exec api alembic upgrade head
docker compose exec api python ingest.py MSFT
# http://localhost:8000/docs
```

Put an `ANTHROPIC_API_KEY` in `.env` for the AI endpoints. See `.env.example`.

<details>
<summary>Without Docker</summary>

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
createdb moat && createdb moat_test
cp .env.example .env
alembic upgrade head
python ingest.py MSFT
uvicorn main:app --reload
```

Tests: `pytest`. Uses an isolated test database and mocks the LLM and SEC calls.
</details>

## The data problems that took the longest

**Q4 doesn't exist.** Companies don't file a standalone fourth quarter. It's buried
inside the annual figure. The clue was that income statement items were missing
while balance sheet items were fine, which is the difference between activity over
a period and a balance at a moment. So I split the tags accordingly, found annual
entries by their ~365 day duration (calendar frames don't work for companies whose
fiscal year isn't the calendar year), and computed Q4 = FY − (Q1+Q2+Q3). Only when
all three other quarters exist. No estimating. Checked it against the real number:
Microsoft's FY2025 Q4 revenue is $76.4B.

**Accounting standards change.** Revenue lives under a different GAAP tag before
and after ASC 606, so anything before ~2016 came back empty. Each metric now maps
to an ordered list of candidate tags, preferring the modern one and falling back
for history.

**Re-running has to be safe.** Ingestion upserts on a composite unique constraint,
enforced in both the code and the database, so running it again refreshes rows
instead of duplicating them.

## Tech

Python 3.12, FastAPI, PostgreSQL 16, SQLAlchemy 2, Alembic, Anthropic API,
BeautifulSoup, Docker, pytest, GitHub Actions.

## What it doesn't do

- **Banks and insurers.** They file under a different GAAP taxonomy (interest
  income instead of revenue, deposits instead of debt). Rather than force it, the
  pipeline reports honest gaps. About 85% of the S&P 500 is non-financial.
- **Companies that report cash flow cumulatively.** Apple reports year-to-date
  within its fiscal year, so only fiscal Q1 has a standalone figure. Fixing it
  means differencing consecutive periods, same idea as the Q4 derivation.
- **Proper ROIC.** Mine uses TTM net income over gross debt plus equity. The real
  version uses NOPAT and nets out excess cash. Directionally right, noted in the
  code.
- **Real time anything.** Data updates when companies file, which is quarterly.
  That's fine for this kind of analysis.
- **Tell you what to buy.** It's a screen. Valuation multiples get reported without
  a threshold attached, because deciding what counts as expensive takes judgment
  the system doesn't have.

## Roadmap

- [x] EDGAR ingestion for any US company, on demand
- [x] Value metrics: margins, ROE, D/E, TTM, ROIC
- [x] 10-K extraction and grounded analysis
- [x] Evaluation harness with hallucination traps
- [x] Scoring framework and full report
- [x] Caching with per-endpoint invalidation
- [ ] Vector search for free-form questions (chunking and embeddings are built and
      tested, pgvector storage is not)
- [x] Deployed somewhere
- [ ] Ranking across a universe of companies