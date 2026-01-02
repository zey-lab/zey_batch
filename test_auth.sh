#!/bin/bash
# Load environment variables
set -a
source .env
set +a

echo "========================================================"
echo "Testing Twilio Authentication via cURL"
echo "========================================================"
echo "Account SID: $TWILIO_ACCOUNT_SID"
echo "Auth Token Length: ${#TWILIO_AUTH_TOKEN}"
echo "Target URL: https://api.twilio.com/2010-04-01/Accounts/$TWILIO_ACCOUNT_SID.json"
echo "========================================================"

# Make the request
curl -s -S -X GET "https://api.twilio.com/2010-04-01/Accounts/$TWILIO_ACCOUNT_SID.json" \
    -u "$TWILIO_ACCOUNT_SID:$TWILIO_AUTH_TOKEN" \
    | head -n 20

echo ""
echo "========================================================"
