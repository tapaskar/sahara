"""Scam signals in what a caller says or what a parent reports.

Heuristics first, because they are cheap, explainable and work offline; the
model then weighs them with the transcript when it is available. Patterns cover
English, Hindi and common romanised Hindi, which is what scam scripts use.
"""
from __future__ import annotations

import re

PATTERNS: list[tuple[str, float, str]] = [  # (regex, weight, label)
    (r"\botp\b|one[- ]time password|ओटीपी|ओ टी पी", 0.6, "asks for OTP"),
    (r"\b(pin|cvv|card number|upi pin|यूपीआई पिन|पिन)\b", 0.5, "asks for PIN/CVV"),
    (r"kyc|केवाईसी|account (will be )?(blocked|suspended|closed)|खाता (बंद|ब्लॉक)", 0.5, "KYC/account-block threat"),
    (r"digital arrest|डिजिटल अरेस्ट|arrest warrant|गिरफ्तार|giraftar", 0.9, "digital arrest"),
    (r"\b(cbi|police|narcotics|customs|ed|enforcement directorate|crime branch)\b|पुलिस|सीबीआई", 0.5, "impersonates police/agency"),
    (r"parcel|courier|fedex|dhl|पार्सल|कूरियर", 0.4, "parcel scam"),
    (r"lottery|prize|jackpot|won|लॉटरी|इनाम|jeeta|jeete", 0.5, "lottery/prize"),
    (r"electricity (bill|connection)|बिजली (बिल|कनेक्शन)|power cut|disconnect", 0.4, "electricity disconnection"),
    (r"anydesk|teamviewer|screen ?share|install (this|the) app|ऐप (इंस्टॉल|डाउनलोड)", 0.7, "remote access app"),
    (r"aadhaar|आधार|pan card|पैन", 0.3, "asks for Aadhaar/PAN"),
    (r"transfer|send money|pay (now|immediately)|पैसे भेज|तुरंत", 0.4, "asks for money now"),
    (r"do not tell anyone|keep (it|this) (secret|confidential)|किसी को (मत|न) बता", 0.6, "demands secrecy"),
    (r"bank (manager|officer|fraud)|sbi|hdfc|icici|rbi|बैंक (से|का)", 0.3, "claims to be the bank"),
    (r"(within|in) (\d+|one|two|ten) (minutes|hours)|last chance|final (notice|warning)|आखिरी", 0.3, "urgency"),
]


def heuristic_risk(text: str) -> tuple[float, list[str]]:
    """Returns (risk 0..1, matched labels). Two independent signals compound."""
    t = (text or "").lower()
    hits = [(w, label) for rx, w, label in PATTERNS if re.search(rx, t)]
    if not hits:
        return 0.0, []
    risk = 1.0
    for w, _ in hits:
        risk *= (1 - w)
    return round(min(0.99, 1 - risk), 2), [label for _, label in hits]


def verdict(risk: float) -> str:
    return "block" if risk >= 0.6 else ("message" if risk >= 0.25 else "connect")
