"""One-time script to get Google Search Console OAuth refresh token."""
import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/webmasters"]
CLIENT_SECRET = "/Users/slava/Downloads/client_secret_997688309278-132gqjkb45na0ln7sm39osi73il1n337.apps.googleusercontent.com.json"

flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, scopes=SCOPES)
creds = flow.run_local_server(port=8090)

print("\n=== Сохрани эти значения в .env ===\n")
print(f"GSC_CLIENT_ID={flow.client_config['client_id']}")
print(f"GSC_CLIENT_SECRET={flow.client_config['client_secret']}")
print(f"GSC_REFRESH_TOKEN={creds.refresh_token}")
print(f"\nToken expiry: {creds.expiry}")
