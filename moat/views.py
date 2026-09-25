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
import json

# ---------------------------------------------------------------- shared shell
#
# One source of truth for the look. The tearsheet, the landing page, and the
# 404 page all pull the same fonts and the same palette from here, so the
# design can't drift between them.

_FONTS = """<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="preload" as="style"
      href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap">
<link rel="stylesheet"
      href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap">"""

# Design tokens. A plain string (single braces): it is inserted into the
# report's f-string and into _document() without needing brace-doubling.
#
# ONE TYPEFACE
#
#     The page used three - a display serif for the ticker, a sans for prose,
#     a monospace for every number. Three families is three font loads, three
#     rendering behaviours and three chances to flash. Inter does all of it:
#     its tabular-figure feature gives numbers the fixed advance that made a
#     monospace necessary, without the typewriter texture.
#
#     Loaded with preconnect, a preload hint and display=swap, over a system
#     stack that is metrically close on every platform. Text is readable from
#     the first paint and never invisible.
#
# COLOUR MEANS SOMETHING OR IT IS GREY
#
#     One accent, used for interaction. Green and red reserved for direction
#     and for pass/fail - never decoration. Everything else is a neutral, so
#     the eye goes to the number that moved rather than to the chrome.
#
#     Both themes are defined here as variables: the automatic one follows the
#     system, and an explicit data-theme on the root overrides it in either
#     direction.
_TOKENS = """
  :root {
    color-scheme: light dark;

    --bg:           #ffffff;
    --bg-subtle:    #fafafa;
    --surface:      #ffffff;
    --surface-2:    #f7f8f9;
    --border:       #e7e9ec;
    --border-strong:#d3d7dd;

    --text:         #14181d;
    --text-muted:   #5c6670;
    --text-subtle:  #6a727c;

    --accent:       #4f46e5;
    --accent-text:  #ffffff;
    --accent-soft:  rgba(79, 70, 229, 0.09);

    --pos:          #067647;
    --pos-soft:     rgba(6, 118, 71, 0.10);
    --neg:          #b42318;
    --neg-soft:     rgba(180, 35, 24, 0.09);
    --warn:         #b54708;
    --warn-soft:    rgba(181, 71, 8, 0.10);

    /* Spacing scale. Every margin and pad in the app is one of these. */
    --s1: 4px;  --s2: 8px;  --s3: 12px; --s4: 16px;
    --s5: 24px; --s6: 32px; --s7: 48px; --s8: 64px;

    --radius-sm: 6px;
    --radius:    10px;
    --radius-lg: 14px;

    --maxw:    1120px;
    --measure: 68ch;

    --sans: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto,
            'Helvetica Neue', Arial, sans-serif;
  }

  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg:           #0c0e11;
      --bg-subtle:    #101317;
      --surface:      #14181d;
      --surface-2:    #191e24;
      --border:       #252b33;
      --border-strong:#333b45;

      --text:         #e7eaee;
      --text-muted:   #9aa4b0;
      --text-subtle:  #828d99;

      --accent:       #8b85f0;
      --accent-text:  #0c0e11;
      --accent-soft:  rgba(139, 133, 240, 0.14);

      --pos:          #3dd68c;
      --pos-soft:     rgba(61, 214, 140, 0.13);
      --neg:          #ff6b5e;
      --neg-soft:     rgba(255, 107, 94, 0.13);
      --warn:         #f5a55f;
      --warn-soft:    rgba(245, 165, 95, 0.13);
    }
  }

  :root[data-theme="dark"] {
    --bg:           #0c0e11;
    --bg-subtle:    #101317;
    --surface:      #14181d;
    --surface-2:    #191e24;
    --border:       #252b33;
    --border-strong:#333b45;

    --text:         #e7eaee;
    --text-muted:   #9aa4b0;
    --text-subtle:  #828d99;

    --accent:       #8b85f0;
    --accent-text:  #0c0e11;
    --accent-soft:  rgba(139, 133, 240, 0.14);

    --pos:          #3dd68c;
    --pos-soft:     rgba(61, 214, 140, 0.13);
    --neg:          #ff6b5e;
    --neg-soft:     rgba(255, 107, 94, 0.13);
    --warn:         #f5a55f;
    --warn-soft:    rgba(245, 165, 95, 0.13);
  }

  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; -webkit-text-size-adjust: 100%; }

  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    font-size: 15px;
    line-height: 1.55;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    font-feature-settings: 'cv05' 1;
  }

  a { color: inherit; }
  ::selection { background: var(--accent); color: var(--accent-text); }

  /* Numbers line up in columns and never jitter as they change. */
  .num, .n, table.health td, .fig-value, .metric-value {
    font-variant-numeric: tabular-nums;
    font-feature-settings: 'tnum' 1, 'cv05' 1;
  }

  /* Direction, not decoration. */
  .pos { color: var(--pos); }
  .neg { color: var(--neg); }

  /* A focus ring that is actually visible, in both themes. */
  :focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
    border-radius: var(--radius-sm);
  }

  @media (prefers-reduced-motion: reduce) {
    html { scroll-behavior: auto; }
    *, *::before, *::after {
      animation-duration: 0.01ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: 0.01ms !important;
    }
  }
"""


