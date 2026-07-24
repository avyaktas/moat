"""Evaluation questions for the risk-factor analyzer, as structured data.

Ported from eval_questions.md and verified against Microsoft's FY2025
10-K Item 1A (69,079 chars, report date 2025-06-30).

Fields:
    id             matches the number in eval_questions.md
    question       the prompt sent to the analyzer
    category       "answerable" | "absent" | "specific"
    should_abstain True if the correct behavior is "not addressed"
    key_terms      lowercase substrings a correct answer should contain,
                   checked case-insensitively. None for abstain cases.

NOTE: key_terms encode YOUR definition of a correct answer. Review each
one - they are a starting point, not gospel. Too strict and correct
answers fail; too loose and wrong answers pass. Tuning these is part of
building a good eval.
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
        "key_terms": ["ai"],
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
        "key_terms": ["talent", "employ"],
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
]