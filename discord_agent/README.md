# Discord Command Center

A production-grade, modular Discord self-bot prototype that routes incoming messages to a private command center (Control Server) and allows an operator to manually respond with realistic typing simulations, priority tagging, and quick templates.

## Prerequisites
- Python 3.11+
- A test Discord account (Do NOT use your main account, self-bots are against Discord ToS).

## Setup
1. Create a virtual environment and install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and fill in your credentials.
   - To get your Discord Token: Open Discord in browser -> F12 -> Network -> Look for an API request -> Copy `Authorization` header.
   - To get Server/Channel IDs: Enable Developer Mode in Discord, right-click the server/channel and select "Copy ID".

## Running the Bot
```bash
python main.py
```

## Features
- **Smart Priority Tagging**: Automatically tags incoming messages as Urgent, Business, or Normal based on keywords.
- **Quick Templates**: In the control thread, type `!reply greeting` to automatically send a pre-canned response.
- **Human Typing Simulation**: Calculates dynamic typing duration based on your message length before sending it back.
