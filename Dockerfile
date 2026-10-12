FROM python:3.11-slim

# Prevent interactive prompts & ensure real-time logging
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PORT=10000

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
