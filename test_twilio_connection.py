import os
from twilio.rest import Client
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

account_sid = os.getenv('TWILIO_ACCOUNT_SID')
auth_token = os.getenv('TWILIO_AUTH_TOKEN')
phone_number = os.getenv('TWILIO_PHONE_NUMBER')

print(f"Account SID: {account_sid}")
print(f"Auth Token: {auth_token[:4]}...{auth_token[-4:]} (Length: {len(auth_token)})")
print(f"Phone Number: {phone_number}")

if not account_sid or not auth_token:
    print("Error: Missing credentials.")
    exit(1)

try:
    client = Client(account_sid, auth_token)
    # Try to fetch account details to verify credentials
    account = client.api.accounts(account_sid).fetch()
    print(f"Successfully authenticated as: {account.friendly_name}")
    print(f"Account Status: {account.status}")
    print(f"Account Type: {account.type}")
    
    # Try to list incoming phone numbers to verify the phone number ownership
    print("\nVerifying phone number ownership...")
    incoming_phone_numbers = client.incoming_phone_numbers.list(phone_number=phone_number)
    if incoming_phone_numbers:
        print(f"Phone number {phone_number} found in account.")
    else:
        print(f"Warning: Phone number {phone_number} NOT found in this account.")
        
except Exception as e:
    print(f"\nAuthentication Failed: {e}")
