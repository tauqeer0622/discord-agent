FROM python:3.11-slim

# Prevent interactive prompts & ensure real-time logging
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PORT=10000

# Aggressive Linux glibc & Python memory tuning to prevent heap fragmentation under 512MB limit
ENV PYTHONMALLOC=malloc
ENV MALLOC_TRIM_THRESHOLD_=65536
ENV MALLOC_ARENA_MAX=2
ENV AUTO_START=false

WORKDIR /app

# Copy requirements from discord_agent and install Python dependencies
COPY discord_agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-install Chromium browser WITH ALL official OS system dependencies
RUN python -m playwright install --with-deps chromium

# Copy application files
COPY discord_agent/ .

# Render assigns PORT dynamically (defaults to 10000)
EXPOSE 10000

CMD ["python", "main.py"]
