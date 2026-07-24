# Evaluation Set — Risk Factor Analysis

Test questions for the 10-K analysis system, written **before** building it.

**Source document:** Microsoft FY2025 10-K, Item 1A Risk Factors
(69,079 characters, report date 2025-06-30)

## How to use this

Every answer the system gives must include a **verbatim quote** from the source
text. That makes grading partly automatic: we string-match the quote against the
document. If the quote isn't there, the model fabricated it — no judgment call
needed.

Three metrics:

- **Grounding rate** — % of claims whose quotes actually appear in the source.
  Target: 100%. Anything less is fabrication.
- **Abstention accuracy** — % of ABSENT questions where the system correctly
  says the filing doesn't address it. Target: 100%.
- **Answer correctness** — % of ANSWERABLE and SPECIFIC questions where the
  answer contains the key fact. Graded by hand.

## Verification method

Expected answers below were verified by keyword search against the extracted
text. Raw counts:

| Term | Occurrences |
|------|-------------|
| Google | 0 |
| Amazon | 0 |
| GDPR | 1 |
| Digital Markets Act | 0 |
| Activision | 2 |
| artificial intelligence | 1 |
| datacenter | 9 |

---

## Category 1: ANSWERABLE
*The filing clearly addresses these. Tests basic retrieval and synthesis.*

| # | Question | Expected answer |
|---|----------|-----------------|
| 1 | Does Microsoft identify competition as a material risk? | Yes — "Strategic and Competitive Risks" is the first risk category, describing competitors ranging from large diversified firms to small specialized ones. |
| 2 | Does the filing discuss risks related to artificial intelligence? | Yes, but see Q19 — the phrase "artificial intelligence" appears only once; verify whether AI risk is discussed at length under the "AI" abbreviation. |
| 3 | Does Microsoft describe cybersecurity or data breach risk? | Yes — "Cybersecurity, Data Privacy, and Platform Abuse Risks" is a named category. |
| 4 | Does the filing mention government regulation or antitrust as a risk? | Yes — "Legal, Regulatory, and Litigation Risks" is a named category. |
| 5 | Does Microsoft discuss risk related to attracting or retaining employees? | Yes — under Operational Risks; the section closes with unionization increasing costs. |
| 6 | Does the filing discuss datacenter or infrastructure capacity risk? | Yes — "datacenter" appears 9 times. |
| 7 | Does Microsoft identify intellectual property risk? | Yes — "Intellectual Property Risks" is its own named category. |
| 8 | Does the filing discuss risks from catastrophic events? | Yes — expected under General Risks. |

## Category 2: ABSENT
*The filing does NOT address these. Correct answer is "the filing does not say."
This is the hallucination test — the most important category. Several of these
are deliberately baited: the model knows the answer from training data, but the
document doesn't contain it.*

| # | Question | Expected answer |
|---|----------|-----------------|
| 9 | What is Microsoft's target debt-to-equity ratio? | NOT STATED — Item 1A contains no financial targets. |
| 10 | How much did Microsoft pay in dividends per share this year? | NOT STATED — dividends appear in the financial statements, not risk factors. |
| 11 | What is the CEO's total compensation? | NOT STATED — compensation is disclosed in the proxy statement (DEF 14A). |
| 12 | How many Azure customers does Microsoft have? | NOT STATED — customer counts are not disclosed here. |
| 13 | What is Microsoft's revenue guidance for next fiscal year? | NOT STATED — 10-Ks contain no forward guidance. |
| 16 | Does the filing name specific competitor companies such as Google or Amazon? | **NOT STATED** — neither name appears anywhere in the section. Competitors are described generically. **This is the key trap:** a model relying on world knowledge will confidently name them. |
| 20 | Does the filing name the EU Digital Markets Act? | **NOT STATED** — "Digital Markets Act" appears zero times, despite being highly relevant to Microsoft in reality. Another world-knowledge trap. |

## Category 3: SPECIFIC
*Requires a precise named detail. Tests whether the system is reading the
document or pattern-matching on financial boilerplate.*

| # | Question | Expected answer |
|---|----------|-----------------|
| 14 | What are the named risk categories in Item 1A, in order? | **VERIFIED — seven categories:** 1. Strategic and Competitive Risks · 2. Risks Relating to the Evolution of Our Business · 3. Cybersecurity, Data Privacy, and Platform Abuse Risks · 4. Operational Risks · 5. Legal, Regulatory, and Litigation Risks · 6. Intellectual Property Risks · 7. General Risks |
| 15 | Which specific privacy or data regulations does the filing name? | **VERIFIED — GDPR** (appears once). Notably NOT the Digital Markets Act. A complete answer names GDPR and does not invent others. |
| 17 | What does the filing say about unionization? | **VERIFIED** — the global workforce is predominantly non-unionized; increased unionization could raise costs and require establishing new relationships with worker representatives. |
| 18 | Does the filing mention any acquisition by name? | **VERIFIED — Activision** (appears twice). |
| 19 | How extensively does the filing discuss AI risk? | **[VERIFY]** — run `t.count("AI ")` and `t.count(" AI")`. If AI is discussed heavily under the abbreviation, Q2 stands as answerable; if genuinely sparse, that is itself a notable finding for a FY2025 Microsoft filing. |

---

## Notes on grading

- A response that says "the filing does not address this" on a Category 1 or 3
  question is a **false abstention** — over-cautious, tracked separately from
  hallucination.
- A response that answers a Category 2 question with anything other than "not
  stated" is a **hallucination**, even if the fact is true in the real world.
  The question is what the *document* says, not what is true.
- Quotes must be verbatim. Paraphrases presented as quotes count as ungrounded.
- Questions 16 and 20 are the highest-value tests in this set: both have answers
  the model almost certainly knows from training but that are absent from the
  source document.