# Applied before the body paints, so a chosen theme never flashes the other
# one first. Inlined in the head of every page rather than bundled with the
# toggle: the preference is site-wide, the control is not.
_THEME_BOOT = """<script>
(function () {
  try {
    var t = localStorage.getItem('moat.theme');
    if (t) document.documentElement.setAttribute('data-theme', t);
  } catch (err) { /* private mode - follow the system preference */ }
})();
</script>"""


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
{_THEME_BOOT}
{_FONTS}
<style>{_TOKENS}{css}</style>
</head>
<body>
{body}
</body>
</html>"""


# ---------------------------------------------------------------- formatting

# Unknown renders as an em-dash, never as a zero or a blank. The distinction
# is load-bearing everywhere else in this codebase and it does not get to
# stop mattering at the last step.
EM_DASH = "\u2014"


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

_STATE_CLASS = {"PASS": "pass", "FAIL": "fail", "UNKNOWN": "unknown"}


def _state_of(check: dict) -> str:
    """An unrecognised status renders as "unknown" rather than raising.

    Indexing a literal dict with check["status"] meant a new status value
    anywhere upstream took down the whole tearsheet with a KeyError.
    """
    return _STATE_CLASS.get(check.get("status"), "unknown")


def _signed(value, formatter) -> str:
    """Format a number, colouring it by direction.

    Red and green mean up and down here, nothing else. A missing value is not
    a direction, so it takes neither - the formatter renders it as an em-dash
    and it stays the colour of ordinary text.
    """
    if value is None:
        return formatter(None)
    number = float(value)
    text = formatter(number)
    if number > 0:
        return f'<span class="pos">{text}</span>'
    if number < 0:
        return f'<span class="neg">{text}</span>'
    return text


def _meter(checks: list[dict], summary: dict) -> str:
    """The scorecard as one line: a segment per criterion, filled where it holds.

    The signature element, kept but slimmed. It used to be six large blocks
    occupying the fold; the information in it is a count and a pattern, and a
    single row of segments carries both without pushing the actual figures
    below the screen.
    """
    segments = "".join(
        f'<span class="seg {_state_of(c)}" title="{esc(c.get("name"))}: '
        f'{esc(c.get("detail"))}"></span>'
        for c in checks
    )
    passed = summary.get("passed", 0)
    evaluable = summary.get("evaluable", 0)
    unknown = summary.get("unknown", 0)
    caption = f"{passed} of {evaluable} criteria hold"
    if unknown:
        caption += f" &middot; {unknown} unevaluable"
    return (
        f'<div class="meter" role="img" aria-label="{esc(caption)}">{segments}</div>'
        f'<p class="meter-caption">{caption}</p>'
    )


def _checks_table(checks: list[dict]) -> str:
    """The scorecard as a grid of cards, one per criterion.

    A table made every row look equally important and buried the verdict in
    the left column. Cards put the outcome first, at a glance, with the
    arithmetic underneath for anyone who wants to check it.
    """
    cards = []
    for c in checks:
        state = _state_of(c)
        label = {"pass": "Pass", "fail": "Fail"}.get(state, "Unknown")
        cards.append(
            f'<div class="check {state}">'
            f'<div class="check-top">'
            f'<span class="check-name">{esc(c.get("name"))}</span>'
            f'<span class="tag {state}">{label}</span>'
            f"</div>"
            f'<p class="check-detail">{esc(c.get("detail"))}</p>'
            f"</div>"
        )
    return f'<div class="checks">{"".join(cards)}</div>'


def _figures(ttm: dict, valuation: dict, price: dict | None) -> str:
    """The headline numbers, as a grid of tiles.

    Every value is tabular so the column of figures reads as a column rather
    than as ragged text, and growth is signed because a negative one means
    something different from a small one.
    """
    share_price = None
    if price and price.get("price") is not None:
        share_price = float(price["price"])

    items = [
        ("Revenue (TTM)", money(ttm.get("revenue")), None),
        ("Net income (TTM)", money(ttm.get("net_income")), None),
        ("Free cash flow (TTM)", money(ttm.get("free_cash_flow")), None),
        ("Net margin", pct(ttm.get("net_margin")), None),
        ("FCF margin", pct(ttm.get("fcf_margin")), None),
        ("Revenue growth", None, ("signed", ttm.get("revenue_growth"), pct)),
        ("ROIC", pct(ttm.get("roic")), None),
        ("ROE", pct(ttm.get("roe")), None),
        ("Market cap", money(valuation.get("market_cap")), None),
        ("P / FCF", mult(valuation.get("p_fcf")), None),
        ("P / E", mult(valuation.get("p_e")), None),
        ("Share price", f"${share_price:,.2f}" if share_price is not None else EM_DASH, None),
    ]

    cells = []
    for label, value, signed in items:
        rendered = _signed(signed[1], signed[2]) if signed else value
        cells.append(
            f'<div class="fig">'
            f'<span class="fig-label">{esc(label)}</span>'
            f'<span class="fig-value num">{rendered}</span>'
            f"</div>"
        )
    return f'<div class="figures">{"".join(cells)}</div>'


def _health(health: dict) -> str:
    """Prior quarter against the latest, for the four survival metrics.

    Direction matters more than level, so the change column is signed and
    coloured while the levels stay neutral - otherwise every row is green and
    the one that moved the wrong way does not stand out.
    """
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
        rows.append(
            f"<tr>"
            f'<th scope="row">{esc(label)}</th>'
            f'<td class="n">{money(row.get("prior"))}</td>'
            f'<td class="n">{money(row.get("current"))}</td>'
            f'<td class="n">{_signed(change, money)}</td>'
            f"</tr>"
        )
    surv = health.get("survivability", {})
    verdict = surv.get("verdict", "")
    return f"""
      <div class="table-scroll">
        <table class="health">
          <thead><tr><th scope="col"></th><th scope="col" class="n">Prior qtr</th>
          <th scope="col" class="n">Latest qtr</th>
          <th scope="col" class="n">Change</th></tr></thead>
          <tbody>{"".join(rows)}</tbody>
        </table>
      </div>
      <p class="survivability">{esc(verdict)}</p>
    """


def _risks(risks: list[dict]) -> str:
    """Each risk, its supporting passage, and what would make you sell.

    The verification badge sits with the quote rather than below it, because
    it is a statement about that passage: this text was found, character for
    character, in the filing.
    """
    if not risks:
        return '<p class="empty">No risk analysis available for this filing.</p>'
    out = []
    for r in risks:
        verified = r.get("quote_verified")
        badge = (
            '<span class="verified">'
            '<svg width="11" height="11" viewBox="0 0 12 12" fill="none" '
            'aria-hidden="true"><path d="M2.5 6.2l2.4 2.4 4.6-5" '
            'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
            'stroke-linejoin="round"/></svg>Quote verified against filing</span>'
            if verified
            else '<span class="unverified">Quote not found in filing</span>'
        )
        out.append(
            f"""
            <article class="risk">
              <h3>{esc(r.get("risk"))}</h3>
              <blockquote>{esc(r.get("quote"))}</blockquote>
              {badge}
              <div class="trigger">
                <span class="trigger-label">What would make you sell</span>
                <p>{esc(r.get("sell_trigger"))}</p>
              </div>
            </article>
            """
        )
    return "".join(out)


def _timestamp(value: str | None) -> str:
    """Render an ISO timestamp, labelling it UTC only when it is UTC.

    The footer used to slice the first 19 characters and append " UTC"
    unconditionally. Postgres returns timestamptz in the session timezone, so
    a cached report arrived as "2026-09-24T14:44:35-04:00" and was displayed
    as "2026-09-24 14:44:35 UTC" - four hours wrong, under a label asserting
    otherwise. The payload now always carries UTC; this refuses to make the
    claim for anything that does not.
    """
    if not value:
        return ""
    text = str(value)
    stamp = esc(text[:19].replace("T", " "))
    if text.endswith("+00:00") or text.endswith("Z"):
        return f"{stamp} UTC"
    return stamp


def _paragraphs(text: str | None) -> str:
    if not text:
        return ""
    parts = [p.strip() for p in text.split("\n") if p.strip()]
    return "".join(f"<p>{esc(p)}</p>" for p in parts)


# ---------------------------------------------------------------- the page


# The report page's own styles. A plain string with single braces: it is
# passed to _document() rather than interpolated into an f-string, so the
# braces no longer have to be doubled - which is what made this block
# awkward to edit and easy to break.
_REPORT_CSS = """
  /* ---- sticky bar ---- */
  .topbar {
    position: sticky; top: 0; z-index: 20;
    background: color-mix(in srgb, var(--bg) 88%, transparent);
    backdrop-filter: saturate(1.6) blur(10px);
    border-bottom: 1px solid transparent;
    transition: border-color 160ms ease;
  }
  .topbar.scrolled { border-bottom-color: var(--border); }
  .topbar-inner {
    max-width: var(--maxw); margin: 0 auto;
    padding: 0 var(--s5); height: 52px;
    display: flex; align-items: center; gap: var(--s3);
  }
  .home {
    display: flex; align-items: center; gap: var(--s2);
    text-decoration: none; font-weight: 600; font-size: 0.92rem;
    letter-spacing: -0.01em; color: var(--text);
  }
  .mark {
    width: 20px; height: 20px; border-radius: 6px; flex: none;
    background: var(--text); position: relative;
  }
  .mark::after {
    content: ''; position: absolute; inset: 6px 6px auto 6px; height: 2px;
    background: var(--bg); border-radius: 1px; box-shadow: 0 4px 0 var(--bg);
  }
  /* Context appears only once the hero has scrolled away. */
  .topbar-ctx {
    display: flex; align-items: center; gap: var(--s3);
    margin-left: var(--s3); opacity: 0; transform: translateY(-2px);
    transition: opacity 160ms ease, transform 160ms ease;
    pointer-events: none;
  }
  .topbar.scrolled .topbar-ctx { opacity: 1; transform: none; }
  .topbar-ticker { font-weight: 600; letter-spacing: -0.01em; }
  .topbar-sep { color: var(--text-subtle); }
  .topbar-right { margin-left: auto; display: flex; align-items: center;
                  gap: var(--s3); }

  .theme-toggle {
    display: inline-flex; align-items: center; justify-content: center;
    width: 30px; height: 30px; padding: 0; cursor: pointer;
    color: var(--text-muted); background: transparent;
    border: 1px solid var(--border); border-radius: var(--radius-sm);
    transition: color 140ms ease, border-color 140ms ease;
  }
  .theme-toggle:hover { color: var(--text); border-color: var(--border-strong); }
  .theme-toggle .moon { display: none; }
  :root[data-theme="dark"] .theme-toggle .moon,
  :root:not([data-theme="light"]) .theme-toggle .moon { display: block; }
  :root[data-theme="dark"] .theme-toggle .sun,
  :root:not([data-theme="light"]) .theme-toggle .sun { display: none; }
  @media (prefers-color-scheme: light) {
    :root:not([data-theme="dark"]) .theme-toggle .moon { display: none; }
    :root:not([data-theme="dark"]) .theme-toggle .sun { display: block; }
  }

  /* ---- page ---- */
  .sheet { max-width: var(--maxw); margin: 0 auto;
           padding: var(--s6) var(--s5) var(--s8); }

  /* ---- hero ---- */
  .hero {
    display: flex; align-items: flex-start; justify-content: space-between;
    gap: var(--s5); flex-wrap: wrap; margin-bottom: var(--s6);
  }
  .hero h1 {
    font-size: clamp(2rem, 4.5vw, 2.75rem); line-height: 1.05;
    letter-spacing: -0.035em; font-weight: 650; margin: 0;
  }
  .hero-sub {
    margin: var(--s2) 0 0; color: var(--text-muted); font-size: 0.92rem;
    min-height: 23px;   /* one line of text, so the skeleton matches */
  }
  .hero-sub .dot { color: var(--text-subtle); margin: 0 var(--s2); }
  .hero-right { text-align: right; display: flex; flex-direction: column;
                align-items: flex-end; gap: var(--s3); }
  .price { font-size: 1.6rem; font-weight: 600; letter-spacing: -0.02em;
           line-height: 1; display: block; min-height: 26px; }
  .price-label { display: block; font-size: 0.72rem; font-weight: 500;
                 letter-spacing: 0.06em; text-transform: uppercase;
                 color: var(--text-subtle); margin-bottom: var(--s1); }

  /* ---- verdict badge ---- */
  .badge {
    display: inline-flex; align-items: center; gap: var(--s2);
    font-size: 0.78rem; font-weight: 600; letter-spacing: 0.02em;
    padding: 5px var(--s3); border-radius: 999px;
    border: 1px solid transparent; white-space: nowrap;
  }
  .badge::before {
    content: ''; width: 6px; height: 6px; border-radius: 50%;
    background: currentColor;
  }
  .badge.buy    { color: var(--pos);  background: var(--pos-soft);
                  border-color: color-mix(in srgb, var(--pos) 26%, transparent); }
  .badge.watch  { color: var(--warn); background: var(--warn-soft);
                  border-color: color-mix(in srgb, var(--warn) 26%, transparent); }
  .badge.avoid  { color: var(--neg);  background: var(--neg-soft);
                  border-color: color-mix(in srgb, var(--neg) 26%, transparent); }
  .badge.none   { color: var(--text-muted); background: var(--surface-2);
                  border-color: var(--border); }

  /* ---- criteria meter ---- */
  .meter {
    display: grid; grid-template-columns: repeat(6, 1fr); gap: 3px;
    height: 6px; margin: 0 0 var(--s3);
  }
  .seg { border-radius: 2px; background: var(--border); }
  .seg.pass { background: var(--pos); }
  .seg.fail { background: var(--neg); }
  .seg.unknown {
    background: repeating-linear-gradient(45deg, var(--border),
      var(--border) 3px, transparent 3px, transparent 6px);
    box-shadow: inset 0 0 0 1px var(--border);
  }
  .meter-caption {
    margin: 0 0 var(--s7); font-size: 0.85rem; color: var(--text-muted);
    min-height: 21px;
  }

  /* ---- sections ---- */
  section { margin-top: var(--s7); }
  section:first-of-type { margin-top: 0; }
  h2 {
    font-size: 0.75rem; font-weight: 600; letter-spacing: 0.08em;
    text-transform: uppercase; color: var(--text-subtle);
    margin: 0 0 var(--s4);
  }

  /* ---- scorecard grid ---- */
  .checks {
    display: grid; gap: var(--s3);
    grid-template-columns: repeat(auto-fit, minmax(248px, 1fr));
  }
  .check {
    border: 1px solid var(--border); border-radius: var(--radius);
    background: var(--surface); padding: var(--s4);
    /* Fixed box so the skeleton and the real card are the same size and
       nothing moves when one replaces the other. Two lines of detail is the
       worst case across the six criteria. */
    min-height: 108px;
  }
  .check-top {
    display: flex; align-items: center; justify-content: space-between;
    gap: var(--s2); margin-bottom: var(--s2);
  }
  .check-name { font-weight: 550; font-size: 0.95rem; }
  .check-detail {
    margin: 0; font-size: 0.83rem; color: var(--text-muted);
    font-variant-numeric: tabular-nums;
  }
  .tag {
    font-size: 0.68rem; font-weight: 600; letter-spacing: 0.04em;
    text-transform: uppercase; padding: 2px 7px; border-radius: 999px;
  }
  .tag.pass    { color: var(--pos); background: var(--pos-soft); }
  .tag.fail    { color: var(--neg); background: var(--neg-soft); }
  .tag.unknown { color: var(--text-subtle); background: var(--surface-2); }

  /* ---- figures ---- */
  .figures {
    display: grid; gap: var(--s3);
    grid-template-columns: repeat(auto-fit, minmax(168px, 1fr));
  }
  .fig {
    border: 1px solid var(--border); border-radius: var(--radius);
    background: var(--surface); padding: var(--s4);
    min-height: 90px;   /* label + value, same for skeleton and real */
  }
  .fig-label {
    display: block; font-size: 0.78rem; color: var(--text-muted);
    margin-bottom: var(--s1);
  }
  .fig-value {
    display: block; font-size: 1.25rem; font-weight: 600;
    letter-spacing: -0.015em;
  }

  /* ---- financial health ---- */
  .table-scroll { overflow-x: auto; }
  table.health { width: 100%; border-collapse: collapse; font-size: 0.9rem;
                 min-width: 520px; }
  table.health th[scope="col"] {
    font-size: 0.72rem; font-weight: 600; letter-spacing: 0.05em;
    text-transform: uppercase; color: var(--text-subtle); text-align: left;
    padding: 0 0 var(--s2); border-bottom: 1px solid var(--border);
  }
  table.health th[scope="row"] {
    font-weight: 450; text-align: left; color: var(--text);
  }
  table.health td, table.health th[scope="row"] {
    padding: var(--s3) 0; border-bottom: 1px solid var(--border);
    height: 45px;   /* pinned so skeleton rows match filled ones */
  }
  table.health tbody tr:last-child td,
  table.health tbody tr:last-child th { border-bottom: none; }
  .n { text-align: right; }
  table.health td.n { padding-left: var(--s4); }
  .survivability {
    margin: var(--s4) 0 0; font-size: 0.88rem; color: var(--text-muted);
    padding: var(--s3) var(--s4); background: var(--surface-2);
    border-radius: var(--radius); border: 1px solid var(--border);
    min-height: 48px;
  }

  /* ---- prose ---- */
  .prose { max-width: var(--measure); font-size: 0.97rem; }
  .prose p { margin: 0 0 var(--s4); color: var(--text); }
  .prose p:last-child { margin-bottom: 0; }
  .lede { font-size: 1.05rem; }

  /* ---- risks ---- */
  .risk {
    border: 1px solid var(--border); border-radius: var(--radius-lg);
    background: var(--surface); padding: var(--s5); margin-bottom: var(--s3);
  }
  .risk:last-child { margin-bottom: 0; }
  .risk h3 {
    font-size: 1rem; font-weight: 600; line-height: 1.4; margin: 0 0 var(--s4);
    max-width: var(--measure); letter-spacing: -0.01em;
  }
  .risk blockquote {
    margin: 0; padding: var(--s4); background: var(--surface-2);
    border-radius: var(--radius); border-left: 2px solid var(--border-strong);
    font-size: 0.92rem; color: var(--text-muted); max-width: var(--measure);
  }
  .verified, .unverified {
    display: inline-flex; align-items: center; gap: 5px;
    margin-top: var(--s3); font-size: 0.72rem; font-weight: 600;
    letter-spacing: 0.02em; padding: 3px 9px; border-radius: 999px;
  }
  .verified   { color: var(--pos); background: var(--pos-soft); }
  .unverified { color: var(--neg); background: var(--neg-soft); }
  .trigger { margin-top: var(--s4); max-width: var(--measure); }
  .trigger-label {
    display: block; font-size: 0.7rem; font-weight: 600; letter-spacing: 0.06em;
    text-transform: uppercase; color: var(--text-subtle); margin-bottom: var(--s1);
  }
  .trigger p { margin: 0; font-size: 0.92rem; }
  .empty { color: var(--text-muted); font-size: 0.92rem; margin: 0; }

  /* ---- footer ---- */
  footer {
    margin-top: var(--s8); padding-top: var(--s5);
    border-top: 1px solid var(--border);
    font-size: 0.82rem; color: var(--text-subtle);
  }
  footer p { margin: var(--s1) 0; }
  footer a { color: var(--text-muted); }
  .disclaimer { margin-top: var(--s4); max-width: var(--measure); }

  /* ---- skeletons ----
     Shaped like the content they stand in for. A generic spinner tells you
     to wait; a skeleton tells you what is coming and reserves its space, so
     the arrival is a change of pixels rather than a change of layout. */
  .sk {
    display: block; border-radius: 5px; background: var(--border);
    position: relative; overflow: hidden;
  }
  .sk::after {
    content: ''; position: absolute; inset: 0; transform: translateX(-100%);
    background: linear-gradient(90deg, transparent,
      color-mix(in srgb, var(--surface) 70%, transparent), transparent);
    animation: sweep 1.4s ease-in-out infinite;
  }
  @keyframes sweep { to { transform: translateX(100%); } }
  .sk-line   { height: 11px; }
  .sk-value  { height: 20px; margin-top: 7px; }
  .sk-title  { height: 15px; }
  .sk-w40 { width: 40%; } .sk-w55 { width: 55%; } .sk-w70 { width: 70%; }
  .sk-w85 { width: 85%; } .sk-w100 { width: 100%; }
  .sk-cell { display: inline-block; height: 12px; width: 62px; }

  /* Content that has just replaced a skeleton. Starts partly visible rather
     than at zero so the swap reads as developing, not as a blank flash. */
  .landed { animation: landed 180ms ease-out; }
  @keyframes landed {
    from { opacity: 0.4; }
    to   { opacity: 1; }
  }

  /* ---- progress ----
     Floating, so it costs no layout at all: it can appear and leave without
     moving a single pixel of the report behind it. */
  .progress {
    position: fixed; right: var(--s5); bottom: var(--s5); z-index: 30;
    width: 254px; padding: var(--s4); margin: 0; list-style: none;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    box-shadow: 0 8px 28px rgba(0, 0, 0, 0.10), 0 1px 2px rgba(0, 0, 0, 0.06);
    transition: opacity 260ms ease, transform 260ms ease;
  }
  .progress.gone { opacity: 0; transform: translateY(6px); pointer-events: none; }
  .progress li {
    display: flex; align-items: center; gap: var(--s2);
    padding: 5px 0; font-size: 0.82rem; color: var(--text-subtle);
  }
  .progress .mark {
    width: 13px; height: 13px; flex: none; border-radius: 50%;
    border: 1.5px solid currentColor;
  }
  .progress .took { margin-left: auto; font-size: 0.72rem; opacity: 0.8;
                    font-variant-numeric: tabular-nums; }
  .progress li[data-state="running"] { color: var(--text); }
  .progress li[data-state="running"] .mark {
    border-color: var(--accent);
    border-right-color: transparent; border-bottom-color: transparent;
    animation: spin 0.7s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .progress li[data-state="done"] { color: var(--text-muted); }
  .progress li[data-state="done"] .mark {
    background: var(--pos); border-color: var(--pos);
  }
  .progress li[data-state="skipped"] .mark { opacity: 0.45; }
  .progress li[data-state="failed"] { color: var(--neg); }
  .progress li[data-state="failed"] .mark { border-color: var(--neg); }

  /* ---- a narrative section still being written ---- */
  .pending {
    display: flex; align-items: center; gap: var(--s2); margin: 0;
    font-size: 0.85rem; color: var(--text-subtle);
  }
  .pending-dot {
    width: 6px; height: 6px; border-radius: 50%; flex: none;
    background: var(--text-subtle); animation: pulse 1.3s ease-in-out infinite;
  }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.25; } }

  /* ---- failure ---- */
  .failure {
    margin: var(--s5) 0 0; padding: var(--s4) var(--s5);
    border: 1px solid color-mix(in srgb, var(--neg) 30%, var(--border));
    border-left-width: 3px; border-left-color: var(--neg);
    border-radius: var(--radius); background: var(--neg-soft);
  }
  .failure h2 {
    margin: 0 0 var(--s2); color: var(--neg); font-size: 0.8rem;
    letter-spacing: 0.06em;
  }
  .failure p { margin: 0; font-size: 0.95rem; color: var(--text); }
  .failure a { color: var(--text); }

  @media (max-width: 720px) {
    .progress { left: var(--s4); right: var(--s4); width: auto; }
    .sheet { padding: var(--s5) var(--s4) var(--s7); }
    .topbar-inner { padding: 0 var(--s4); }
    .hero { gap: var(--s4); }
    .hero-right { align-items: flex-start; text-align: left; }
    .topbar-ctx { display: none; }
  }
