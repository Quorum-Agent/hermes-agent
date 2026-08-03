"""Request compiler — Python port of packages/core/src/request-compiler.ts.

Zero external dependencies. Deterministic heuristic classifier + sensitive-data
detector that runs before any model sees the prompt.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass

from hermes_routing.software_taxonomy import (
    detect_software_reference,
    is_named_software_follow_up,
)
from hermes_routing.types import (
    ChatMessage,
    ChatRequest,
    CompiledRequest,
    PromptAnalyzerResult,
    RequestAnalysis,
    RequestIntent,
    RequestRequirements,
    SensitiveDataCategory,
)
from hermes_routing.uuid import random_uuid

# ---------------------------------------------------------------------------
# Compiled regexes — verbatim from the TS source
# ---------------------------------------------------------------------------

_MODEL_FAMILY = r"(?:qwen|llama|mistral|mixtral|gemma|deepseek|falcon|phi-\d|gpt(?:-\w+)?|claude|gemini|grok)"
_TIME_SENSITIVE_SUBJECT = (
    r"(?:news|events?|laws?|regulations?|prices?|weather|versions?|releases?|models?|llms?|"
    + _MODEL_FAMILY
    + r"|exchange rates?|schedules?|scores?|officeholders?|presidents?|ceos?)"
)
_SUPERLATIVE_CLAIM = (
    r"(?:best|top|leading|strongest|fastest|newest|most (?:capable|advanced|powerful|recent)|"
    r"state[- ]of[- ]the[- ]art|cutting[- ]edge)"
)
_PUBLIC_SCOPE_CLAIM = (
    r"(?:publicly |commercially )?available|on the market|open[- ]weights?|open[- ]source|"
    r"in the (?:public )?(?:market|wild)"
)
_RANKED_PUBLIC_PATTERN = re.compile(
    rf"\b{_SUPERLATIVE_CLAIM}\b[^.!?\n]{{0,80}}\b(?:{_PUBLIC_SCOPE_CLAIM})\b"
    rf"|\b(?:{_PUBLIC_SCOPE_CLAIM})\b[^.!?\n]{{0,80}}\b{_SUPERLATIVE_CLAIM}\b",
    re.IGNORECASE,
)
_EXPLICIT_FRESHNESS_PATTERN = re.compile(
    rf"(?:\b(?:latest|recent|live|current(?:ly)?|today|this week|up[- ]to[- ]date)\b"
    rf"[^.!?\n]{{0,60}}\b{_TIME_SENSITIVE_SUBJECT}\b"
    rf"|\b{_TIME_SENSITIVE_SUBJECT}\b"
    rf"[^.!?\n]{{0,60}}\b(?:latest|recent|live|current(?:ly)?|today|this week|up[- ]to[- ]date)\b)",
    re.IGNORECASE,
)
_CONTEXTUAL_CURRENT_PATTERN = re.compile(
    rf"(?:\bcurrent(?:ly)?\b[^.!?\n]{{0,60}}\b{_TIME_SENSITIVE_SUBJECT}\b"
    rf"|\b{_TIME_SENSITIVE_SUBJECT}\b[^.!?\n]{{0,60}}\bcurrent(?:ly)?\b)",
    re.IGNORECASE,
)
_EXPLICIT_CHAT_PATTERN = re.compile(
    r"\b(tell me a joke|just chat|casual conversation|new topic|let'?s (?:just )?talk)\b",
    re.IGNORECASE,
)
_CODE_DOMAIN_PATTERN = re.compile(
    r"\b(compiler|stack trace|source code|codebase|repository|api endpoint|rest endpoint|"
    r"database schema|unit tests?|microservices?|programmatically|http\s+[45]\d{2})\b",
    re.IGNORECASE,
)
_CODE_ACTION_PATTERN = re.compile(
    r"\b(write|implement|refactor|debug|fix|compile|program|code|containeri[sz]e|optimi[sz]e|"
    r"review|test|add|change|edit|build|design|create|analy[sz]e|undo|use)\b",
    re.IGNORECASE,
)
_CODE_TARGET_PATTERN = re.compile(
    r"\b(code|function|method|class|interface|type|script|service|endpoint|query|component|"
    r"bug|error|repository|module|package|algorithm|commit|array)\b",
    re.IGNORECASE,
)
_CODE_BLOCK_PATTERN = re.compile(
    r"```[\s\S]*```|(?:^|\n)\s*(?:const|let|var|def|fn|class|interface)\s+",
    re.IGNORECASE,
)
_REASONING_DOMAIN_PATTERN = re.compile(
    r"\b(equation|theorem|proof|prove|square root|sqrt|integral|derivative|probability|"
    r"statistics|combinatorics|algebra|geometry|arithmetic|syllogism|logical|logic puzzle)\b",
    re.IGNORECASE,
)
_REASONING_ACTION_PATTERN = re.compile(
    r"\b(calculate|solve|derive|reason|analy[sz]e|evaluate|determine)\b",
    re.IGNORECASE,
)
_REASONING_TARGET_PATTERN = re.compile(
    r"\b(problem|equation|math(?:ematics)?|logic|argument|proof|puzzle|probability|claim|"
    r"area|circle|radius|volume|distance|percentage)\b",
    re.IGNORECASE,
)
_MATH_EXPRESSION_PATTERN = re.compile(
    r"(?:\d|\bx\b)\s*(?:[+\-*/^=]|\b(?:plus|minus|times|divided by)\b)\s*(?:\d|\bx\b)",
    re.IGNORECASE,
)
_ANALYTICAL_DECISION_PATTERN = re.compile(
    r"\b(?:compare|evaluate|assess|weigh)\b[^.!?\n]{0,160}\b(?:architectures?|approaches?|"
    r"options?|trade[- ]offs?|designs?|strategies?|patterns?)\b"
    r"|\b(?:recommend|choose|decide)\b[^.!?\n]{0,160}\b(?:architecture|approach|option|design|"
    r"strategy|pattern|starting point)\b",
    re.IGNORECASE,
)
_DOCUMENT_PATTERN = re.compile(
    r"\b(?:attached|this|the)\s+(?:pdf|document|invoice|contract|spreadsheet|attachment|file)\b"
    r"|\b(?:summari[sz]e|extract|parse|review|read)\b[^.!?\n]{0,40}\b"
    r"(?:pdf|document|invoice|contract|spreadsheet|attachment|file)\b",
    re.IGNORECASE,
)
_VISUAL_SUBJECT = r"(?:image|photo|picture|diagram|screenshot|pcb)"
_REFERENCED_VISUAL = rf"(?:(?:this|that|attached|uploaded)\s+{_VISUAL_SUBJECT}|{_VISUAL_SUBJECT}\s+(?:attached|uploaded))"
_VISION_PATTERN = re.compile(
    rf"(?:\b(?:analy[sz]e|inspect|describe|read|extract|identify|recognize|interpret|look at)\b"
    rf"[^.!?\n]{{0,80}}\b{_REFERENCED_VISUAL}\b"
    rf"|\b(?:this|that|attached|uploaded)\s+{_VISUAL_SUBJECT}\b[^.!?\n]{{0,80}}"
    rf"\b(?:explain|show|contain|depict|mean|say)\b"
    rf"|\bwhat(?:'s| is| are| does| do)\b[^.!?\n]{{0,40}}\b(?:in|on)\s+{_REFERENCED_VISUAL}\b)",
    re.IGNORECASE,
)
_EXPLICIT_RESEARCH_PATTERN = re.compile(
    r"\b(?:research|compare interpretations|search (?:the )?web|browse (?:the )?web|"
    r"look (?:it |this |that )?up online)\b",
    re.IGNORECASE,
)
_EXPLICIT_WEB_REQUEST_PATTERN = re.compile(
    r"\b(?:search (?:the )?web|browse (?:the )?web|look (?:it |this |that )?up online|"
    r"use (?:the )?internet|external (?:web )?sources?)\b",
    re.IGNORECASE,
)
_EVIDENCE_REQUEST_PATTERN = re.compile(
    r"\b(?:provide|include|cite|show|give)\b[^.!?\n]{0,40}\b(?:an?\s+|the\s+)?(?:sources?|citations?)\b"
    r"|\bwhat (?:sources?|citations?)\b"
    r"|\bsources?\s+(?:for|on|about|supporting)\b",
    re.IGNORECASE,
)
_NETWORK_DENIAL_PATTERN = re.compile(
    r"\b(?:(?:do not|don'?t|never)\s+(?:use|search|access|contact)\s+"
    r"|without\s+(?:using\s+|accessing\s+)?|no\s+)"
    r"(?:the\s+)?(?:internet|web|network|online services?|external services?)\b"
    r"|\boffline(?:[ -]only)?\b"
    r"|\b(?:local[ -]only|only (?:my|the) local)\b",
    re.IGNORECASE,
)
_LOCAL_SCOPE_PATTERN = re.compile(
    r"\b(?:my|the|only)\s+local\s+(?:notes?|files?|documents?|database|sources?|context)\b"
    r"|\blocal\s+(?:notes?|files?|documents?|database|sources?|context)\s+only\b",
    re.IGNORECASE,
)
_PAYMENT_CARD_CANDIDATE_PATTERN = re.compile(r"(?:\d[ -]*?){13,19}")
_PERSONAL_SCOPE_PATTERN = re.compile(
    r"\b(?:my|our|me|mine|ours|patient(?:'s)?)\b"
    r"[^.!?\n]{0,80}\b(?:address|appointment|bank|benefits?|birthday|date of birth|diagnosis|"
    r"email|health|hiv|insurance|medication|medical|payroll|phone|prescription|price|record|"
    r"salary|schedule|ssn|social security|treatment)\b",
    re.IGNORECASE,
)

_CONFUSABLE_ASCII: dict[str, str] = {
    "\u0430": "a",  # Cyrillic small a
    "\u0435": "e",  # Cyrillic small ie
    "\u043e": "o",  # Cyrillic small o
    "\u0440": "p",  # Cyrillic small er
    "\u0441": "c",  # Cyrillic small es
    "\u0445": "x",  # Cyrillic small ha
    "\u0443": "y",  # Cyrillic small u
    "\u043a": "k",  # Cyrillic small ka
    "\u043c": "m",  # Cyrillic small em
    "\u0442": "t",  # Cyrillic small te
    "\u043d": "h",  # Cyrillic small en
}

_COURTESY_PREFIX_PATTERN = re.compile(
    r"^(?:(?:thank you|thanks(?:\s+(?:so much|a lot))?|okay|ok|great|got it|understood|"
    r"makes sense|perfect)[\s.!,:;-]+)+",
    re.IGNORECASE,
)
_FOLLOW_UP_PATTERN = re.compile(
    r"^(?:(?:and|also|now|then|next|okay,?\s+now)\b"
    r"|what(?:'s| is)\s+(?:the\s+)?(?:latest|current)\b"
    r"|(?:what|how) about\s+(?:(?:it|that|this|those|them)\b|"
    r"(?:for|in|on|with|using|doing|implementing|running)\b)"
    r"|(?:please\s+)?(?:continue|retry|redo)"
    r"(?:\s+(?:(?:with\s+)?(?:it|that|this|those|them)|"
    r"(?:the|this|that)\s+[a-z0-9_-]+(?:\s+[a-z0-9_-]+){0,4}))?\s*[.!?]*$"
    r"|(?:please\s+)?(?:keep going|go on|carry on)\s*[.!?]*$"
    r"|(?:please\s+)?(?:make|change|fix|explain|summari[sz]e|add|remove|update|use|adapt|"
    r"integrate|convert|port)\s+(?:it|that|this|those|them)\b"
    r"|(?:why|how|are you sure)\??$)",
    re.IGNORECASE,
)
_COMPARATIVE_FOLLOW_UP_PATTERN = re.compile(
    r"^(?:(?:are|is)\s+there\s+(?:(?:any|a)\s+)?(?:better|other|alternative)\s+"
    r"(?:ways?|options?|approach(?:es)?|methods?|solutions?)"
    r"|(?:what|any)\s+(?:other|better|alternative)\s+"
    r"(?:ways?|options?|approach(?:es)?|methods?|solutions?)(?:\s+are\s+there)?"
    r"|(?:any\s+)?alternatives?|what\s+else)\??$",
    re.IGNORECASE,
)
_MODAL_REFERENTIAL_FOLLOW_UP_PATTERN = re.compile(
    r"^(?:(?:can|could|would|should|will|may|might|does|do|did|is|are|was|were)\s+"
    r"(?:this|that|it|these|those|they)\b"
    r"|(?:can|could|would|should|may|might)\s+(?:i|we|you)\s+"
    r"(?:use|apply|adapt|integrate|implement|extend|reuse|port)\s+"
    r"(?:this|that|it|these|those|them)\b)",
    re.IGNORECASE,
)

# For hasHighEntropyToken
_HIGH_ENTROPY_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9+/_=-]{40,}")


# ---------------------------------------------------------------------------
# Helper functions (verbatim from TS)
# ---------------------------------------------------------------------------


def _strip_format_chars(s: str) -> str:
    """Strip Unicode format characters (category Cf).

    Equivalent of JS /\\p{Cf}/gu replace."""
    return "".join(ch for ch in s if unicodedata.category(ch) != "Cf")


def _normalized_follow_up_prompt(prompt: str) -> str:
    return _COURTESY_PREFIX_PATTERN.sub("", prompt.strip()).strip()


def _is_contextual_follow_up(prompt: str) -> bool:
    normalized = _normalized_follow_up_prompt(prompt)
    return (
        _FOLLOW_UP_PATTERN.search(normalized) is not None
        or _COMPARATIVE_FOLLOW_UP_PATTERN.search(normalized) is not None
        or _MODAL_REFERENTIAL_FOLLOW_UP_PATTERN.search(normalized) is not None
    )


def _requires_freshness(prompt: str) -> bool:
    return (
        _EXPLICIT_FRESHNESS_PATTERN.search(prompt) is not None
        or _CONTEXTUAL_CURRENT_PATTERN.search(prompt) is not None
        or _RANKED_PUBLIC_PATTERN.search(prompt) is not None
    )


def _normalize_for_sensitive_detection(content: str) -> str:
    normalized = unicodedata.normalize("NFKC", content)
    stripped = _strip_format_chars(normalized)
    result: list[str] = []
    for ch in stripped:
        lower = ch.lower()
        result.append(_CONFUSABLE_ASCII.get(lower, ch))
    return "".join(result)


def _luhn_valid(candidate: str) -> bool:
    digits = re.sub(r"\D", "", candidate)
    if len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    double_digit = False
    for idx in range(len(digits) - 1, -1, -1):
        value = int(digits[idx])
        if double_digit:
            value *= 2
            if value > 9:
                value -= 9
        total += value
        double_digit = not double_digit
    return total % 10 == 0


def _has_high_entropy_token(content: str) -> bool:
    for match in _HIGH_ENTROPY_TOKEN_PATTERN.finditer(content):
        token = re.sub(r"=+$", "", match.group(0))
        # Absolute workspace/temp paths can be long and character-diverse
        # without being credentials. Explicit key/JWT/PEM detectors still run
        # independently, so exclude only multi-segment absolute path tokens
        # from this generic entropy fallback.
        if token.startswith("/") and token.count("/") >= 2:
            continue
        character_classes = sum(
            1
            for present in (
                bool(re.search(r"[a-z]", token)),
                bool(re.search(r"[A-Z]", token)),
                bool(re.search(r"\d", token)),
                bool(re.search(r"[+/_-]", token)),
            )
            if present
        )
        if character_classes < 3:
            continue
        frequencies: dict[str, int] = {}
        for ch in token:
            frequencies[ch] = frequencies.get(ch, 0) + 1
        entropy = 0.0
        token_len = len(token)
        for count in frequencies.values():
            prob = count / token_len
            entropy -= prob * math.log2(prob)
        if entropy >= 4.25:
            return True
    return False


# ---------------------------------------------------------------------------
# Public sensitive-content detection
# ---------------------------------------------------------------------------


def detect_sensitive_content(content: str) -> list[SensitiveDataCategory]:
    normalized = _normalize_for_sensitive_detection(content)
    categories: set[SensitiveDataCategory] = set()

    # --- credentials ---
    if (
        re.search(
            r"\b(?:password|passwd|passphrase|db[_ -]?pass|api[_ -]?key|secret|"
            r"access[_ -]?token|bearer[_ -]?token|credentials?)\s*(?::|=|is)\s*"
            r"(?!an?\b|the\b|used\b|needed\b|defined\b|a\s+way\b)\S{4,}",
            normalized,
            re.IGNORECASE,
        )
        or re.search(r"\bAKIA[0-9A-Z]{16}\b", normalized)
        or re.search(r"\bAIza[0-9A-Za-z_-]{35}\b", normalized)
        or re.search(r"\bsk-(?:[A-Za-z0-9_-]{10,})\b", normalized)
        or re.search(
            r"\bsk_(?:live|test)_[A-Za-z0-9_-]{12,}\b", normalized, re.IGNORECASE
        )
        or re.search(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", normalized)
        or re.search(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b", normalized)
        or re.search(
            r"\bauthorization\s*:\s*(?:basic|bearer)\s+[A-Za-z0-9._~+/=-]+\b",
            normalized,
            re.IGNORECASE,
        )
        or re.search(
            r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
            normalized,
        )
        or re.search(
            r"\b(?:accountkey|sharedaccesssignature)\s*=\s*[^;\s]+",
            normalized,
            re.IGNORECASE,
        )
        or re.search(
            r"\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis):\/\/[^@\s/]+:[^@\s/]+@",
            normalized,
            re.IGNORECASE,
        )
        or re.search(
            r"\b[A-Z][A-Z0-9_]*(?:SECRET|PASSWORD|PASS|API_KEY|TOKEN|CREDENTIAL)[A-Z0-9_]*\s*=\s*\S{4,}",
            normalized,
        )
        or re.search(
            r"\b(?:my|this|the)\s+(?:password|secret|private[ _-]?key|api[ _-]?key|"
            r"credentials?|access[ _-]?token)\b",
            normalized,
            re.IGNORECASE,
        )
        or re.search(
            r"\buse\s+(?:this|the|my)\s+(?:api[ _-]?key|password|secret|credentials?)\b",
            normalized,
            re.IGNORECASE,
        )
        or _has_high_entropy_token(normalized)
    ):
        categories.add("credentials")

    # --- private_key ---
    if (
        re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", normalized, re.IGNORECASE)
        or re.search(r"\bMII[A-Za-z0-9+/]{45,}={0,2}\b", normalized)
        or re.search(
            r"\bprivate[ _-]?key\s*(?::|=|is)\s*[A-Za-z0-9+/=\s]{48,}",
            normalized,
            re.IGNORECASE,
        )
    ):
        categories.add("private_key")

    # --- government_id ---
    if re.search(r"\b\d{3}-\d{2}-\d{4}\b", normalized) or re.search(
        r"\b(?:ssn|social security(?: number)?|social)\s*(?:number|no\.?)?\s*(?::|=|is)?\s*\d{9}\b",
        normalized,
        re.IGNORECASE,
    ):
        categories.add("government_id")

    # --- financial (IBAN / account / routing) ---
    if re.search(
        r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b", normalized, re.IGNORECASE
    ) or re.search(
        r"\b(?:account|routing|iban)\s*(?:number|no\.?)?\s*(?::|=|is)\s*[A-Z0-9 -]{8,34}",
        normalized,
        re.IGNORECASE,
    ):
        categories.add("financial")

    # --- financial (payment card via Luhn) ---
    card_context = (
        re.search(r"\b(?:card|credit|debit|payment)\b", normalized, re.IGNORECASE)
        is not None
        or re.search(r"\d[ -]+\d", normalized) is not None
    )
    if card_context:
        candidates = _PAYMENT_CARD_CANDIDATE_PATTERN.findall(normalized)
        if any(_luhn_valid(c) for c in candidates):
            categories.add("financial")

    # --- personal_contact ---
    # The first check (email/phone/etc with colon/equals/is) is sufficient
    # on its own.  The three auxiliary checks only matter when the first
    # pattern did NOT fire — they gate on 2+ matches (address + birthday, etc).
    personal_contact_direct = bool(
        re.search(
            r"\b(?:email|phone|telephone|mobile|address|date of birth|dob)\s*(?::|=|is)\s*\S{5,}",
            normalized,
            re.IGNORECASE,
        )
    )
    personal_contact_aux = sum(
        1
        for pattern in (
            r"\b(?:born|birth(?:day)?)\s+(?:on\s+)?\d{4}-\d{2}-\d{2}\b",
            r"\b\d{1,5}\s+[A-Za-z0-9.' -]{2,40}\s+(?:st(?:reet)?|ave(?:nue)?|rd|road|blvd|"
            r"boulevard|lane|ln|drive|dr)\b",
            r"\b(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b",
        )
        if re.search(pattern, normalized, re.IGNORECASE)
    )
    if personal_contact_direct or personal_contact_aux >= 2:
        categories.add("personal_contact")

    # --- health ---
    if re.search(
        r"\b(?:i|we|my|our|me|mine|ours|patient(?:'s)?)\b[^.!?\n]{0,80}\b"
        r"(?:appointment|benefits?|diagnos(?:is|ed)|health|hiv|insurance|medication|"
        r"medical|prescription|record|schedule|treatment)\b",
        normalized,
        re.IGNORECASE,
    ) or re.search(
        r"\b(?:diagnosis|medical record|patient id|prescription)\s*(?::|=|is)\s*\S+",
        normalized,
        re.IGNORECASE,
    ):
        categories.add("health")

    # --- financial (personal-bank) ---
    if re.search(
        r"\b(?:my|our|me|mine|ours)\b[^.!?\n]{0,80}\b(?:bank|payroll|price|salary)\b",
        normalized,
        re.IGNORECASE,
    ):
        categories.add("financial")

    # --- personal_contact (personal address/birthday/email/phone) ---
    if re.search(
        r"\b(?:my|our|me|mine|ours)\b[^.!?\n]{0,80}\b(?:address|birthday|date of birth|email|phone)\b",
        normalized,
        re.IGNORECASE,
    ):
        categories.add("personal_contact")

    # --- confidential ---
    if (
        re.search(
            r"\b(?:confidential|proprietary|restricted data|do not distribute)\s*(?::|=)\s*\S+",
            normalized,
            re.IGNORECASE,
        )
        or re.search(
            r"\b(?:INTERNAL ONLY|DO NOT DISTRIBUTE|RESTRICTED DATA)\b", normalized
        )
        or re.search(
            r"\b(?:summari[sz]e|review|analy[sz]e|read|upload)\b[^.!?\n]{0,60}\bconfidential\b",
            normalized,
            re.IGNORECASE,
        )
    ):
        categories.add("confidential")

    return list(categories)


def contains_sensitive_content(content: str) -> bool:
    return len(detect_sensitive_content(content)) > 0


# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------


@dataclass
class IntentClassification:
    intent: RequestIntent
    confidence: float
    requires_freshness: bool
    explicit_reset: bool
    source: str = "default"  # added by classify_conversation


def _classify_prompt(prompt: str) -> IntentClassification:
    fresh_required = _requires_freshness(prompt)
    software_reference = detect_software_reference(prompt)
    coding_signal = (
        software_reference is not None
        or _CODE_DOMAIN_PATTERN.search(prompt) is not None
        or _CODE_BLOCK_PATTERN.search(prompt) is not None
        or (
            _CODE_ACTION_PATTERN.search(prompt) is not None
            and _CODE_TARGET_PATTERN.search(prompt) is not None
        )
    )

    if _EXPLICIT_CHAT_PATTERN.search(prompt):
        return IntentClassification(
            intent="conversation",
            confidence=0.98,
            requires_freshness=False,
            explicit_reset=True,
        )

    if (
        _EXPLICIT_RESEARCH_PATTERN.search(prompt)
        or _EXPLICIT_WEB_REQUEST_PATTERN.search(prompt)
        or fresh_required
    ):
        return IntentClassification(
            intent="research",
            confidence=0.98,
            requires_freshness=fresh_required,
            explicit_reset=False,
        )

    if _DOCUMENT_PATTERN.search(prompt):
        return IntentClassification(
            intent="document",
            confidence=0.9,
            requires_freshness=False,
            explicit_reset=False,
        )

    if _VISION_PATTERN.search(prompt):
        return IntentClassification(
            intent="vision",
            confidence=0.94,
            requires_freshness=False,
            explicit_reset=False,
        )

    if coding_signal:
        if software_reference == "strong" or _CODE_DOMAIN_PATTERN.search(prompt):
            conf = 0.94
        elif software_reference == "contextual":
            conf = 0.82
        else:
            conf = 0.86
        return IntentClassification(
            intent="coding",
            confidence=conf,
            requires_freshness=False,
            explicit_reset=False,
        )

    if _EVIDENCE_REQUEST_PATTERN.search(prompt):
        return IntentClassification(
            intent="research",
            confidence=0.9,
            requires_freshness=False,
            explicit_reset=False,
        )

    reasoning_signal = (
        _ANALYTICAL_DECISION_PATTERN.search(prompt) is not None
        or _REASONING_DOMAIN_PATTERN.search(prompt) is not None
        or _MATH_EXPRESSION_PATTERN.search(prompt) is not None
        or (
            _REASONING_ACTION_PATTERN.search(prompt) is not None
            and _REASONING_TARGET_PATTERN.search(prompt) is not None
        )
    )
    if reasoning_signal:
        if _ANALYTICAL_DECISION_PATTERN.search(prompt):
            conf = 0.9
        elif _REASONING_DOMAIN_PATTERN.search(prompt):
            conf = 0.92
        else:
            conf = 0.84
        return IntentClassification(
            intent="reasoning",
            confidence=conf,
            requires_freshness=False,
            explicit_reset=False,
        )

    return IntentClassification(
        intent="conversation",
        confidence=0.5,
        requires_freshness=False,
        explicit_reset=False,
    )


def _classify_conversation(messages: list[ChatMessage]) -> IntentClassification:
    user_messages = [m for m in messages if m.role == "user" and m.content.strip()]
    last_user = user_messages[-1] if user_messages else None
    current = _classify_prompt(last_user.content if last_user else "")
    latest_prompt = last_user.content.strip() if last_user else ""
    contextual_follow_up = _is_contextual_follow_up(latest_prompt)
    named_software_follow_up = is_named_software_follow_up(latest_prompt)

    # Not conversational or explicit reset or not a follow-up → use current
    if (
        current.intent != "conversation"
        or current.explicit_reset
        or (not contextual_follow_up and not named_software_follow_up)
    ):
        source = "default" if current.intent == "conversation" else "current"
        current.source = source
        return current

    # Find latest user index in full messages
    latest_user_index = -1
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.role == "user" and m.content.strip():
            latest_user_index = i
            break

    # Check previous assistant execution for intent carry-over
    if latest_user_index >= 0:
        for m in reversed(messages[:latest_user_index]):
            if m.role == "assistant" and m.execution is not None:
                prev_analysis = m.execution.plan.analysis
                if prev_analysis.intent != "conversation" and (
                    not named_software_follow_up or prev_analysis.intent == "coding"
                ):
                    return IntentClassification(
                        intent=prev_analysis.intent,
                        confidence=min(prev_analysis.confidence, 0.78),
                        requires_freshness=False,
                        explicit_reset=False,
                        source="conversation",
                    )
                break  # Only check the most recent assistant before this message

    # Walk back through user messages
    for idx in range(len(user_messages) - 2, -1, -1):
        prev = user_messages[idx]
        classification = _classify_prompt(prev.content)
        if classification.explicit_reset:
            break
        if classification.intent != "conversation" and (
            not named_software_follow_up or classification.intent == "coding"
        ):
            return IntentClassification(
                intent=classification.intent,
                confidence=min(classification.confidence, 0.78),
                requires_freshness=False,
                explicit_reset=False,
                source="conversation",
            )
        if not _is_contextual_follow_up(prev.content):
            break

    current.source = "default"
    return current


def _derive_requirements(
    messages: list[ChatMessage],
    classification: IntentClassification,
) -> RequestRequirements:
    intent = classification.intent
    capabilities: list[str] = ["chat"]

    if intent == "coding":
        capabilities.append("coding")
    if intent == "reasoning":
        capabilities.append("reasoning")
    if intent == "document":
        capabilities.append("documents")
    if intent == "vision":
        capabilities.append("vision")
    if intent == "research":
        capabilities.append("reasoning")
        # latest user prompt for network directive analysis
        latest_user_prompt = ""
        for m in reversed(messages):
            if m.role == "user" and m.content.strip():
                latest_user_prompt = m.content.strip()
                break

        latest_prior_network_directive = None
        if classification.source == "conversation":
            for m in reversed(messages[:-1]):
                if m.role == "user" and m.content.strip():
                    directive = {
                        "blocks": bool(
                            _NETWORK_DENIAL_PATTERN.search(m.content)
                            or _LOCAL_SCOPE_PATTERN.search(m.content)
                        ),
                        "authorizes": bool(
                            _requires_freshness(m.content)
                            or _EXPLICIT_WEB_REQUEST_PATTERN.search(m.content)
                            or _EVIDENCE_REQUEST_PATTERN.search(m.content)
                        ),
                    }
                    if directive["blocks"] or directive["authorizes"]:
                        latest_prior_network_directive = directive
                        break

        prior_prompt_authorizes_web = (
            latest_prior_network_directive is not None
            and latest_prior_network_directive["authorizes"]
            and not latest_prior_network_directive["blocks"]
        )
        current_prompt_blocks_web = (
            _NETWORK_DENIAL_PATTERN.search(latest_user_prompt) is not None
            or _LOCAL_SCOPE_PATTERN.search(latest_user_prompt) is not None
            or _PERSONAL_SCOPE_PATTERN.search(latest_user_prompt) is not None
        )
        current_prompt_authorizes_web = not current_prompt_blocks_web and (
            classification.requires_freshness
            or _EXPLICIT_WEB_REQUEST_PATTERN.search(latest_user_prompt) is not None
            or _EVIDENCE_REQUEST_PATTERN.search(latest_user_prompt) is not None
        )
        if (
            classification.source != "classifier"
            and not current_prompt_blocks_web
            and (current_prompt_authorizes_web or prior_prompt_authorizes_web)
        ):
            capabilities.append("web")

    # Sensitive data detection: only scan user-authored content
    sensitive_cats: list[SensitiveDataCategory] = []
    seen: set[SensitiveDataCategory] = set()
    for m in messages:
        if m.role == "user":
            for cat in detect_sensitive_content(m.content):
                if cat not in seen:
                    seen.add(cat)
                    sensitive_cats.append(cat)

    # Web-grounded data: check assistant messages
    contains_web_grounded = False
    for m in messages:
        if m.role == "assistant":
            if m.provenance == "web_grounded":
                contains_web_grounded = True
                break
            if m.execution is not None and m.execution.plan.web_search is not None:
                sources = m.execution.plan.web_search.get("sources", [])
                if len(sources) > 0:
                    contains_web_grounded = True
                    break

    return RequestRequirements(
        intent=intent,
        intent_confidence=classification.confidence,
        intent_source=classification.source,
        capabilities=capabilities,
        requires_freshness=classification.requires_freshness,
        contains_sensitive_data=len(sensitive_cats) > 0,
        sensitive_data_categories=sensitive_cats,
        contains_web_grounded_data=contains_web_grounded,
    )


def _compact_summary(prompt: str) -> str:
    single_line = re.sub(r"\s+", " ", prompt).strip()
    if len(single_line) > 240:
        return single_line[:239] + "\u2026"
    return single_line


def _valid_analyzer_result(analysis: PromptAnalyzerResult) -> PromptAnalyzerResult:
    if (
        not math.isfinite(analysis.confidence)
        or analysis.confidence < 0
        or analysis.confidence > 1
    ):
        raise ValueError("Prompt analyzer returned an invalid confidence.")
    task_summary = _compact_summary(analysis.task_summary)
    if not task_summary:
        raise ValueError("Prompt analyzer returned an empty task summary.")
    return PromptAnalyzerResult(
        intent=analysis.intent,
        confidence=analysis.confidence,
        task_summary=task_summary,
    )


# ---------------------------------------------------------------------------
# RequestCompiler
# ---------------------------------------------------------------------------


class RequestCompiler:
    def compile(self, input: ChatRequest) -> CompiledRequest:
        user_messages = [
            m for m in input.messages if m.role == "user" and m.content.strip()
        ]
        prompt = user_messages[-1].content.strip() if user_messages else ""

        if not prompt:
            raise ValueError("A non-empty user message is required.")

        classification = _classify_conversation(input.messages)
        return CompiledRequest(
            id=random_uuid(),
            conversation_id=input.conversation_id,
            messages=input.messages,
            prompt=prompt,
            policy=input.policy,
            verbosity=input.verbosity if input.verbosity is not None else "standard",
            analysis=RequestAnalysis(
                source="heuristic",
                intent=classification.intent,
                confidence=classification.confidence,
                task_summary=_compact_summary(prompt),
            ),
            requirements=_derive_requirements(input.messages, classification),
        )

    def apply_prompt_analysis(
        self,
        request: CompiledRequest,
        analyzer: dict | object,
        incoming: PromptAnalyzerResult,
    ) -> CompiledRequest:
        analysis = _valid_analyzer_result(incoming)
        heuristic = request.analysis
        protected_conversation_context = (
            request.requirements.intent_source == "conversation"
            and heuristic.intent != "conversation"
        )
        conflicting_strong_heuristic = heuristic.intent != analysis.intent and (
            heuristic.confidence >= 0.84 or protected_conversation_context
        )
        analyzer_is_usable = analysis.confidence >= 0.65
        use_analyzer = analyzer_is_usable and not conflicting_strong_heuristic

        if use_analyzer:
            selected_intent = analysis.intent
            selected_confidence = analysis.confidence
            selected_source = "classifier"
        else:
            selected_intent = heuristic.intent
            selected_confidence = heuristic.confidence
            selected_source = request.requirements.intent_source

        # Extract analyzer id and label — works with both dict and object
        if isinstance(analyzer, dict):
            analyzer_id = analyzer.get("id", "")
            analyzer_label = analyzer.get("label", "")
        else:
            analyzer_id = getattr(analyzer, "id", "")
            analyzer_label = getattr(analyzer, "label", "")

        merged_analysis = RequestAnalysis(
            source="local_model" if use_analyzer else "hybrid",
            intent=selected_intent,
            confidence=selected_confidence,
            task_summary=(
                analysis.task_summary if use_analyzer else heuristic.task_summary
            ),
        )
        # Attach analyzer metadata via object.__setattr__ since it's not a dataclass field
        from hermes_routing.types import AnalyzerRef

        merged_analysis.analyzer = AnalyzerRef(
            model_id=analyzer_id,
            model_label=analyzer_label,
            intent=analysis.intent,
            confidence=analysis.confidence,
        )

        requirements = _derive_requirements(
            request.messages,
            IntentClassification(
                intent=selected_intent,
                confidence=selected_confidence,
                requires_freshness=request.requirements.requires_freshness,
                explicit_reset=False,
                source=selected_source,
            ),
        )

        # Preserve web capability from the original request if the new
        # classification is still "research" but the heuristic dropped it
        if (
            selected_intent == "research"
            and "web" in request.requirements.capabilities
            and "web" not in requirements.capabilities
        ):
            requirements.capabilities.append("web")

        return CompiledRequest(
            id=request.id,
            conversation_id=request.conversation_id,
            messages=request.messages,
            prompt=request.prompt,
            policy=request.policy,
            verbosity=request.verbosity,
            analysis=merged_analysis,
            requirements=requirements,
        )
