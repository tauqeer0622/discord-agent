def format_discord_timestamp(dt) -> str:
    """Formats a datetime object to Discord's relative/absolute timestamp format."""
    # Using Discord's built in timestamp format <t:timestamp:F>
    return f"<t:{int(dt.timestamp())}:F>"

def extract_attachments(message) -> str:
    """Returns a formatted string of attachment URLs from a message."""
    if not message.attachments:
        return "None"
    
    urls = [att.url for att in message.attachments]
    return "\n".join(urls)