"""


def _pending(label: str) -> str:
    """A narrative section the model has not finished writing yet."""
    return f'<p class="pending"><span class="pending-dot"></span>{esc(label)}</p>'


VERDICT_CLASS = {"BUY-CASE": "buy", "WATCH-CASE": "watch", "AVOID-CASE": "avoid"}


def _theme_toggle() -> str:
    """A light/dark switch, drawn as two icons with one shown at a time."""
    return """
      <button class="theme-toggle" id="theme" type="button"
              aria-label="Switch between light and dark">
        <svg class="sun" width="15" height="15" viewBox="0 0 16 16" fill="none"
             aria-hidden="true">
          <circle cx="8" cy="8" r="3.1" stroke="currentColor" stroke-width="1.5"/>
          <path d="M8 1v1.6M8 13.4V15M15 8h-1.6M2.6 8H1M12.9 3.1l-1.1 1.1M4.2 11.8l-1.1 1.1M12.9 12.9l-1.1-1.1M4.2 4.2L3.1 3.1"
                stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        </svg>
        <svg class="moon" width="15" height="15" viewBox="0 0 16 16" fill="none"
             aria-hidden="true">
          <path d="M13.5 9.6A5.8 5.8 0 016.4 2.5a5.8 5.8 0 107.1 7.1z"
                stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
        </svg>
      </button>
    """


def _topbar(ticker: str, name: str, verdict: str, verdict_class: str) -> str:
    """The persistent bar: home, and - once scrolled - which report this is."""
    return f"""
    <div class="topbar" id="topbar">
      <div class="topbar-inner">
        <a class="home" href="/">
          <span class="mark" aria-hidden="true"></span>Moat
        </a>
        <div class="topbar-ctx" aria-hidden="true">
          <span class="topbar-sep">/</span>
          <span class="topbar-ticker">{esc(ticker)}</span>
          <span class="badge {verdict_class}">{esc(verdict)}</span>
        </div>
        <div class="topbar-right">{_theme_toggle()}</div>
      </div>
    </div>
    """


def _report_sheet(report: dict, pending: bool = False) -> str:
    """The tearsheet body, with or without a narrative.

    One builder for both states. When pending is true the computed sections
    render exactly as they finally will - they are already final - and only
    the narrative sections show that the model is still working. That is what
    lets the page show real content seconds after the request rather than
    minutes, without maintaining a second copy of the layout that could drift
    from this one.
    """
    data = report.get("data", {})
    ttm = data.get("ttm", {})
    scorecard = data.get("scorecard", {})
    checks = scorecard.get("checks", [])
    summary = scorecard.get("summary", {})
    narrative = report.get("narrative") or {}
    sources = report.get("sources", {})
    cache = report.get("cache", {})
    price = data.get("price")

    verdict = narrative.get("verdict", "PENDING" if pending else "NO VERDICT")
    verdict_class = VERDICT_CLASS.get(verdict, "none")

    grounding = narrative.get("grounding_rate")
    grounding_str = f"{float(grounding) * 100:.0f}%" if grounding is not None else EM_DASH

    # esc(None) is the empty string, so an absent filing URL produced
    # href="" - a link back to the current page, which reads as working and
    # is not. With no URL there is nothing to link to, so say so in text.
    filing_url = sources.get("filing")
    if filing_url:
        filing_line = (
            f'<a href="{esc(filing_url)}">10-K filed {esc(sources.get("report_date"))}</a>'
        )
    else:
        filing_line = "10-K unavailable"

    share_price = None
    if price and price.get("price") is not None:
        share_price = f"${float(price['price']):,.2f}"

    price_block = (
        f'<div><span class="price-label">Share price</span>'
        f'<span class="price num">{share_price}</span></div>'
        if share_price
        else ""
    )

    # id="sheet" on every rendering of the sheet, not just the shell's. The
    # page swaps this element out as each stage lands, so the anchor has to
    # survive the swap - without it the partial replaced the only element
    # carrying the id, and the done handler then had nothing to replace.
    body = f"""<div class="sheet" id="sheet">

  <header class="hero">
    <div>
      <h1>{esc(report.get("company"))}</h1>
      <p class="hero-sub">{esc(report.get("name"))}<span class="dot">&middot;</span>
         Data as of {esc(data.get("as_of"))}</p>
    </div>
    <div class="hero-right">
      {price_block}
      <span class="badge {verdict_class}">{esc(verdict)}</span>
    </div>
  </header>

  {_meter(checks, summary)}

  <section>
    <h2>Scorecard</h2>
    {_checks_table(checks)}
  </section>

  <section>
    <h2>Figures</h2>
    {_figures(ttm, scorecard.get("valuation", {}), price)}
  </section>

  <section>
    <h2>Financial health</h2>
    {_health(scorecard.get("financial_health", {}))}
  </section>

  <section>
    <h2>Hype versus reality</h2>
    <div class="prose lede">{
        _pending("Writing analysis\u2026")
        if pending
        else _paragraphs(narrative.get("hype_vs_reality"))
    }</div>
  </section>

  <section>
    <h2>Risks and sell triggers</h2>
    {_pending("Reading the risk factors\u2026") if pending else _risks(narrative.get("risks", []))}
  </section>

  <section>
    <h2>The case</h2>
    <div class="prose">{
        _pending("Writing analysis\u2026") if pending else _paragraphs(narrative.get("reasoning"))
    }</div>
  </section>

  <section>
    <h2>The strategy</h2>
    <div class="prose">{
        _pending("Writing analysis\u2026") if pending else _paragraphs(narrative.get("strategy"))
    }</div>
  </section>

  <footer>
    <p>Financials from {esc(sources.get("financials"))}.
       Price from {esc(sources.get("price"))}.</p>
    <p>Filing: {filing_line}{
        ""
        if pending
        else f" &middot; {grounding_str} of quotes verified against the source document."
    }</p>
    <p>{"Cached" if cache.get("cached") else "Generated"}
       {_timestamp(cache.get("generated_at"))}</p>
    <p class="disclaimer">This is a screen against stated criteria, not
       investment advice. Every figure is computed from filed data; the
       narrative interprets those figures and does not calculate them.</p>
  </footer>

