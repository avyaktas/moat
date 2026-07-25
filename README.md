# Moat

An LLM-powered financial research platform. Give it any US-listed ticker and it
ingests the company's SEC filings, computes value-investing metrics, and
generates **grounding-verified** analyses of the company's 10-K — every claim
backed by a quote checked against the source document.

Built from scratch as a learning project.
---

## What makes it different is that it is measured

Most LLM projects can't answer "how do you know it works?" This one can, with a
number. The analysis layer ships with an **evaluation harness**:

- **24 questions** across three categories — answerable, absent, and specific —
  including deliberate **hallucination traps**: questions the model knows the
  answer to from training (does the filing name Google as a competitor? the EU
  Digital Markets Act?) but that the document does **not** contain.
- **Every quote the model returns is verified** by string-matching it against
  the source filing. A fabricated quote is detected mechanically — no LLM judge,
  no subjectivity.

Current results on Microsoft's FY2025 10-K:

| Metric | Result |
|--------|--------|
| Grounding rate (quotes found in source) | **100%** |
| Hallucinations (absent questions answered anyway) | **0** |
| Abstention accuracy | **100%** |
| Answer correctness (answerable/specific) | **100%** |

Building the eval also caught a bug — in the eval itself: a question keyed as
"should abstain" was wrong, because the model correctly surfaced Microsoft's
$28.9B IRS transfer-pricing contingency, disproving the assumption that risk
factors are purely qualitative. The failing test indicted the answer key, not
the model.

---

## Example

`GET /company/MSFT/brief`

```json
{
  "addressed": true,
  "answer": "The filing organizes its risk factors into major categories: intense
             competition; cloud and AI execution risk; cybersecurity (including a
             nation-state attack); regulatory, antitrust, and tax disputes; IP;
             and general risks including talent retention...",
  "quotes": [
    "We face intense competition across all markets for our products and services...",
    "beginning in late November 2023, a nation-state associated threat actor used
     a password spray attack to compromise a legacy test account...",
    "In the NOPAs, the IRS is seeking an additional tax payment of $28.9 billion
     plus penalties and interest."
  ],
  "grounding_rate": 1.0,
  "filing_url": "https://www.sec.gov/Archives/edgar/data/789019/...",
  "report_date": "2025-06-30"
}
```

`GET /company/MSFT/metrics` returns computed value metrics (TTM ROIC 27.5%, net
margin 39%, etc.) from primary-source EDGAR data.

---

## Architecture

```
                         SEC EDGAR
              ┌──────────────┴──────────────┐
       companyfacts API              submissions + filing HTML
              │                              │
        ingest.py                       filings.py
     (numbers → Postgres)         (8MB HTML → 69K risk section)
              │                              │
        metrics.py                      analysis.py
   (ROIC, margins, TTM)          (grounded LLM answer + quote check)
              │                              │
              └──────────────┬───────────────┘
                        FastAPI
        /company/{ticker}/metrics   /company/{ticker}/brief
                             │
                 PostgreSQL (Alembic migrations)
          companies · financials · briefs (LLM cache)

  Cross-cutting: on-demand ingestion (read-through cache) ·
  Docker Compose · GitHub Actions CI · pytest · evaluation harness
```

## The AI layer, in depth

- **Section extraction over embeddings.** A 10-K is ~8MB of inline-XBRL HTML
  (~2M tokens). Because 10-K structure is mandated by regulation (Item 1A is
  always Risk Factors), the system slices out the relevant section directly —
  ~69K chars, a 99% reduction — rather than embedding the whole document and
  hoping vector similarity retrieves the right passages. Simpler and more
  reliable for section-targeted questions. (Free-form whole-document Q&A, where
  vector retrieval earns its place, is the planned next layer.)
- **Grounding by construction.** The prompt forbids outside knowledge and
  requires verbatim quotes; the code then verifies each quote against the source
  with whitespace-insensitive matching (filer HTML splits words across tags).
- **Honest abstention.** When the document doesn't address a question, the
  correct answer is "not addressed" — the same principle as the honest NULLs in
  the metrics layer. Unknown is a real answer.
- **Cached per (company, question)** via Postgres upsert, so repeat requests are
  instant and concurrent requests don't collide.

## Data engineering highlights

- **Q4 recovery** — companies file no standalone Q4; it's derived as
  FY − (Q1+Q2+Q3) from the annual figure.
- **Tag fallbacks across accounting eras** — revenue lives under different GAAP
  tags before/after ASC 606; the pipeline tries ordered candidates.
- **Idempotent, upsert-based ingestion** — re-running refreshes rows without
  duplicates, enforced by composite unique constraints.
- **Honest nulls everywhere** — missing data is never zero or estimated.

---

## Quickstart (Docker)

```bash
docker compose up --build
docker compose exec api alembic upgrade head
docker compose exec api python ingest.py MSFT
# open http://localhost:8000/docs
```

Set `ANTHROPIC_API_KEY` in `.env` for the `/brief` endpoint (see `.env.example`).

## Tech

Python 3.12 · FastAPI · PostgreSQL 16 · SQLAlchemy 2 · Alembic · Anthropic API ·
BeautifulSoup · Docker + Compose · pytest · GitHub Actions CI

## Scope & honest limitations

- **Non-financial companies** — banks use a different GAAP taxonomy; the pipeline
  reports honest gaps rather than misleading ratios.
- **Cumulative cash-flow filers** (e.g. Apple) need Q2/Q3 differencing — planned.
- **ROIC is simplified** — TTM net income over gross debt + equity, not NOPAT.
- **Section-targeted analysis** — the AI layer answers questions about specific
  10-K sections; whole-document free-form Q&A (vector retrieval) is next.

## Roadmap

- [x] EDGAR ingestion, any US-listed company, on-demand
- [x] Value metrics: margins, ROE, D/E, TTM, ROIC
- [x] Docker, CI, migrations, isolated tests
- [x] 10-K section extraction
- [x] Grounded LLM analysis with citation verification
- [x] Evaluation harness (grounding, abstention, hallucination traps)
- [ ] Vector search for free-form whole-document Q&A (pgvector)
- [ ] ML ranking layer across a company universe
- [ ] Deployed instance