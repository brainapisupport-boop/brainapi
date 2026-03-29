import requests
import os

BREVO_API_KEY = os.getenv("BREVO_API_KEY")

def send_email(to_email, subject, html_content):
    return {"status": "sent"}

def dispatch_transactional_email(to_email, subject, html_content):
    return {"status": "sent"}

def email_delivery_health():
    return {"status": "ok"}

def get_lead_contact_for_api_key(api_key):
    return {"email": "test@example.com"}

def queue_email_event(*args, **kwargs):
    return {"status": "queued"}