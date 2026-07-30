"""Render a report as an HTML tearsheet.

The JSON from /report is complete but unreadable - a wall of unformatted
numbers. This module turns the same data into something a person would
actually read: a research note.

No template engine. The report has one fixed shape, so building the HTML
in Python keeps it dependency-free and keeps the formatting logic (which
is most of the work) next to the markup it feeds.

FORMATTING IS THE POINT
    318273000000.0 is data. $318.3B is information. Every number here goes
    through a formatter that picks a scale, and every ratio becomes a
    percentage or a multiple. Nulls render as an em-dash rather than
    "None", because the reader should see an absence, not a Python value.
"""

import html


# ---------------------------------------------------------------- shared shell
#
# One source of truth for the look. The tearsheet, the landing page, and the
# 404 page all pull the same fonts and the same palette from here, so the
# design can't drift between them.

_FONTS = """<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">"""

# Design tokens + base reset. Plain string (single braces): it is inserted into
# the report's f-string and into _document() without needing brace-doubling.
_TOKENS = """
  :root {
    --ink:      #16232B;
    --ink-soft: #4A5A63;
    --paper:    #F7F5F0;
    --rule:     #D8D3C8;
    --hold:     #2F6F5E;
    --breach:   #B4462F;
    --unknown:  #9A958A;
    --measure:  34rem;
  }
  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; }
  body {
    margin: 0;
    background: var(--paper);
    color: var(--ink);
    font-family: 'Inter', -apple-system, system-ui, sans-serif;
    font-size: 16px;
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
    text-rendering: optimizeLegibility;
  }
  a { color: inherit; }
  ::selection { background: var(--ink); color: var(--paper); }
"""


