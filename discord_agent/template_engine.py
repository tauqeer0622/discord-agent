import logging

logger = logging.getLogger(__name__)

# Pre-canned templates
TEMPLATES = {
    "greeting": "Hello! Thank you for reaching out. How can I help you today?",
    "busy": "I'm currently away or occupied at the moment, but I'll get back to you as soon as possible.",
    "thanks": "Thank you for the information! I appreciate it.",
    "collab": "Thanks for reaching out regarding a collaboration. Please send over the details and I'll review them shortly.",
}

def expand_template(content: str) -> str:
    """
    Checks if the operator's message starts with a !reply command and expands it.
    If no match or not a command, returns the original content.
    """
    if not content.startswith("!reply "):
        return content

    # Extract the template key
    parts = content.split(" ", 1)
    if len(parts) < 2:
        return content
    
    key = parts[1].strip().lower()
    
    # Check if the key is in our templates
    for template_key, template_value in TEMPLATES.items():
        if key.startswith(template_key):
            logger.info(f"Template Engine: Expanded '!reply {template_key}'")
            return template_value

    logger.warning(f"Template Engine: Unknown template key '{key}'")
    return content
