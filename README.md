# AutoMinds Voice Agent

AI voice assistant with persistent memory, Google Workspace integration, and phone access via Twilio.

## Features

- **Gemini 2.0 Flash** — 1M token context window for deep memory
- **Persistent Memory** — SQLite + Google Drive backup (survives redeploys)
- **Google Workspace** — Live Gmail, Calendar, Drive, Sheets, Docs, Contacts
- **Phone Calls** — Twilio inbound/outbound via +1-855-529-0581
- **Multi-Channel** — Works with OpenClaw, Telegram (@amixnbot), and voice
- **Memory Extraction** — Auto-extracts facts, preferences, and action items

## Architecture

```
Phone Call → Twilio → Flask → Gemini 2.0 Flash
                                  ↕
                          SQLite Memory DB ←→ Google Drive Backup
                                  ↕
                          Google Workspace APIs
```

## Setup on a New Machine

1. **Clone and install:**
   ```bash
   git clone https://github.com/kinglyonz/autominds-voice.git
   cd autominds-voice
   pip install -r requirements.txt
   ```

2. **Set environment variables:**
   ```bash
   export GEMINI_API_KEY="your-key"
   export GOOGLE_TOKENS='{"client_id":"...","client_secret":"...","refresh_token":"..."}'
   export TWILIO_ACCOUNT_SID="your-sid"
   export TWILIO_AUTH_TOKEN="your-token"
   export TWILIO_PHONE_NUMBER="+18555290581"
   ```

3. **Run locally:**
   ```bash
   python app.py
   ```

4. **Deploy to Railway:**
   ```bash
   railway link
   railway up --service autominds-voice
   ```

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check + memory DB status |
| `/voice/incoming` | POST | Twilio incoming call webhook |
| `/voice/process` | POST | Speech processing + AI response |
| `/callme` | GET/POST | Outbound call (AI calls you) |
| `/memory/stats` | GET | View memory statistics |
| `/memory/backup` | POST | Trigger Drive backup |
| `/memory/export` | GET | Export full memory as JSON |

## Memory System

- **Short-term**: Last 50 messages kept in full for active context
- **Long-term**: Facts, preferences, and goals auto-extracted and stored
- **Summaries**: Conversations compressed into summaries after calls end
- **Backup**: Auto-syncs to Google Drive every 5 minutes + on call end
- **Restore**: Memory auto-restored from Drive on container restart
2. Click your number
3. Under "Voice & Fax", set:
   - **A CALL COMES IN:** Webhook
   - **URL:** `https://your-voice-service.up.railway.app/voice/incoming`
   - **HTTP:** POST

## Testing

```bash
# Health check
curl https://your-voice-service.up.railway.app/health

# Test incoming call simulation
curl -X POST https://your-voice-service.up.railway.app/voice/incoming \
  -d "From=+1234567890"
```

## Environment Variables

- `PORT` - Server port (default: 5000)
- `ORCHESTRATOR_API` - Main bot API URL (optional)
