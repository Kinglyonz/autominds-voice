#!/usr/bin/env python3
"""Configure Twilio to use voice service"""

import os
from twilio.rest import Client

# Credentials (from environment variables)
account_sid = os.environ.get('TWILIO_ACCOUNT_SID', '')
auth_token = os.environ.get('TWILIO_AUTH_TOKEN', '')
phone_number = os.environ.get('TWILIO_PHONE_NUMBER', '')

# Voice service URL
voice_url = "https://autominds-voice-production.up.railway.app/voice/incoming"
status_callback = "https://autominds-voice-production.up.railway.app/voice/status"

client = Client(account_sid, auth_token)

print(f"Configuring {phone_number}...")
print(f"Voice URL: {voice_url}")

# Get our number
numbers = client.incoming_phone_numbers.list()
our_number = None
for num in numbers:
    if num.phone_number == phone_number:
        our_number = num
        break

if our_number:
    # Configure for incoming calls
    client.incoming_phone_numbers(our_number.sid).update(
        voice_url=voice_url,
        voice_method='POST',
        status_callback=status_callback,
        status_callback_method='POST'
    )

    print("\nSUCCESS!")
    print(f"\nYour AI is ready at: {phone_number}")
    print("\nYou can now:")
    print("  1. CALL IT: Dial +1-855-529-0581 anytime")
    print("  2. HAVE IT CALL YOU: Use /callme in Telegram")
    print("\nTry calling NOW and have a conversation!")

else:
    print("Could not find number")
