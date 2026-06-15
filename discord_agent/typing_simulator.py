import asyncio
import random
import logging

logger = logging.getLogger(__name__)

def calculate_typing_duration(message_content: str) -> float:
    """
    Calculates dynamic typing duration based on message length.
    - Short messages (<50 chars): 3-5 sec
    - Medium messages (50-150 chars): 5-10 sec
    - Long messages (>150 chars): 10-20 sec
    """
    length = len(message_content)
    
    if length < 50:
        duration = random.uniform(3.0, 5.0)
    elif length < 150:
        duration = random.uniform(5.0, 10.0)
    else:
        duration = random.uniform(10.0, 20.0)
        
    return duration

async def simulate_typing_and_send(channel, content: str):
    """
    Simulates typing in the given channel for a calculated duration,
    then sends the message.
    """
    duration = calculate_typing_duration(content)
    logger.info(f"Simulating typing for {duration:.1f} seconds in {channel.name if hasattr(channel, 'name') else 'DM'}...")
    
    try:
        async with channel.typing():
            await asyncio.sleep(duration)
    except Exception as e:
        logger.warning(f"Typing simulation failed (probably self-bot limitation): {e}. Proceeding with delay anyway.")
        await asyncio.sleep(duration)
        
    # Send the actual message
    try:
        await channel.send(content)
        logger.info(f"Successfully sent message to {channel.name if hasattr(channel, 'name') else 'DM'}.")
        return True
    except Exception as e:
        logger.error(f"Failed to send message: {e}")
        return False
