#!/usr/bin/env python3
"""
AutoMinds Voice Service
Handles all Twilio voice conversations with Gemini AI + Google Workspace
Persistent memory via SQLite + Google Drive backup
"""

from flask import Flask, request, jsonify
from twilio.twiml.voice_response import VoiceResponse, Gather
from twilio.rest import Client as TwilioClient
import os
import json
import sqlite3
import threading
import time
from datetime import datetime, timezone

app = Flask(__name__)

# Twilio credentials (set via environment variables)
TWILIO_SID = os.environ.get('TWILIO_ACCOUNT_SID', '')
TWILIO_AUTH = os.environ.get('TWILIO_AUTH_TOKEN', '')
TWILIO_NUMBER = os.environ.get('TWILIO_PHONE_NUMBER', '')
VOICE_URL = os.environ.get('VOICE_URL', 'https://autominds-voice-production.up.railway.app')

# Google tokens (stored as env var JSON)
GOOGLE_TOKENS = os.environ.get('GOOGLE_TOKENS', '')

# --- Persistent Memory System ---
DB_PATH = os.environ.get('MEMORY_DB_PATH', '/tmp/autominds_memory.db')
DRIVE_MEMORY_FILE = 'autominds_voice_memory.json'
_db_lock = threading.Lock()


def init_db():
    """Initialize SQLite database for conversation memory"""
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        # Per-call message log
        c.execute('''CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )''')
        # Long-term memory / facts the AI learns about the user
        c.execute('''CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            fact TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            timestamp TEXT NOT NULL,
            UNIQUE(phone, fact)
        )''')
        # Conversation summaries (compressed history)
        c.execute('''CREATE TABLE IF NOT EXISTS summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            summary TEXT NOT NULL,
            message_count INTEGER DEFAULT 0,
            timestamp TEXT NOT NULL
        )''')
        conn.commit()
        conn.close()
    print("[MEMORY] SQLite database initialized")


