"""Evaluation questions for the risk-factor analyzer, as structured data.

Verified against Microsoft's FY2025 10-K Item 1A (69,079 chars, report
date 2025-06-30) by keyword search. Answer keys reflect what the document
actually contains, not what is true about Microsoft in general.

Fields:
    id             matches eval_questions.md
    question       the prompt sent to the analyzer
    category       "answerable" | "absent" | "specific"
    should_abstain True if the correct behavior is "not addressed"
    key_terms      lowercase substrings a correct answer should contain
                   (checked case-insensitively). None for abstain cases.

Verified term counts in the source: LinkedIn 1, climate 2, environmental
4, antitrust 1, employees 15, penalt 4.
"""

QUESTIONS = [
    # ---------- Category 1: ANSWERABLE ----------
    {
        "id": 1,
        "question": "Does Microsoft identify competition as a material risk?",
        "category": "answerable",
        "should_abstain": False,
        "key_terms": ["compet"],
    },
    {
        "id": 2,
        "question": "Does the filing discuss risks related to artificial intelligence?",
        "category": "answerable",
        "should_abstain": False,
        "key_terms": ["ai"],  # NOTE: weak term - "ai" matches many words. Review.
    },
    {
        "id": 3,
        "question": "Does Microsoft describe cybersecurity or data breach risk?",
        "category": "answerable",
        "should_abstain": False,
        "key_terms": ["cybersecurity"],
    },
    {
        "id": 4,
        "question": "Does the filing mention government regulation or antitrust as a risk?",
        "category": "answerable",
        "should_abstain": False,
        "key_terms": ["regulat"],
    },
    {
        "id": 5,
        "question": "Does Microsoft discuss risk related to attracting or retaining employees?",
        "category": "answerable",
        "should_abstain": False,
        "key_terms": ["employ"],
    },
    {
        "id": 6,
        "question": "Does the filing discuss datacenter or infrastructure capacity risk?",
        "category": "answerable",
        "should_abstain": False,
        "key_terms": ["datacenter"],
    },
    {
        "id": 7,
        "question": "Does Microsoft identify intellectual property risk?",
        "category": "answerable",
        "should_abstain": False,
        "key_terms": ["intellectual property"],
    },
    {
        "id": 8,
        "question": "Does the filing discuss risks from catastrophic events?",
        "category": "answerable",
        "should_abstain": False,
        "key_terms": ["catastrophic"],
    },
    # ---------- Category 2: ABSENT (hallucination tests) ----------
    {
        "id": 9,
        "question": "What is Microsoft's target debt-to-equity ratio?",
        "category": "absent",
        "should_abstain": True,
        "key_terms": None,
    },
    {
        "id": 10,
        "question": "How much did Microsoft pay in dividends per share this year?",
        "category": "absent",
        "should_abstain": True,
        "key_terms": None,
    },
    {
        "id": 11,
        "question": "What is the CEO's total compensation?",
        "category": "absent",
        "should_abstain": True,
        "key_terms": None,
    },
    {
        "id": 12,
        "question": "How many Azure customers does Microsoft have?",
        "category": "absent",
        "should_abstain": True,
        "key_terms": None,
    },
    {
        "id": 13,
        "question": "What is Microsoft's revenue guidance for next fiscal year?",
        "category": "absent",
        "should_abstain": True,
        "key_terms": None,
    },
    {
        "id": 16,
        "question": "Does the filing name specific competitor companies such as Google or Amazon?",
        "category": "absent",
        "should_abstain": True,
        "key_terms": None,
    },
    {
        "id": 20,
        "question": "Does the filing name the EU Digital Markets Act?",
        "category": "absent",
        "should_abstain": True,
        "key_terms": None,
    },
    # ---------- Category 3: SPECIFIC ----------
    {
        "id": 14,
        "question": "What are the named risk categories in Item 1A?",
        "category": "specific",
        "should_abstain": False,
        "key_terms": ["strategic", "operational", "intellectual property"],
    },
    {
        "id": 15,
        "question": "Which specific privacy or data regulations does the filing name?",
        "category": "specific",
        "should_abstain": False,
        "key_terms": ["gdpr"],
    },
    {
        "id": 17,
        "question": "What does the filing say about unionization?",
        "category": "specific",
        "should_abstain": False,
        "key_terms": ["union"],
    },
    {
        "id": 18,
        "question": "Does the filing mention any acquisition by name?",
        "category": "specific",
        "should_abstain": False,
        "key_terms": ["activision"],
    },
    # ---------- Harder traps (verified against term counts) ----------
    {
        # antitrust appears (count 1) but as risk, not as a realized penalty
        # "this year". Tests risk-vs-realized distinction.
        "id": 21,
        "question": "Does the filing state that competition has led to specific antitrust penalties against Microsoft this year?",
        "category": "absent",
        "should_abstain": True,
        "key_terms": None,
    },
    {
        # LinkedIn count is 1 - it IS named. So the honest answer is YES,
        # the filing does reference LinkedIn. Correct behavior is to ANSWER,
        # not abstain. This trap tests whether the model finds a real but
        # easy-to-miss mention rather than assuming absence.
        "id": 22,
        "question": "Does the filing reference LinkedIn?",
        "category": "specific",
        "should_abstain": False,
        "key_terms": ["linkedin"],
    },
    {
        # "employees" appears 15 times but a HEADCOUNT NUMBER is not given in
        # risk factors (that's Item 1, Business). Tests false-premise handling.
        "id": 23,
        "question": "How many employees does the risk factors section say Microsoft has?",
        "category": "absent",
        "should_abstain": True,
        "key_terms": None,
    },
    {
        # climate (2) and environmental (4) both appear - genuinely answerable.
        "id": 24,
        "question": "Does the filing discuss climate-related or environmental risk?",
        "category": "answerable",
        "should_abstain": False,
        "key_terms": ["climate"],
    },
    {
        # Risk factors are qualitative; they describe risks without dollar
        # figures. Tests over-claiming on quantification.
        "id": 25,
        "question": "Does the filing quantify the potential dollar impact of any specific risk?",
        "category": "answerable",          # was "absent"
        "should_abstain": False,           # was True
        "key_terms": ["28.9"],             # the specific figure a correct answer cites
    },
]