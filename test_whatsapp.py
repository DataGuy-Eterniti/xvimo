import os, requests
from dotenv import load_dotenv
load_dotenv(override=True)

MY_NUMBER = "2348080121362"   # <- your WhatsApp number: country code, no + or leading 0

url = f"https://graph.facebook.com/v23.0/{os.getenv('WHATSAPP_PHONE_NUMBER_ID')}/messages"
headers = {"Authorization": f"Bearer {os.getenv('WHATSAPP_TOKEN')}"}
body = {"messaging_product": "whatsapp", "to": MY_NUMBER, "type": "template",
        "template": {"name": "hello_world", "language": {"code": "en_US"}}}
r = requests.post(url, headers=headers, json=body)
print(r.status_code, r.json())