def save_message(phone, role, content):
    """Save a single message to the database"""
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('INSERT INTO messages (phone, role, content, timestamp) VALUES (?, ?, ?, ?)',
                  (phone, role, content, datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()


def get_conversation_history(phone, limit=50):
    """Get recent conversation history for a phone number"""
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''SELECT role, content, timestamp FROM messages
                      WHERE phone = ? ORDER BY id DESC LIMIT ?''', (phone, limit))
        rows = c.fetchall()
        conn.close()
    # Reverse so oldest first
    rows.reverse()
    return [{'role': r[0], 'content': r[1], 'timestamp': r[2]} for r in rows]


def get_all_history(phone):
    """Get ALL conversation history for a phone number (for big context)"""
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT role, content, timestamp FROM messages WHERE phone = ? ORDER BY id', (phone,))
        rows = c.fetchall()
        conn.close()
    return [{'role': r[0], 'content': r[1], 'timestamp': r[2]} for r in rows]


def save_memory(phone, fact, category='general'):
    """Save a long-term memory/fact about the user"""
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO memories (phone, fact, category, timestamp) VALUES (?, ?, ?, ?)',
                      (phone, fact, category, datetime.now(timezone.utc).isoformat()))
            conn.commit()
        except Exception as e:
            print(f"[MEMORY] Failed to save memory: {e}")
        conn.close()


def get_memories(phone):
    """Get all long-term memories for a phone number"""
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT fact, category, timestamp FROM memories WHERE phone = ? ORDER BY id', (phone,))
        rows = c.fetchall()
        conn.close()
    return [{'fact': r[0], 'category': r[1], 'timestamp': r[2]} for r in rows]


def save_summary(phone, summary, message_count):
    """Save a conversation summary"""
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('INSERT INTO summaries (phone, summary, message_count, timestamp) VALUES (?, ?, ?, ?)',
                  (phone, summary, message_count, datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()


def get_summaries(phone):
    """Get conversation summaries for a phone number"""
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT summary, message_count, timestamp FROM summaries WHERE phone = ? ORDER BY id', (phone,))
        rows = c.fetchall()
        conn.close()
    return [{'summary': r[0], 'message_count': r[1], 'timestamp': r[2]} for r in rows]


def get_message_count(phone):
    """Get total message count for a phone number"""
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM messages WHERE phone = ?', (phone,))
        count = c.fetchone()[0]
        conn.close()
    return count


def export_memory_to_json():
    """Export entire memory database to JSON (for Drive backup)"""
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT phone, role, content, timestamp FROM messages ORDER BY id')
        messages = [{'phone': r[0], 'role': r[1], 'content': r[2], 'timestamp': r[3]} for r in c.fetchall()]
        c.execute('SELECT phone, fact, category, timestamp FROM memories ORDER BY id')
        memories = [{'phone': r[0], 'fact': r[1], 'category': r[2], 'timestamp': r[3]} for r in c.fetchall()]
        c.execute('SELECT phone, summary, message_count, timestamp FROM summaries ORDER BY id')
        summaries = [{'phone': r[0], 'summary': r[1], 'message_count': r[2], 'timestamp': r[3]} for r in c.fetchall()]
        conn.close()
    return {'messages': messages, 'memories': memories, 'summaries': summaries,
            'exported_at': datetime.now(timezone.utc).isoformat()}


def import_memory_from_json(data):
    """Import memory database from JSON (from Drive restore)"""
    with _db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        for m in data.get('messages', []):
            try:
                c.execute('INSERT INTO messages (phone, role, content, timestamp) VALUES (?, ?, ?, ?)',
                          (m['phone'], m['role'], m['content'], m['timestamp']))
            except Exception:
                pass
        for m in data.get('memories', []):
            try:
                c.execute('INSERT OR IGNORE INTO memories (phone, fact, category, timestamp) VALUES (?, ?, ?, ?)',
                          (m['phone'], m['fact'], m['category'], m['timestamp']))
            except Exception:
                pass
        for s in data.get('summaries', []):
            try:
                c.execute('INSERT INTO summaries (phone, summary, message_count, timestamp) VALUES (?, ?, ?, ?)',
                          (s['phone'], s['summary'], s['message_count'], s['timestamp']))
            except Exception:
                pass
        conn.commit()
        conn.close()
    print(f"[MEMORY] Imported {len(data.get('messages', []))} messages, "
          f"{len(data.get('memories', []))} memories, {len(data.get('summaries', []))} summaries")


def backup_memory_to_drive():
    """Backup memory database to Google Drive"""
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaInMemoryUpload

        tokens = json.loads(GOOGLE_TOKENS) if GOOGLE_TOKENS else None
        if not tokens:
            print("[MEMORY] No Google tokens - skipping Drive backup")
            return

        creds = Credentials(
            token=tokens.get('access_token'),
            refresh_token=tokens.get('refresh_token'),
            token_uri='https://oauth2.googleapis.com/token',
            client_id=tokens.get('client_id'),
            client_secret=tokens.get('client_secret')
        )
        drive = build('drive', 'v3', credentials=creds)

        # Export memory
        memory_data = export_memory_to_json()
        content = json.dumps(memory_data, indent=2).encode('utf-8')
        media = MediaInMemoryUpload(content, mimetype='application/json')

        # Check if file already exists
        results = drive.files().list(
            q=f"name='{DRIVE_MEMORY_FILE}' and trashed=false",
            spaces='drive', fields='files(id, name)'
        ).execute()
        files = results.get('files', [])

        if files:
            # Update existing
            drive.files().update(fileId=files[0]['id'], media_body=media).execute()
            print(f"[MEMORY] Updated Drive backup: {DRIVE_MEMORY_FILE}")
        else:
            # Create new
            file_metadata = {'name': DRIVE_MEMORY_FILE, 'mimeType': 'application/json'}
            drive.files().create(body=file_metadata, media_body=media).execute()
            print(f"[MEMORY] Created Drive backup: {DRIVE_MEMORY_FILE}")
    except Exception as e:
        print(f"[MEMORY] Drive backup failed: {e}")


def restore_memory_from_drive():
    """Restore memory database from Google Drive on startup"""
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        import io

        tokens = json.loads(GOOGLE_TOKENS) if GOOGLE_TOKENS else None
        if not tokens:
            print("[MEMORY] No Google tokens - skipping Drive restore")
            return

        creds = Credentials(
            token=tokens.get('access_token'),
            refresh_token=tokens.get('refresh_token'),
            token_uri='https://oauth2.googleapis.com/token',
            client_id=tokens.get('client_id'),
            client_secret=tokens.get('client_secret')
        )
        drive = build('drive', 'v3', credentials=creds)

        # Find the memory file
        results = drive.files().list(
            q=f"name='{DRIVE_MEMORY_FILE}' and trashed=false",
            spaces='drive', fields='files(id, name)'
        ).execute()
        files = results.get('files', [])

        if files:
            file_id = files[0]['id']
            content = drive.files().get_media(fileId=file_id).execute()
            data = json.loads(content.decode('utf-8'))
            import_memory_from_json(data)
            print(f"[MEMORY] Restored memory from Drive backup")
        else:
            print("[MEMORY] No Drive backup found - starting fresh")
    except Exception as e:
        print(f"[MEMORY] Drive restore failed: {e}")


def periodic_backup():
    """Background thread: backup memory to Drive every 5 minutes"""
    while True:
        time.sleep(300)  # 5 minutes
        try:
            backup_memory_to_drive()
        except Exception as e:
            print(f"[MEMORY] Periodic backup error: {e}")


# Active call sessions (ephemeral, just tracks active calls)
active_calls = {}


def get_gmail_service():
    """Get authenticated Gmail service using stored tokens"""
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        
        tokens = json.loads(GOOGLE_TOKENS) if GOOGLE_TOKENS else None
        if not tokens:
            return None
        
        creds = Credentials(
            token=tokens.get('access_token'),
            refresh_token=tokens.get('refresh_token'),
            token_uri='https://oauth2.googleapis.com/token',
            client_id=tokens.get('client_id'),
            client_secret=tokens.get('client_secret')
        )
        return build('gmail', 'v1', credentials=creds)
    except Exception as e:
        print(f"[ERROR] Gmail service init failed: {e}")
        return None


def get_calendar_service():
    """Get authenticated Calendar service using stored tokens"""
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        
        tokens = json.loads(GOOGLE_TOKENS) if GOOGLE_TOKENS else None
        if not tokens:
            return None
        
        creds = Credentials(
            token=tokens.get('access_token'),
            refresh_token=tokens.get('refresh_token'),
            token_uri='https://oauth2.googleapis.com/token',
            client_id=tokens.get('client_id'),
            client_secret=tokens.get('client_secret')
        )
        return build('calendar', 'v3', credentials=creds)
    except Exception as e:
        print(f"[ERROR] Calendar service init failed: {e}")
        return None


@app.route('/', methods=['GET'])
def home():
    return {'status': 'AutoMinds Voice Service Running', 'version': '2.0-memory'}, 200


@app.route('/health', methods=['GET'])
def health():
    return {'status': 'healthy', 'active_calls': len(active_calls),
            'memory_db': os.path.exists(DB_PATH)}, 200


@app.route('/memory/stats', methods=['GET'])
def memory_stats():
    """Show memory statistics"""
    phone = request.args.get('phone', '+17037954193')
    messages = get_all_history(phone)
    memories = get_memories(phone)
    summaries = get_summaries(phone)
    return jsonify({
        'phone': phone,
        'total_messages': len(messages),
        'long_term_memories': len(memories),
        'conversation_summaries': len(summaries),
        'memories': memories,
        'last_5_messages': messages[-5:] if messages else []
    }), 200


@app.route('/memory/backup', methods=['POST'])
def trigger_backup():
    """Manually trigger a memory backup to Google Drive"""
    backup_memory_to_drive()
    return jsonify({'status': 'backup_triggered'}), 200


@app.route('/memory/export', methods=['GET'])
def export_memory():
    """Export full memory as JSON"""
    data = export_memory_to_json()
    return jsonify(data), 200


@app.route('/voice/incoming', methods=['POST'])
def voice_incoming():
    """Handle incoming calls"""
    response = VoiceResponse()
    from_number = request.form.get('From', '')

    # Track active call
    active_calls[from_number] = {'started': datetime.now(timezone.utc).isoformat()}

    # Check if we have history with this caller
    msg_count = get_message_count(from_number)
    memories = get_memories(from_number)

    # Personalized greeting based on memory
    if msg_count > 0:
        greeting = f"Hey Khalil, welcome back! We've had {msg_count} exchanges so far. What's on your mind?"
    else:
        greeting = "Hey Khalil! I'm your AI assistant, connected to your Gmail, Calendar, and Drive. I remember everything we talk about. What do you need?"

    gather = Gather(
        input='speech',
        action='/voice/process',
        timeout=3,
        speech_timeout='auto',
        language='en-US',
        hints='help, status, goals, opportunities, leads, revenue, remember, recall'
    )
    gather.say(greeting, voice='Polly.Joanna')

    response.append(gather)
    response.say("I didn't catch that. What can I help you with?", voice='Polly.Joanna')
    response.redirect('/voice/incoming')

    return str(response), 200, {'Content-Type': 'text/xml'}


@app.route('/voice/process', methods=['POST'])
def voice_process():
    """Process speech and respond with AI"""
    response = VoiceResponse()
    speech = request.form.get('SpeechResult', '')
    from_number = request.form.get('From', '')

    print(f"[VOICE] {from_number} said: {speech}")

    if not speech:
        response.say("I didn't hear anything. Let's try again.", voice='Polly.Joanna')
        response.redirect('/voice/incoming')
        return str(response), 200, {'Content-Type': 'text/xml'}

    # Save user message to persistent memory
    save_message(from_number, 'user', speech)

    # Check for goodbye
    if any(word in speech.lower() for word in ['goodbye', 'bye', 'hang up', 'end call', 'that\'s all']):
        response.say(
            "Great talking with you! Everything we discussed is saved in my memory. Talk soon!",
            voice='Polly.Joanna'
        )
        response.hangup()
        active_calls.pop(from_number, None)
        # Summarize this conversation and backup
        try:
            summarize_and_extract(from_number)
            backup_memory_to_drive()
        except Exception as e:
            print(f"[MEMORY] Post-call processing failed: {e}")
        return str(response), 200, {'Content-Type': 'text/xml'}

    # Get AI response
    try:
        ai_response = get_ai_response(speech, from_number)
    except Exception as e:
        print(f"[ERROR] AI response failed: {e}")
        ai_response = "I'm having trouble connecting right now. Can you try asking again?"

    print(f"[VOICE] AI responds: {ai_response}")

    # Save AI response to persistent memory
    save_message(from_number, 'assistant', ai_response)

    # Extract any new facts/memories from the conversation
    try:
        extract_memories_from_message(from_number, speech)
    except Exception:
        pass

    # Respond and continue conversation
    gather = Gather(
        input='speech',
        action='/voice/process',
        timeout=3,
        speech_timeout='auto',
        language='en-US'
    )
    gather.say(ai_response, voice='Polly.Joanna')
    response.append(gather)

    response.say("Anything else?", voice='Polly.Joanna')
    response.redirect('/voice/process')

    return str(response), 200, {'Content-Type': 'text/xml'}


def get_ai_response(user_speech, from_number):
    """Get AI response using Gemini with full persistent memory + Google Workspace"""
    # Get persistent conversation history (last 50 messages for context)
    history = get_conversation_history(from_number, limit=50)
    history_text = "\n".join([f"{'User' if m['role']=='user' else 'AI'}: {m['content']}" for m in history])

    # Get long-term memories about this person
    memories = get_memories(from_number)
    memories_text = ""
    if memories:
        memories_text = "THINGS YOU REMEMBER ABOUT THIS PERSON:\n"
        memories_text += "\n".join([f"- [{m['category']}] {m['fact']}" for m in memories])

    # Get conversation summaries (compressed older history)
    summaries = get_summaries(from_number)
    summaries_text = ""
    if summaries:
        summaries_text = "SUMMARIES OF PAST CONVERSATIONS:\n"
        summaries_text += "\n".join([f"[{s['timestamp'][:10]}] {s['summary']}" for s in summaries])

    # Get workspace context
    context = get_workspace_context(user_speech)

    return get_gemini_response(user_speech, history_text, context, memories_text, summaries_text)


def extract_memories_from_message(phone, user_speech):
    """Extract facts/preferences from user speech and save as long-term memory"""
    # Simple keyword-based fact extraction
    speech_lower = user_speech.lower()

    # Detect personal facts worth remembering
    fact_patterns = [
        ('preference', ['i like', 'i love', 'i prefer', 'i hate', 'i enjoy', 'my favorite']),
        ('personal', ['my name is', 'i live in', 'i work at', 'my job is', 'i am a', "i'm a"]),
        ('goal', ['i want to', 'i need to', 'my goal is', 'i plan to', "i'm trying to", 'i aim to']),
        ('business', ['my company', 'my business', 'my startup', 'my product', 'my client',
                       'revenue', 'profit', 'growth']),
        ('reminder', ['remind me', 'don\'t forget', 'remember that', 'keep in mind']),
    ]

    for category, keywords in fact_patterns:
        for kw in keywords:
            if kw in speech_lower:
                # Save the whole sentence as a memory
                save_memory(phone, user_speech.strip(), category)
                print(f"[MEMORY] Saved {category} memory: {user_speech[:60]}...")
                return


def summarize_and_extract(phone):
    """After a call ends, summarize the conversation and extract key facts"""
    try:
        import google.generativeai as genai
        genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))
        model = genai.GenerativeModel('gemini-2.0-flash-exp')

        # Get unsummarized messages
        all_msgs = get_all_history(phone)
        if len(all_msgs) < 4:  # Not enough to summarize
            return

        conversation_text = "\n".join([f"{'User' if m['role']=='user' else 'AI'}: {m['content']}" for m in all_msgs[-20:]])

        prompt = f"""Analyze this conversation and return a JSON response:
{{
  "summary": "2-3 sentence summary of what was discussed",
  "facts": ["list of important facts, preferences, or information the user shared"],
  "action_items": ["any tasks or follow-ups mentioned"]
}}

Conversation:
{conversation_text}

Return ONLY valid JSON, nothing else."""

        response = model.generate_content(prompt)
        text = response.text.strip()

        # Clean up markdown if present
        if text.startswith('```'):
            text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()

        result = json.loads(text)

        # Save summary
        if result.get('summary'):
            save_summary(phone, result['summary'], len(all_msgs))

        # Save extracted facts as memories
        for fact in result.get('facts', []):
            save_memory(phone, fact, 'extracted')

        for item in result.get('action_items', []):
            save_memory(phone, f"ACTION: {item}", 'action_item')

        print(f"[MEMORY] Post-call extraction: {len(result.get('facts', []))} facts, "
              f"{len(result.get('action_items', []))} actions")

    except Exception as e:
        print(f"[MEMORY] Summarization failed: {e}")


def get_workspace_context(user_speech):
    """Pull relevant Google Workspace context using Google APIs directly"""
    import base64
    context_parts = []
    speech_lower = user_speech.lower()

    try:
        # If asking about email/inbox
        if any(word in speech_lower for word in ['email', 'mail', 'inbox', 'unread', 'message', 'messages']):
            gmail = get_gmail_service()
            if gmail:
                results = gmail.users().messages().list(
                    userId='me', q='is:unread', maxResults=5
                ).execute()
                messages = results.get('messages', [])
                email_summaries = []
                for msg in messages[:5]:
                    data = gmail.users().messages().get(userId='me', id=msg['id'], format='metadata',
                        metadataHeaders=['From', 'Subject']).execute()
                    headers = {h['name']: h['value'] for h in data['payload']['headers']}
                    email_summaries.append(f"- From: {headers.get('From', '?')} | Subject: {headers.get('Subject', '?')}")
                if email_summaries:
                    context_parts.append(f"Unread emails ({len(messages)} total):\n" + "\n".join(email_summaries))

        # If asking about calendar/schedule/meetings
        if any(word in speech_lower for word in ['calendar', 'schedule', 'meeting', 'meetings', 'today', 'tomorrow', 'event', 'events', 'busy', 'free']):
            cal = get_calendar_service()
            if cal:
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc).isoformat()
                events_result = cal.events().list(
                    calendarId='primary', timeMin=now,
                    maxResults=5, singleEvents=True, orderBy='startTime'
                ).execute()
                events = events_result.get('items', [])
                event_summaries = []
                for event in events:
                    start = event['start'].get('dateTime', event['start'].get('date'))
                    event_summaries.append(f"- {event.get('summary', 'No title')} at {start}")
                if event_summaries:
                    context_parts.append(f"Upcoming events:\n" + "\n".join(event_summaries))

    except Exception as e:
        print(f"[CONTEXT] Workspace context fetch failed: {e}")

    return "\n\n".join(context_parts) if context_parts else ""


def get_gemini_response(user_speech, history_text, workspace_context="", memories_text="", summaries_text=""):
    """Get AI response from Gemini with full memory + workspace awareness"""
    try:
        import google.generativeai as genai

        genai.configure(api_key=os.environ.get('GEMINI_API_KEY'))
        model = genai.GenerativeModel('gemini-2.0-flash-exp')

        # Build memory sections
        memory_section = ""
        if memories_text:
            memory_section += f"\n{memories_text}\n"
        if summaries_text:
            memory_section += f"\n{summaries_text}\n"

        workspace_section = ""
        if workspace_context:
            workspace_section = f"""
LIVE DATA FROM YOUR GOOGLE WORKSPACE:
{workspace_context}

Use this real data to answer the user's question. Reference specific emails, events, or files by name."""

        prompt = f"""You are Khalil's personal AI assistant with PERFECT MEMORY. You remember EVERYTHING from all past conversations.
You have LIVE access to his Gmail, Calendar, Google Drive, Sheets, and Docs.

CRITICAL INSTRUCTIONS:
- Keep responses SHORT (2-3 sentences max) since this is a phone call
- Be natural, warm, and conversational like a trusted friend
- Reference past conversations and memories when relevant - show you remember
- If the user mentions something important (a name, goal, preference, task), acknowledge you'll remember it
- You have access to the user's full conversation history below
{memory_section}
{workspace_section}

FULL CONVERSATION HISTORY (you remember all of this):
{history_text}

User just said: {user_speech}

Your helpful, natural response (remember: SHORT for phone):"""

        response = model.generate_content(prompt)
        ai_response = response.text.strip()

        # Keep short for phone
        if len(ai_response) > 400:
            ai_response = ai_response[:400] + "..."

        return ai_response

    except Exception as e:
        print(f"[ERROR] Gemini failed: {e}")
        return f"I heard you, but I'm having a moment connecting to my brain. Can you try again?"


@app.route('/voice/status', methods=['POST'])
def voice_status():
    """Call status webhook"""
    call_sid = request.form.get('CallSid', '')
    status = request.form.get('CallStatus', '')
    print(f"[STATUS] Call {call_sid[:10]}... is {status}")
    return '', 200


@app.route('/callme', methods=['POST', 'GET'])
def callme():
    """Outbound call - AI calls you"""
    to_number = request.args.get('to') or request.form.get('to') or os.environ.get('DEFAULT_CALL_NUMBER', '')
    
    try:
        client = TwilioClient(TWILIO_SID, TWILIO_AUTH)
        call = client.calls.create(
            url=f'{VOICE_URL}/voice/incoming',
            to=to_number,
            from_=TWILIO_NUMBER,
            status_callback=f'{VOICE_URL}/voice/status',
            status_callback_method='POST'
        )
        return jsonify({'status': 'calling', 'to': to_number, 'call_sid': call.sid}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"AutoMinds Voice Service v2.0 starting on port {port}...")
    
    # Initialize persistent memory
    init_db()
    restore_memory_from_drive()
    
    # Start background backup thread
    backup_thread = threading.Thread(target=periodic_backup, daemon=True)
    backup_thread.start()
    print("[MEMORY] Background backup thread started (every 5 min)")
    
    app.run(host='0.0.0.0', port=port, debug=False)
else:
    # When running under gunicorn
    init_db()
    restore_memory_from_drive()
    backup_thread = threading.Thread(target=periodic_backup, daemon=True)
    backup_thread.start()
    print("[MEMORY] Persistent memory system initialized under gunicorn")
