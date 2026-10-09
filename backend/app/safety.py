"""Prompt-injection defences that run before and around the model."""
import re

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Tags the prompts use as delimiters; users must not be able to forge or close them.
_DELIMITERS = re.compile(r"<\s*/?\s*(question|system|assistant|human|schema|current_sql|sql)\s*>", re.I)

_INJECTION_PATTERNS = [
    r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(instructions?|rules?|prompts?|guidelines?|guardrails?|restrictions?|constraints?)\b",
    r"\b(reveal|show|print|repeat|display|leak|output|tell me|what (is|are|were))\b[^.\n]{0,30}\b(system|hidden|initial|original|your)\b[^.\n]{0,15}\b(prompt|instructions?|rules)\b",
    r"\b(bypass|disable|turn off|circumvent)\b[^.\n]{0,25}\b(safety|guardrails?|filters?|restrictions?|read-?only|validation)\b",
    r"\b(developer|debug|god|jailbreak|dan)\s+mode\b",
    r"\bjailbreak\b",
    r"\b(pretend|act) (to be|as if you|that you)\b",
    r"\byou are (now|no longer)\b",
    r"<\s*/?\s*(system|assistant)\s*>",
    r"^\s*(system|assistant)\s*:",
]
_INJECTION = re.compile("|".join(f"(?:{p})" for p in _INJECTION_PATTERNS), re.I | re.M)


def sanitize(text: str, limit: int = 2000) -> str:
    """Drop control characters and any delimiter tags so text cannot break out of its wrapper."""
    return _DELIMITERS.sub("", _CONTROL.sub("", text)).strip()[:limit]


def wrap_question(text: str) -> str:
    return f"<question>{sanitize(text)}</question>"


def looks_like_injection(text: str) -> bool:
    """Cheap deterministic screen; the classifier model is the second line of defence."""
    return bool(_INJECTION.search(text))
