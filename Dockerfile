FROM python:3.11-slim

# Prevent interactive prompts & ensure real-time logging
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=0

# Install Linux system dependencies required by Chromium/Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    curl \
    ca-certificates \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements from discord_agent and install Python dependencies
COPY discord_agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-install Chromium browser inside package directory at build time
RUN python -m playwright install chromium

# Copy application files
COPY discord_agent/ .

# Render assigns PORT dynamically (defaults to 10000)
EXPOSE 10000

CMD ["python", "main.py"]