def _document(title: str, body: str, css: str = "") -> str:
    """Wrap page body in the shared HTML shell (doctype, head, fonts, tokens).

    `title` and any dynamic content in `body` must be pre-escaped by the caller.
    """
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
{_FONTS}
<style>{_TOKENS}{css}</style>
</head>
<body>
{body}
</body>
</html>"""


# ---------------------------------------------------------------- formatting


def money(v: float | None) -> str:
    """Large dollar figures at human scale: $318.3B, $46.2M, -$1.2B."""
    if v is None:
        return "—"
    v = float(v)
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1e12:
        return f"{sign}${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"{sign}${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"{sign}${v / 1e6:.1f}M"
    return f"{sign}${v:,.0f}"


def pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{float(v) * 100:.1f}%"


def mult(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{float(v):.1f}x"


def num(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{float(v):.2f}"


def esc(s) -> str:
    return html.escape(str(s)) if s is not None else ""


# ---------------------------------------------------------------- components


def _wall(checks: list[dict]) -> str:
    """The signature element: the scorecard as a wall.

    Six blocks, one per check. Solid where the business holds, breached
    where it fails, hatched where the data won't say. Readable before a
    single word of the report.
    """
    blocks = []
    for c in checks:
        state = {"PASS": "hold", "FAIL": "breach", "UNKNOWN": "unknown"}[c["status"]]
        blocks.append(
            f'<div class="block {state}" title="{esc(c["name"])}: {esc(c["detail"])}">'
            f'<span class="block-label">{esc(c["name"])}</span></div>'
        )
    return f'<div class="wall">{"".join(blocks)}</div>'


def _checks_table(checks: list[dict]) -> str:
    rows = []
    for c in checks:
        state = {"PASS": "hold", "FAIL": "breach", "UNKNOWN": "unknown"}[c["status"]]
        rows.append(
            f'<tr class="{state}">'
            f'<td class="check-mark"></td>'
            f'<td class="check-name">{esc(c["name"])}</td>'
            f'<td class="check-detail">{esc(c["detail"])}</td>'
            f"</tr>"
        )
    return f'<table class="checks">{"".join(rows)}</table>'


def _figures(ttm: dict, valuation: dict, price: dict | None) -> str:
    items = [
        ("Revenue (TTM)", money(ttm.get("revenue"))),
        ("Net income (TTM)", money(ttm.get("net_income"))),
        ("Free cash flow (TTM)", money(ttm.get("free_cash_flow"))),
        ("Net margin", pct(ttm.get("net_margin"))),
        ("FCF margin", pct(ttm.get("fcf_margin"))),
        ("Revenue growth", pct(ttm.get("revenue_growth"))),
        ("ROIC", pct(ttm.get("roic"))),
        ("ROE", pct(ttm.get("roe"))),
        ("Market cap", money(valuation.get("market_cap"))),
        ("P / FCF", mult(valuation.get("p_fcf"))),
        ("P / E", mult(valuation.get("p_e"))),
        ("Share price",
         f"${float(price['price']):,.2f}" if price and price.get("price") else "—"),
    ]
    cells = "".join(
        f'<div class="fig"><span class="fig-label">{esc(k)}</span>'
        f'<span class="fig-value">{esc(v)}</span></div>'
        for k, v in items
    )
    return f'<div class="figures">{cells}</div>'


def _health(health: dict) -> str:
    labels = {
        "cash": "Cash",
        "short_term_investments": "Short-term investments",
        "total_debt": "Total debt",
        "free_cash_flow": "Free cash flow (quarter)",
        "shareholders_equity": "Shareholders' equity",
    }
    rows = []
    for key, label in labels.items():
        row = health.get(key, {})
        change = row.get("change")
        # Coerce like the formatters do: a report cached by the old
        # json.dumps(default=str) serializer stores numbers as strings, and
        # "1234" > 0 raises TypeError.
        change = float(change) if change is not None else None
        direction = ""
        if change:
            direction = "up" if change > 0 else "down"
        rows.append(
            f"<tr>"
            f"<td>{esc(label)}</td>"
            f'<td class="n">{money(row.get("prior"))}</td>'
            f'<td class="n">{money(row.get("current"))}</td>'
            f'<td class="n {direction}">{money(change) if change else "—"}</td>'
            f"</tr>"
        )
    surv = health.get("survivability", {})
    return f"""
      <table class="health">
        <thead><tr><th></th><th class="n">Prior qtr</th>
        <th class="n">Latest qtr</th><th class="n">Change</th></tr></thead>
        <tbody>{"".join(rows)}</tbody>
      </table>
      <p class="survivability">{esc(surv.get("verdict", ""))}</p>
    """


def _risks(risks: list[dict]) -> str:
    if not risks:
        return '<p class="empty">No risk analysis available for this filing.</p>'
    out = []
    for r in risks:
        verified = r.get("quote_verified")
        mark = (
            '<span class="verified">Quote verified against filing</span>'
            if verified
            else '<span class="unverified">Quote not found in filing</span>'
        )
        out.append(
            f"""
            <article class="risk">
              <h3>{esc(r.get("risk"))}</h3>
              <blockquote>{esc(r.get("quote"))}</blockquote>
              {mark}
              <p class="trigger"><span class="trigger-label">What would make you sell</span>
                 {esc(r.get("sell_trigger"))}</p>
            </article>
            """
        )
    return "".join(out)


def _paragraphs(text: str | None) -> str:
    if not text:
        return ""
    parts = [p.strip() for p in text.split("\n") if p.strip()]
    return "".join(f"<p>{esc(p)}</p>" for p in parts)


# ---------------------------------------------------------------- the page


def render_report(report: dict) -> str:
    data = report.get("data", {})
    ttm = data.get("ttm", {})
    scorecard = data.get("scorecard", {})
    checks = scorecard.get("checks", [])
    summary = scorecard.get("summary", {})
    narrative = report.get("narrative") or {}
    sources = report.get("sources", {})
    cache = report.get("cache", {})

    verdict = narrative.get("verdict", "NO VERDICT")
    verdict_class = {
        "BUY-CASE": "buy",
        "WATCH-CASE": "watch",
        "AVOID-CASE": "avoid",
    }.get(verdict, "none")

    grounding = narrative.get("grounding_rate")
    grounding_str = f"{float(grounding) * 100:.0f}%" if grounding is not None else "—"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(report.get("company"))} · Moat</title>
{_FONTS}
<style>{_TOKENS}
  .sheet {{ max-width: 62rem; margin: 0 auto; padding: 4rem 2rem 6rem; }}

  /* ---- masthead ---- */
  .masthead {{
    display: flex; align-items: flex-end; justify-content: space-between;
    gap: 2rem; flex-wrap: wrap;
    padding-bottom: 1.25rem; border-bottom: 1px solid var(--ink);
  }}
  .eyebrow {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.7rem; letter-spacing: 0.14em; text-transform: uppercase;
    color: var(--ink-soft); margin-bottom: 0.5rem;
  }}
  .eyebrow a {{ text-decoration: none; border-bottom: 1px solid var(--rule);
                padding-bottom: 1px; }}
  .eyebrow a:hover {{ border-color: var(--ink-soft); color: var(--ink); }}
  .ticker {{
    font-family: 'Instrument Serif', Georgia, serif;
    font-size: clamp(3rem, 9vw, 5.5rem); line-height: 0.9;
    letter-spacing: -0.01em; margin: 0;
  }}
  .company-name {{
    font-size: 0.95rem; color: var(--ink-soft); margin: 0.6rem 0 0;
  }}
  .verdict {{
    font-family: 'Instrument Serif', Georgia, serif;
    font-size: clamp(1.5rem, 4vw, 2.25rem); line-height: 1;
    padding: 0.5rem 0 0.5rem 1.25rem; border-left: 3px solid currentColor;
  }}
  .verdict.buy    {{ color: var(--hold); }}
  .verdict.watch  {{ color: var(--ink); }}
  .verdict.avoid  {{ color: var(--breach); }}
  .verdict small {{
    display: block; font-family: 'JetBrains Mono', monospace;
    font-size: 0.65rem; letter-spacing: 0.14em; text-transform: uppercase;
    color: var(--ink-soft); margin-bottom: 0.35rem;
  }}

  /* ---- the wall: signature element ---- */
  .wall {{
    display: grid; grid-template-columns: repeat(6, 1fr);
    gap: 4px; margin: 2.5rem 0 0.75rem; height: 7rem;
  }}
  .block {{
    position: relative; border: 1.5px solid var(--ink);
    display: flex; align-items: flex-end;
  }}
  .block.hold    {{ background: var(--ink); }}
  .block.breach  {{ background: transparent; border-color: var(--breach); }}
  .block.unknown {{
    border-color: var(--unknown);
    background: repeating-linear-gradient(45deg,
      transparent, transparent 5px, var(--rule) 5px, var(--rule) 6px);
  }}
  .block-label {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.6rem; letter-spacing: 0.08em; text-transform: uppercase;
    padding: 0.5rem; line-height: 1.2;
  }}
  .block.hold .block-label   {{ color: var(--paper); }}
  .block.breach .block-label {{ color: var(--breach); }}
  .wall-caption {{
    font-family: 'JetBrains Mono', monospace; font-size: 0.7rem;
    letter-spacing: 0.08em; color: var(--ink-soft); text-transform: uppercase;
  }}

  /* ---- sections ---- */
  section {{ margin-top: 3.5rem; }}
  h2 {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.72rem; letter-spacing: 0.16em; text-transform: uppercase;
    font-weight: 500; color: var(--ink-soft);
    padding-bottom: 0.6rem; border-bottom: 1px solid var(--rule);
    margin: 0 0 1.5rem;
  }}

  /* ---- checks ---- */
  table.checks {{ width: 100%; border-collapse: collapse; }}
  table.checks td {{ padding: 0.7rem 0; border-bottom: 1px solid var(--rule);
                     vertical-align: baseline; }}
  .check-mark {{ width: 1.5rem; }}
  .check-mark::before {{
    content: ''; display: block; width: 9px; height: 9px; border: 1.5px solid;
  }}
  tr.hold    .check-mark::before {{ background: var(--hold); border-color: var(--hold); }}
  tr.breach  .check-mark::before {{ background: transparent; border-color: var(--breach); }}
  tr.unknown .check-mark::before {{ background: var(--rule); border-color: var(--unknown); }}
  .check-name {{ width: 12rem; font-weight: 500; }}
  .check-detail {{ font-family: 'JetBrains Mono', monospace; font-size: 0.82rem;
                   color: var(--ink-soft); }}
  tr.breach .check-detail {{ color: var(--breach); }}

  /* ---- figures ---- */
  .figures {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
    gap: 1px; background: var(--rule); border: 1px solid var(--rule);
  }}
  .fig {{ background: var(--paper); padding: 1rem 1.1rem;
          transition: background 150ms ease; }}
  .fig:hover {{ background: #FCFBF7; }}
  .fig-label {{
    display: block; font-size: 0.72rem; color: var(--ink-soft);
    margin-bottom: 0.35rem;
  }}
  .fig-value {{
    display: block; font-family: 'JetBrains Mono', monospace;
    font-size: 1.15rem; font-variant-numeric: tabular-nums;
  }}

  /* ---- health ---- */
  table.health {{ width: 100%; border-collapse: collapse;
                  font-family: 'JetBrains Mono', monospace; font-size: 0.85rem; }}
  table.health th {{
    font-family: 'Inter', sans-serif; font-size: 0.72rem; font-weight: 500;
    color: var(--ink-soft); text-align: left; padding-bottom: 0.6rem;
    border-bottom: 1px solid var(--ink);
  }}
  table.health td {{ padding: 0.6rem 0; border-bottom: 1px solid var(--rule); }}
  table.health td:first-child {{ font-family: 'Inter', sans-serif; }}
  .n {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .up   {{ color: var(--hold); }}
  .down {{ color: var(--breach); }}
  .survivability {{
    margin-top: 1rem; font-size: 0.9rem; color: var(--ink-soft);
    padding-left: 1rem; border-left: 2px solid var(--rule);
  }}

  /* ---- prose ---- */
  .prose {{ max-width: var(--measure); }}
  .prose p {{ margin: 0 0 1.15rem; }}
  .lede {{ font-size: 1.05rem; }}

  /* ---- risks ---- */
  .risk {{ padding: 1.75rem 0; border-bottom: 1px solid var(--rule); }}
  .risk:first-of-type {{ padding-top: 0; }}
  .risk h3 {{ font-size: 1rem; font-weight: 600; margin: 0 0 0.9rem;
              max-width: var(--measure); }}
  .risk blockquote {{
    margin: 0 0 0.5rem; padding-left: 1.1rem;
    border-left: 2px solid var(--ink); font-size: 0.92rem;
    color: var(--ink-soft); max-width: var(--measure);
  }}
  .verified, .unverified {{
    font-family: 'JetBrains Mono', monospace; font-size: 0.65rem;
    letter-spacing: 0.1em; text-transform: uppercase;
  }}
  .verified   {{ color: var(--hold); }}
  .unverified {{ color: var(--breach); }}
  .trigger {{ margin: 1rem 0 0; font-size: 0.92rem; max-width: var(--measure); }}
  .trigger-label {{
    display: block; font-family: 'JetBrains Mono', monospace;
    font-size: 0.65rem; letter-spacing: 0.1em; text-transform: uppercase;
    color: var(--ink-soft); margin-bottom: 0.3rem;
  }}

  /* ---- footer ---- */
  footer {{
    margin-top: 4rem; padding-top: 1.25rem; border-top: 1px solid var(--ink);
    font-size: 0.78rem; color: var(--ink-soft);
  }}
  footer a {{ color: var(--ink-soft); text-decoration: none;
              border-bottom: 1px solid var(--rule); }}
  footer a:hover {{ color: var(--ink); border-color: var(--ink-soft); }}
  footer p {{ margin: 0.3rem 0; }}
  .disclaimer {{ margin-top: 1.25rem; font-style: italic; }}

  @media (max-width: 40rem) {{
    .sheet {{ padding: 2.5rem 1.25rem 4rem; }}
    .wall {{ height: 5rem; }}
    .block-label {{ font-size: 0.5rem; padding: 0.3rem; }}
    .check-name {{ width: auto; }}
  }}
  @media (prefers-reduced-motion: no-preference) {{
    .block {{ transition: background 200ms ease; }}
  }}
</style>
</head>
<body>
<div class="sheet">

  <header class="masthead">
    <div>
      <p class="eyebrow"><a href="/">Moat</a> · Filing analysis</p>
      <h1 class="ticker">{esc(report.get("company"))}</h1>
      <p class="company-name">{esc(report.get("name"))} &nbsp;·&nbsp;
         Data as of {esc(data.get("as_of"))}</p>
    </div>
    <div class="verdict {verdict_class}">
      <small>Framework verdict</small>
      {esc(verdict)}
    </div>
  </header>

  {_wall(checks)}
  <p class="wall-caption">
    {summary.get("passed", 0)} of {summary.get("evaluable", 0)} criteria hold
    {f'· {summary.get("unknown")} unevaluable' if summary.get("unknown") else ''}
  </p>

  <section>
    <h2>Scorecard</h2>
    {_checks_table(checks)}
  </section>

  <section>
    <h2>Figures</h2>
    {_figures(ttm, scorecard.get("valuation", {}), data.get("price"))}
  </section>

  <section>
    <h2>Financial health</h2>
    {_health(scorecard.get("financial_health", {}))}
  </section>

  <section>
    <h2>Hype versus reality</h2>
    <div class="prose lede">{_paragraphs(narrative.get("hype_vs_reality"))}</div>
  </section>

  <section>
    <h2>Risks and sell triggers</h2>
    {_risks(narrative.get("risks", []))}
  </section>

  <section>
    <h2>The case</h2>
    <div class="prose">{_paragraphs(narrative.get("reasoning"))}</div>
  </section>

  <section>
    <h2>The strategy</h2>
    <div class="prose">{_paragraphs(narrative.get("strategy"))}</div>
  </section>

  <footer>
    <p>Financials from {esc(sources.get("financials"))}.
       Price from {esc(sources.get("price"))}.</p>
    <p>Filing: <a href="{esc(sources.get("filing"))}">10-K filed
       {esc(sources.get("report_date"))}</a> ·
       {grounding_str} of quotes verified against the source document.</p>
    <p>{"Cached" if cache.get("cached") else "Generated"}
       {esc(cache.get("generated_at", ""))[:19].replace("T", " ")} UTC.</p>
    <p class="disclaimer">This is a screen against stated criteria, not
       investment advice. Every figure is computed from filed data; the
       narrative interprets those figures and does not calculate them.</p>
  </footer>

</div>
</body>
</html>"""