</div>
"""
    return body


# Shared by every page that has a sticky bar: remember the choice, apply it
# before paint so there is no flash of the wrong theme, and keep following the
# system until someone actually chooses.
THEME_SCRIPT = """
<script>
(function () {
  var KEY = 'moat.theme';   // applied in the head by _THEME_BOOT

  function bind() {
    var btn = document.getElementById('theme');
    if (!btn) return;
    btn.addEventListener('click', function () {
      var root = document.documentElement;
      var now = root.getAttribute('data-theme');
      if (!now) {
        now = window.matchMedia('(prefers-color-scheme: dark)').matches
          ? 'dark' : 'light';
      }
      var next = now === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem(KEY, next); } catch (err) { /* ignore */ }
    });
  }

  var bar = document.getElementById('topbar');
  function onScroll() {
    if (bar) bar.classList.toggle('scrolled', window.scrollY > 24);
  }
  window.addEventListener('scroll', onScroll, { passive: true });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { bind(); onScroll(); });
  } else { bind(); onScroll(); }
})();
</script>
"""


def render_report(report: dict) -> str:
    """The finished tearsheet, as a complete page."""
    narrative = report.get("narrative") or {}
    verdict = narrative.get("verdict", "NO VERDICT")
    body = (
        _topbar(
            str(report.get("company") or ""),
            str(report.get("name") or ""),
            verdict,
            VERDICT_CLASS.get(verdict, "none"),
        )
        + _report_sheet(report)
        + THEME_SCRIPT
    )
    return _document(f"{esc(report.get('company'))} \u00b7 Moat", body, _REPORT_CSS)


# ---------------------------------------------------------------- landing page


def render_landing() -> str:
    """The front door: a wordmark, one line of what this is, and a ticker box.

    No framework and no build step. The only script focuses the field, binds
    "/" the way every search-first product does, and keeps the last few
    tickers in localStorage so the second visit is one click rather than
    retyping.
    """
    css = """
      .wrap { min-height: 100vh; display: flex; flex-direction: column; }
      .land-main {
        flex: 1; width: 100%; max-width: 560px; margin: 0 auto;
        padding: 18vh var(--s5) var(--s7);
      }
      .brand {
        display: flex; align-items: center; gap: var(--s3);
        margin: 0 0 var(--s6);
      }
      .brand-mark {
        width: 30px; height: 30px; border-radius: 8px; flex: none;
        background: var(--text); position: relative;
      }
      .brand-mark::after {
        content: ''; position: absolute; inset: 9px 9px auto 9px; height: 3px;
        background: var(--bg); border-radius: 2px;
        box-shadow: 0 6px 0 var(--bg);
      }
      .brand-name {
        font-size: 1.15rem; font-weight: 600; letter-spacing: -0.015em;
      }
      h1 {
        font-size: clamp(2rem, 5vw, 2.6rem); line-height: 1.12;
        letter-spacing: -0.03em; font-weight: 600; margin: 0 0 var(--s4);
      }
      .lede {
        font-size: 1rem; color: var(--text-muted); margin: 0 0 var(--s6);
        max-width: 46ch;
      }

      form.search { position: relative; }
      .field {
        display: flex; align-items: center; gap: var(--s2);
        background: var(--surface); border: 1px solid var(--border-strong);
        border-radius: var(--radius); padding: 0 var(--s2) 0 var(--s4);
        transition: border-color 140ms ease, box-shadow 140ms ease;
      }
      .field:focus-within {
        border-color: var(--accent);
        box-shadow: 0 0 0 3px var(--accent-soft);
      }
      .field svg { flex: none; color: var(--text-subtle); }
      .field input {
        flex: 1; font: inherit; font-size: 1rem; font-weight: 500;
        letter-spacing: 0.04em; color: var(--text); background: transparent;
        border: none; outline: none; padding: 14px 0; min-width: 0;
      }
      .field input::placeholder {
        color: var(--text-subtle); letter-spacing: 0; font-weight: 400;
      }
      .slash {
        flex: none; font-size: 0.72rem; color: var(--text-subtle);
        border: 1px solid var(--border); border-radius: var(--radius-sm);
        padding: 2px 7px; line-height: 1.5;
      }
      .field button {
        flex: none; font: inherit; font-size: 0.85rem; font-weight: 550;
        color: var(--accent-text); background: var(--accent); border: none;
        border-radius: var(--radius-sm); padding: 9px var(--s4);
        cursor: pointer; transition: opacity 140ms ease;
      }
      .field button:hover { opacity: 0.88; }
      .field button:disabled { opacity: 0.6; cursor: default; }

      .status {
        margin: var(--s3) 0 0; min-height: 1.2em; font-size: 0.85rem;
        color: var(--accent);
      }

      .row {
        display: flex; align-items: center; gap: var(--s2);
        flex-wrap: wrap; margin-top: var(--s5);
        font-size: 0.85rem; color: var(--text-subtle);
      }
      .row .label { margin-right: var(--s1); }
      .chip {
        text-decoration: none; color: var(--text-muted);
        border: 1px solid var(--border); border-radius: 999px;
        padding: 4px 11px; font-weight: 500; letter-spacing: 0.02em;
        transition: border-color 140ms ease, color 140ms ease,
                    background 140ms ease;
      }
      .chip:hover {
        color: var(--text); border-color: var(--border-strong);
        background: var(--surface-2);
      }
      #recent-row[hidden] { display: none; }

      footer {
        width: 100%; max-width: 560px; margin: 0 auto;
        padding: var(--s5) var(--s5) var(--s7);
        font-size: 0.8rem; color: var(--text-subtle);
      }
      footer p { margin: var(--s1) 0; }
      footer .disclaimer { margin-top: var(--s3); }

      @media (max-width: 640px) {
        .land-main { padding: 10vh var(--s4) var(--s6); }
        footer { padding-left: var(--s4); padding-right: var(--s4); }
      }
    """
    body = """
    <div class="wrap">
      <main class="land-main">
        <div class="brand">
          <span class="brand-mark" aria-hidden="true"></span>
          <span class="brand-name">Moat</span>
        </div>

        <h1>Grounded analysis of any US-listed company.</h1>
        <p class="lede">Computed financials from SEC filings, a scorecard
           against value-investing criteria, and the real risks pulled from the
           10-K &mdash; every claim checked against the source document.</p>

        <form class="search" role="search" onsubmit="return moatGo(event)">
          <div class="field">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none"
                 aria-hidden="true">
              <circle cx="7" cy="7" r="4.6" stroke="currentColor"
                      stroke-width="1.6"/>
              <path d="M10.5 10.5L14 14" stroke="currentColor"
                    stroke-width="1.6" stroke-linecap="round"/>
            </svg>
            <input id="t" name="t" placeholder="Search a ticker"
                   aria-label="Ticker" autocomplete="off"
                   autocapitalize="characters" autocorrect="off"
                   spellcheck="false" maxlength="10">
            <span class="slash" id="slash" aria-hidden="true">/</span>
            <button type="submit" id="go">Analyze</button>
          </div>
        </form>
        <p class="status" id="searching" aria-live="polite"></p>

        <div class="row" id="recent-row" hidden>
          <span class="label">Recent</span>
          <span id="recent"></span>
        </div>
        <div class="row">
          <span class="label">Try</span>
          <a class="chip" href="/company/MSFT/report/view">MSFT</a>
          <a class="chip" href="/company/AAPL/report/view">AAPL</a>
          <a class="chip" href="/company/NVDA/report/view">NVDA</a>
          <a class="chip" href="/company/IBM/report/view">IBM</a>
        </div>
      </main>
      <footer>
        <p>Fundamentals from SEC EDGAR &middot; price from yfinance.</p>
        <p class="disclaimer">A screen against stated value-investing criteria,
           not investment advice.</p>
      </footer>
    </div>
    <script>
      var inp = document.getElementById('t');
      var slash = document.getElementById('slash');

      inp.addEventListener('input', function () {
        inp.value = inp.value.toUpperCase();
      });
      // The hint is only useful while the field is not focused.
      inp.addEventListener('focus', function () { slash.style.opacity = '0'; });
      inp.addEventListener('blur', function () { slash.style.opacity = ''; });

      // "/" focuses search, the way every search-first product behaves.
      // Ignored while typing somewhere else, so it stays a shortcut rather
      // than a keystroke thief.
      document.addEventListener('keydown', function (e) {
        if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey) return;
        var el = document.activeElement;
        if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' ||
                   el.isContentEditable)) return;
        e.preventDefault();
        inp.focus();
        inp.select();
      });

      var KEY = 'moat.recent';

      function readRecent() {
        try { return JSON.parse(localStorage.getItem(KEY)) || []; }
        catch (err) { return []; }
      }

      function remember(ticker) {
        try {
          var list = readRecent().filter(function (x) { return x !== ticker; });
          list.unshift(ticker);
          localStorage.setItem(KEY, JSON.stringify(list.slice(0, 5)));
        } catch (err) { /* private mode, or storage full - not worth failing */ }
      }

      function paintRecent() {
        var list = readRecent();
        if (!list.length) return;
        document.getElementById('recent').innerHTML = list.map(function (t) {
          return '<a class="chip" href="/company/' + encodeURIComponent(t) +
                 '/report/view">' + t + '</a>';
        }).join(' ');
        document.getElementById('recent-row').hidden = false;
      }
      paintRecent();

      function moatGo(e) {
        e.preventDefault();
        var t = inp.value.trim().toUpperCase().replace(/[^A-Z0-9.-]/g, '');
        if (!t) { inp.focus(); return false; }
        remember(t);
        // Acknowledge the submit before navigating. The next page answers in
        // milliseconds, but "milliseconds" is not "immediately", and a button
        // that does nothing visible when pressed is the whole complaint.
        var btn = document.getElementById('go');
        btn.textContent = 'Loading ' + t;
        btn.disabled = true;
        inp.disabled = true;
        document.getElementById('searching').textContent =
          'Opening ' + t + '\u2026';
        window.location.href = '/company/' + encodeURIComponent(t) + '/report/view';
        return false;
      }

      inp.focus();
    </script>
    """
    return _document("Moat \u00b7 Filing analysis", body, css)


def render_not_found(detail: str) -> str:
    """A small, on-brand 404 for /company/* misses (bad ticker, no filing).

    It offers the search box rather than only a way back to it: the reason
    you are here is almost always a mistyped ticker, and the fix is to type
    another one.
    """
    css = """
      .nf { min-height: 100vh; display: flex; align-items: center;
            justify-content: center; padding: var(--s5); }
      .nf-inner { max-width: 420px; width: 100%; text-align: center; }
      .nf-icon {
        width: 44px; height: 44px; margin: 0 auto var(--s4);
        display: flex; align-items: center; justify-content: center;
        border-radius: 12px; background: var(--neg-soft); color: var(--neg);
      }
      .nf h1 {
        font-size: 1.4rem; font-weight: 600; letter-spacing: -0.02em;
        margin: 0 0 var(--s2);
      }
      .nf .detail {
        font-size: 0.95rem; color: var(--text-muted); margin: 0 0 var(--s5);
      }
      .field {
        display: flex; align-items: center; gap: var(--s2);
        background: var(--surface); border: 1px solid var(--border-strong);
        border-radius: var(--radius); padding: 0 var(--s2) 0 var(--s4);
        text-align: left;
      }
      .field:focus-within {
        border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft);
      }
      .field svg { flex: none; color: var(--text-subtle); }
      .field input {
        flex: 1; font: inherit; font-size: 0.95rem; font-weight: 500;
        letter-spacing: 0.04em; color: var(--text); background: transparent;
        border: none; outline: none; padding: 12px 0; min-width: 0;
      }
      .field input::placeholder { color: var(--text-subtle); letter-spacing: 0;
                                  font-weight: 400; }
      .field button {
        flex: none; font: inherit; font-size: 0.82rem; font-weight: 550;
        color: var(--accent-text); background: var(--accent); border: none;
        border-radius: var(--radius-sm); padding: 8px var(--s4); cursor: pointer;
      }
      .field button:hover { opacity: 0.88; }
      .nf .home {
        display: inline-block; margin-top: var(--s5); font-size: 0.85rem;
        color: var(--text-subtle); text-decoration: none;
      }
      .nf .home:hover { color: var(--text); }
    """
    body = f"""
    <div class="nf"><div class="nf-inner">
      <div class="nf-icon" aria-hidden="true">
        <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
          <circle cx="10" cy="10" r="7.5" stroke="currentColor" stroke-width="1.7"/>
          <path d="M10 6.2v4.4" stroke="currentColor" stroke-width="1.7"
                stroke-linecap="round"/>
          <circle cx="10" cy="13.6" r="0.95" fill="currentColor"/>
        </svg>
      </div>
      <h1>Not found</h1>
      <p class="detail">{esc(detail)}</p>
      <form class="search" role="search" onsubmit="return moatGo(event)">
        <div class="field">
          <svg width="15" height="15" viewBox="0 0 16 16" fill="none"
               aria-hidden="true">
            <circle cx="7" cy="7" r="4.6" stroke="currentColor" stroke-width="1.6"/>
            <path d="M10.5 10.5L14 14" stroke="currentColor" stroke-width="1.6"
                  stroke-linecap="round"/>
          </svg>
          <input id="t" placeholder="Try another ticker" aria-label="Ticker"
                 autocomplete="off" autocapitalize="characters" autocorrect="off"
                 spellcheck="false" maxlength="10">
          <button type="submit">Analyze</button>
        </div>
      </form>
      <a class="home" href="/">&larr; Back to search</a>
    </div></div>
    <script>
      var inp = document.getElementById('t');
      inp.addEventListener('input', function () {{
        inp.value = inp.value.toUpperCase();
      }});
      function moatGo(e) {{
        e.preventDefault();
        var t = inp.value.trim().toUpperCase().replace(/[^A-Z0-9.-]/g, '');
        if (!t) {{ inp.focus(); return false; }}
        window.location.href = '/company/' + encodeURIComponent(t) + '/report/view';
        return false;
      }}
      inp.focus();
    </script>
    """
    return _document("Not found \u00b7 Moat", body, css)


# ---------------------------------------------------- the progressive report
#
# A cold ticker takes around thirty seconds, almost all of it the model
# writing. The page used to be a blank document for that whole time, because
# the browser was simply waiting on the response. These three renderers turn
# it into something that shows what it knows the moment it knows it.


def _progress_list(active: str = "fetch") -> str:
    """The stage checklist, floating clear of the report.

    Rendered server-side so the sequence is in the first byte rather than
    appearing once JavaScript has run, and positioned out of flow so it can
    arrive and leave without moving any of the content behind it.
    """
    stages = [
        ("fetch", "Fetching SEC filings"),
        ("store", "Storing financials"),
        ("metrics", "Computing metrics"),
        ("synthesis", "Writing analysis"),
    ]
    items = []
    for key, label in stages:
        state = "running" if key == active else "pending"
        items.append(
            f'<li data-stage="{key}" data-state="{state}">'
            f'<span class="mark"></span><span class="label">{esc(label)}</span>'
            f'<span class="took"></span></li>'
        )
    return (
        f'<ul class="progress" id="progress" aria-live="polite" '
        f'aria-label="Report progress">{"".join(items)}</ul>'
    )


def _sk(classes: str = "sk-line sk-w70") -> str:
    return f'<span class="sk {classes}"></span>'


def _skeleton_sheet(ticker: str) -> str:
    """The report's shape, before any of it is known.

    Deliberately the same markup and the same classes as the real sheet, so
    every box is already the size the content will need: six criterion cards,
    twelve figure tiles, five health rows. When the figures land they land in
    place - the swap changes pixels, not positions.

    A generic spinner would have been less code and less use. This says what
    is coming and holds its seat.
    """
    safe = esc(ticker.upper())
    checks = "".join(
        f'<div class="check">'
        f'<div class="check-top">{_sk("sk-title sk-w55")}'
        f"{_sk('sk-line sk-w40')}</div>"
        f'<p class="check-detail">{_sk("sk-line sk-w100")}'
        f"{_sk('sk-line sk-w70')}</p>"
        f"</div>"
        for _ in range(6)
    )
    figures = "".join(
        f'<div class="fig">{_sk("sk-line sk-w70")}{_sk("sk-value sk-w55")}</div>' for _ in range(12)
    )
    rows = "".join(
        f'<tr><th scope="row">{_sk("sk-line sk-w55")}</th>'
        f'<td class="n">{_sk("sk-cell")}</td>'
        f'<td class="n">{_sk("sk-cell")}</td>'
        f'<td class="n">{_sk("sk-cell")}</td></tr>'
        for _ in range(5)
    )
    segments = "".join('<span class="seg"></span>' for _ in range(6))

    return f"""<div class="sheet" id="sheet">

  <header class="hero">
    <div>
      <h1>{safe}</h1>
      <p class="hero-sub">{_sk("sk-line sk-w70")}</p>
    </div>
    <div class="hero-right">
      <div><span class="price-label">Share price</span>
           <span class="price">{_sk("sk-value sk-w100")}</span></div>
      <span class="badge none">PENDING</span>
    </div>
  </header>

  <div class="meter">{segments}</div>
  <p class="meter-caption">{_sk("sk-line sk-w40")}</p>

  <section>
    <h2>Scorecard</h2>
    <div class="checks">{checks}</div>
  </section>

  <section>
    <h2>Figures</h2>
    <div class="figures">{figures}</div>
  </section>

  <section>
    <h2>Financial health</h2>
    <div class="table-scroll">
      <table class="health">
        <thead><tr><th scope="col"></th><th scope="col" class="n">Prior qtr</th>
        <th scope="col" class="n">Latest qtr</th>
        <th scope="col" class="n">Change</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    <p class="survivability">{_sk("sk-line sk-w55")}</p>
  </section>

  <section>
    <h2>Hype versus reality</h2>
    <div class="prose lede">{_pending("Writing analysis\u2026")}</div>
  </section>

  <section>
    <h2>Risks and sell triggers</h2>
    {_pending("Reading the risk factors\u2026")}
  </section>

  <section>
    <h2>The case</h2>
    <div class="prose">{_pending("Writing analysis\u2026")}</div>
  </section>

  <section>
    <h2>The strategy</h2>
    <div class="prose">{_pending("Writing analysis\u2026")}</div>
  </section>

</div>
"""


def render_report_fragment(report: dict, pending: bool = False) -> str:
    """The sheet on its own, for swapping into a page already on screen."""
    return _report_sheet(report, pending=pending)


def render_failure(title: str, detail: str) -> str:
    """An error the reader can act on, in the report's own styling."""
    return f"""
    <div class="failure">
      <h2>{esc(title)}</h2>
      <p>{esc(detail)}</p>
      <p style="margin-top:0.9rem"><a href="/">&larr; Back to search</a></p>
    </div>
    """


def render_report_shell(ticker: str) -> str:
    """The page served immediately while the report is built.

    It carries the sticky bar, the hero and a full skeleton of the report, so
    there is real structure on screen in the first response, then connects to
    the stream and fills itself in as each stage completes: the computed
    figures arrive at about two seconds, the narrative when the model is done.

    EventSource rather than polling: the server already knows when each stage
    finishes, so there is nothing to discover by asking repeatedly, and no job
    record to store or clean up.
    """
    safe = esc(ticker.upper())
    stream_url = json.dumps(f"/company/{ticker.upper()}/report/stream")
    lost = json.dumps(
        render_failure(
            "Connection lost",
            "The connection to the server dropped before the report was finished. "
            "Reloading will pick up from wherever it got to.",
        )
    )

    body = f"""
{_topbar(safe, "", "PENDING", "none")}
{_skeleton_sheet(ticker)}
{_progress_list()}

<noscript>
  <div class="sheet">
    <p class="meter-caption">This page builds the report as it loads and needs
      JavaScript. The same analysis is available as JSON at
      <a href="/company/{safe}/report">/company/{safe}/report</a>.</p>
  </div>
</noscript>
<script>
(function () {{
  var source = new EventSource({stream_url});
  var settled = false;
  var bar = document.getElementById('progress');

  function swap(html) {{
    var current = document.getElementById('sheet');
    if (!current) return;
    current.outerHTML = html;
    // The skeleton and the real content occupy the same boxes, so this is a
    // change of pixels rather than of layout. The fade makes it read as
    // developing instead of snapping.
    var fresh = document.getElementById('sheet');
    if (fresh) fresh.classList.add('landed');
    syncBar();
  }}

  // The swap replaces the sheet, not the bar above it, so the bar keeps
  // whatever verdict it was rendered with - it sat on PENDING while the hero
  // said WATCH-CASE. Mirror the hero's badge and company name into it.
  function syncBar() {{
    var hero = document.querySelector('.hero .badge');
    var slot = document.querySelector('.topbar-ctx .badge');
    if (hero && slot) {{
      slot.textContent = hero.textContent.trim();
      slot.className = hero.className;
    }}
  }}

  function setStage(stage) {{
    var li = document.querySelector('[data-stage="' + stage.key + '"]');
    if (!li) return;
    li.setAttribute('data-state', stage.state);
    if (stage.seconds !== undefined) {{
      li.querySelector('.took').textContent = stage.seconds.toFixed(1) + 's';
    }}
    if (stage.detail) {{
      li.querySelector('.label').textContent = stage.label + ' \u2014 ' + stage.detail;
    }}
  }}

  function dismiss(delay) {{
    if (!bar) return;
    setTimeout(function () {{ bar.classList.add('gone'); }}, delay);
  }}

  source.addEventListener('stage', function (e) {{ setStage(JSON.parse(e.data)); }});
  source.addEventListener('partial', function (e) {{ swap(JSON.parse(e.data).html); }});

  source.addEventListener('done', function (e) {{
    settled = true;
    swap(JSON.parse(e.data).html);
    dismiss(900);
    source.close();
  }});

  source.addEventListener('failed', function (e) {{
    settled = true;
    var target = document.getElementById('sheet') || document.body;
    target.insertAdjacentHTML('beforeend', JSON.parse(e.data).html);
    var running = document.querySelector('[data-state="running"]');
    if (running) running.setAttribute('data-state', 'failed');
    dismiss(2500);
    source.close();
  }});

  // A dropped connection must not leave the page building forever.
  source.onerror = function () {{
    if (settled) return;
    settled = true;
    source.close();
    var target = document.getElementById('sheet') || document.body;
    target.insertAdjacentHTML('beforeend', {lost});
    dismiss(2500);
  }};
}})();
</script>
{THEME_SCRIPT}
"""
    return _document(f"{safe} \u00b7 Moat", body, _REPORT_CSS)
