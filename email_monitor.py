import os
import json
import re
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from trello import TrelloClient
from dotenv import load_dotenv
import base64

load_dotenv()

# Gmail setup - assumes token.json from OAuth flow
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
creds = Credentials.from_authorized_user_file('token.json', SCOPES)
service = build('gmail', 'v1', credentials=creds)

# Trello setup
trello_client = TrelloClient(api_key=os.getenv('TRELLO_KEY'), token=os.getenv('TRELLO_TOKEN'))
trello_board_id = os.getenv('TRELLO_BOARD_ID')

# Function to get board lists
board = trello_client.get_board(trello_board_id)
lists = board.list_lists()

# Assume list names like 'Applied', 'Interview', 'Rejected'
applied_list = next(l for l in lists if l.name == 'Applied')
interview_list = next(l for l in lists if l.name == 'Interview')
rejected_list = next(l for l in lists if l.name == 'Rejected')

def monitor_emails():
    results = service.users().messages().list(userId='me', q='is:unread label:INBOX').execute()
    messages = results.get('messages', [])
    
    for msg in messages:
        msg_data = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
        payload = msg_data['payload']
        headers = payload['headers']
        subject = next(h['value'] for h in headers if h['name'] == 'Subject')
        from_email = next(h['value'] for h in headers if h['name'] == 'From')
        
        # Extract body
        if 'parts' in payload:
            body = payload['parts'][0]['body']['data']
        else:
            body = payload['body']['data']
        body = base64.urlsafe_b64decode(body).decode('utf-8')
        
        # Simple logic: look for keywords
        if 'interview' in subject.lower() or 'next steps' in body.lower():
            status = 'Interview'
            target_list = interview_list
        elif 'rejected' in subject.lower() or 'not selected' in body.lower():
            status = 'Rejected'
            target_list = rejected_list
        else:
            continue
        
        # Find matching card - assume card desc has job URL or title
        cards = applied_list.list_cards()
        for card in cards:
            if re.search(r'URL: .*' + re.escape(subject), card.desc):  # Rough match
                card.change_list(target_list.id)
                print(f"Updated card {card.name} to {status}")
                break
        
        # Mark as read
        service.users().messages().modify(userId='me', id=msg['id'], body={'removeLabelIds': ['UNREAD']}).execute()

if __name__ == '__main__':
    monitor_emails() 