# ---------------------------------------------------------------- landing page


def render_landing() -> str:
    """The front door: a wordmark, a line about what this is, and a ticker box
    that sends you straight to a tearsheet. No framework, no build step - the
    only script is a few lines to uppercase the input and build the URL."""
    css = """
      .land { min-height: 100vh; display: flex; flex-direction: column; }
      .land-main { flex: 1; width: 100%; max-width: 46rem;
                   margin: 0 auto; padding: 13vh 2rem 4rem; }
      .land .eyebrow {
        font-family: 'JetBrains Mono', monospace; font-size: 0.72rem;
        letter-spacing: 0.16em; text-transform: uppercase;
        color: var(--ink-soft); margin: 0 0 1.5rem;
      }
      .land h1 {
        font-family: 'Instrument Serif', Georgia, serif; font-weight: 400;
        font-size: clamp(4.5rem, 17vw, 9.5rem); line-height: 0.84;
        letter-spacing: -0.02em; margin: 0;
      }
      .land .lede {
        font-size: 1.15rem; color: var(--ink-soft);
        max-width: 33rem; margin: 1.75rem 0 3rem;
      }
      form.search { display: flex; gap: 0.6rem; max-width: 30rem; }
      form.search input {
        flex: 1; font: inherit; font-size: 1.1rem; color: var(--ink);
        background: transparent; border: none; border-bottom: 2px solid var(--ink);
        padding: 0.65rem 0.2rem; letter-spacing: 0.06em;
      }
      form.search input::placeholder { color: var(--unknown); letter-spacing: 0; }
      form.search input:focus { outline: none; border-color: var(--hold); }
      form.search button {
        font-family: 'JetBrains Mono', monospace; font-size: 0.72rem;
        letter-spacing: 0.14em; text-transform: uppercase;
        color: var(--paper); background: var(--ink); border: none;
        padding: 0 1.5rem; cursor: pointer; transition: background 150ms ease;
      }
      form.search button:hover { background: var(--hold); }
      .examples {
        margin-top: 1.9rem; font-family: 'JetBrains Mono', monospace;
        font-size: 0.75rem; letter-spacing: 0.06em; color: var(--ink-soft);
      }
      .examples a {
        text-decoration: none; border-bottom: 1px solid var(--rule);
        padding-bottom: 1px; margin-left: 0.85rem;
      }
      .examples a:hover { border-color: var(--ink); color: var(--ink); }
      .land footer {
        width: 100%; max-width: 46rem; margin: 0 auto;
        padding: 1.5rem 2rem 3rem; border-top: 1px solid var(--rule);
        font-size: 0.78rem; color: var(--ink-soft);
      }
      .land footer p { margin: 0.3rem 0; }
      .land footer .disclaimer { font-style: italic; margin-top: 0.6rem; }
      @media (max-width: 40rem) {
        .land-main { padding: 8vh 1.25rem 3rem; }
        form.search { flex-wrap: wrap; }
        form.search button { padding: 0.7rem 1.5rem; }
      }
    """
    body = """
    <div class="land">
      <main class="land-main">
        <p class="eyebrow">Moat · Filing analysis</p>
        <h1>Moat</h1>
        <p class="lede">Type any US-listed ticker for a grounded analyst report:
           computed financials from SEC filings, a scorecard against value-investing
           criteria, and the real risks pulled from the 10-K &mdash; every claim
           checked against the source document.</p>
        <form class="search" role="search" onsubmit="return moatGo(event)">
          <input id="t" name="t" placeholder="Ticker &mdash; e.g. MSFT"
                 aria-label="Ticker" autocomplete="off" autocapitalize="characters"
                 autocorrect="off" spellcheck="false">
          <button type="submit">Analyze</button>
        </form>
        <p class="examples">Try
          <a href="/company/MSFT/report/view">MSFT</a>
          <a href="/company/AAPL/report/view">AAPL</a>
          <a href="/company/NVDA/report/view">NVDA</a>
          <a href="/company/IBM/report/view">IBM</a>
        </p>
      </main>
      <footer>
        <p>Fundamentals from SEC EDGAR &middot; price from yfinance.</p>
        <p class="disclaimer">A screen against stated value-investing criteria,
           not investment advice.</p>
      </footer>
    </div>
    <script>
      var inp = document.getElementById('t');
      inp.addEventListener('input', function () { inp.value = inp.value.toUpperCase(); });
      function moatGo(e) {
        e.preventDefault();
        var t = inp.value.trim().toUpperCase().replace(/[^A-Z0-9.-]/g, '');
        if (!t) { inp.focus(); return false; }
        window.location.href = '/company/' + encodeURIComponent(t) + '/report/view';
        return false;
      }
    </script>
    """
    return _document("Moat · Filing analysis", body, css)


