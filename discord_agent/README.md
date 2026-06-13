# Discord Command Center

A Discord command center that routes monitored messages into one persistent
control thread per source channel. AI drafts and automatic AI replies are not
used.

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
   - Set `MONGODB_URI` to the MongoDB Atlas connection string.
   - Optionally set `MONGODB_DB_NAME`; it defaults to `discord_agent_db`.

## Running the Bot
```bash
python main.py
```

## Features
- **Smart Priority Tagging**: Automatically tags incoming messages as Urgent, Business, or Normal based on keywords.
- **Channel Threads**: Each monitored source channel maps to exactly one control thread, including after a normal process restart.
- **MongoDB Persistence**: Messages, channel configuration, thread mappings, and counters are stored in MongoDB. The database and indexes are initialized automatically.
- **Seven-Day Cleanup**: A channel thread is deleted after seven days without a routed message. A later matching question creates a new thread.
- **Manual Replies**: Messages typed by an operator in a control thread are sent to that thread's source channel.
- **Human Typing Simulation**: Calculates dynamic typing duration based on your message length before sending it back.

## Render Deployment

The included `render.yaml` creates a Render web service:

1. Push this repository to GitHub, GitLab, or Bitbucket.
2. In Render, create a new Blueprint and select the repository.
3. Set `DISCORD_TOKEN`, `CONTROL_SERVER_ID`, `CONTROL_CHANNEL_ID`, and
   `MONGODB_URI` when Render prompts for secret environment variables.
4. Deploy and open `https://<service-name>.onrender.com/api/status` to check
   the service.

`PORT` is read automatically from Render.

### Allow Render to Reach MongoDB

Whitelisting your home IP does not allow the deployed service to reach Atlas.
After creating the Render service:

1. Open the service in Render.
2. Select `Connect`, then open the `Outbound` tab.
3. Copy every listed outbound CIDR range.
4. Ask the Atlas project owner to add those ranges under `Network Access`.
5. Redeploy the Render service after the Atlas entries become active.

### Keep the Free Service Awake

Render free web services spin down after 15 minutes without inbound traffic.
For hobby or test use, configure UptimeRobot to check the status endpoint:

1. In UptimeRobot, create a new `HTTP(s)` monitor.
2. Enter `https://<service-name>.onrender.com/api/status` as the URL.
3. Set the monitoring interval to 5 minutes.
4. Start the monitor and confirm that the endpoint reports HTTP 200.

Important limitations of Render's free plan:

- Render can still restart or suspend a free service, so this is not a
  production uptime guarantee.
- The free service filesystem is ephemeral. Runtime state is stored in
  MongoDB, so it is not dependent on Render's local filesystem.
- Render describes free instances as suitable for testing, not production.
