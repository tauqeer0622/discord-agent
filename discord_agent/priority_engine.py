import re
import logging

logger = logging.getLogger(__name__)

# Define priority keywords
URGENT_KEYWORDS = [r"\bhelp\b", r"\burgent\b", r"\basap\b", r"\bbroken\b", r"\bissue\b", r"\berror\b", r"\bfix\b", r"\bcritical\b"]
BUSINESS_KEYWORDS = [r"\bcollab\b", r"\bcollaboration\b", r"\bsponsor\b", r"\bsponsorship\b", r"\bpricing\b", r"\bbusiness\b", r"\bpartnership\b"]

def evaluate_priority(content: str) -> str:
    """
    Evaluates message content locally and assigns a priority tag.
    Returns the tag string (e.g. 🔴 URGENT).
    """
    if not content:
        return "⚪ NORMAL"

    content_lower = content.lower()

    # Check for urgent keywords
    for pattern in URGENT_KEYWORDS:
        if re.search(pattern, content_lower):
            logger.debug(f"Priority Engine: Tagged URGENT due to pattern {pattern}")
            return "🔴 URGENT"

    # Check for business keywords
    for pattern in BUSINESS_KEYWORDS:
        if re.search(pattern, content_lower):
            logger.debug(f"Priority Engine: Tagged BUSINESS due to pattern {pattern}")
            return "💼 BUSINESS"

    return "⚪ NORMAL"

# Define target question keywords to filter messages
TARGET_QUESTION_KEYWORDS = [
    r"\bhow to\b", r"\bhelp\b", r"\bpricing\b", r"\bissue\b", 
    r"\bcost\b", r"\bcollab\b", r"\bsupport\b", r"\berror\b",
    r"\bwhy\b", r"\bwhat is\b", r"\bcan you\b", r"\bquestion\b",
    r"\btest\b", r"\bhi\b", r"\bhey\b", r"\bhello\b", r"\bsup\b",
    r"\bmarket\b", r"\bcrypto\b", r"\bbtc\b", r"\beth\b", r"\bstock\b",
    r"\byield\b", r"\boptions\b", r"\bcalls\b", r"\bputs\b"
]

def is_target_question(content: str) -> bool:
    """
    Determines if the incoming message is a relevant question that the bot should handle.
    Returns True if it matches any target keywords.
    """
    if not content:
        return False
        
    content_lower = content.lower()
    for pattern in TARGET_QUESTION_KEYWORDS:
        if re.search(pattern, content_lower):
            return True
            
    # Also fetch if it ends with a question mark
    if content.strip().endswith("?"):
        return True
        
    return False