def render_not_found(detail: str) -> str:
    """A small, on-brand 404 for /company/* misses (bad ticker, no filing),
    instead of a raw JSON error, with a way back to the search box."""
    css = """
      .nf { min-height: 100vh; display: flex; align-items: center;
            justify-content: center; text-align: center; padding: 2rem; }
      .nf-inner { max-width: 34rem; }
      .nf .eyebrow {
        font-family: 'JetBrains Mono', monospace; font-size: 0.72rem;
        letter-spacing: 0.16em; text-transform: uppercase;
        color: var(--ink-soft); margin: 0 0 1rem;
      }
      .nf h1 {
        font-family: 'Instrument Serif', Georgia, serif; font-weight: 400;
        font-size: clamp(3rem, 11vw, 5rem); line-height: 0.9;
        margin: 0 0 1.1rem; color: var(--breach);
      }
      .nf .detail {
        font-family: 'JetBrains Mono', monospace; font-size: 0.9rem;
        color: var(--ink); margin: 0 0 2.25rem;
      }
      .nf a.back {
        font-family: 'JetBrains Mono', monospace; font-size: 0.72rem;
        letter-spacing: 0.14em; text-transform: uppercase; text-decoration: none;
        color: var(--paper); background: var(--ink); padding: 0.75rem 1.4rem;
        display: inline-block; transition: background 150ms ease;
      }
      .nf a.back:hover { background: var(--hold); }
    """
    body = f"""
    <div class="nf"><div class="nf-inner">
      <p class="eyebrow">Moat</p>
      <h1>Not found</h1>
      <p class="detail">{esc(detail)}</p>
      <a class="back" href="/">&larr; Back to search</a>
    </div></div>
    """
    return _document("Not found · Moat", body, css)