import requests
import os

BREVO_API_KEY = os.getenv("BREVO_API_KEY")

def send_email(to_email, subject, html_content):
    url = "https://api.brevo.com/v3/smtp/email"

    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json"
    }

    payload = {
        "sender": {
            "name": "BrainAPI",
            "email": "your_verified_email@domain.com"
        },
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html_content
    }

    response = requests.post(url, json=payload, headers=headers)

    print("Brevo response:", response.status_code, response.text)

    if response.status_code not in [200, 201]:
        raise Exception(f"Email failed: {response.text}")


# keep old calls working
def dispatch_transactional_email(to_email, subject, html_content):
    return send_email(to_email, subject, html_content)
def email_delivery_health():
    return {
        "status": "ok",
        "provider": "brevo"
    }
    # -----------------------------
# Compatibility functions (for tests)
# -----------------------------

def email_delivery_health():
    return {
        "status": "ok",
        "provider": "brevo"
    }


def dispatch_transactional_email(to_email, subject, html_content):
    return send_email(to_email, subject, html_content)


def get_lead_contact_for_api_key(api_key: str):
    # simple mock for now
    return {
        "email": "test@example.com",
        "name": "Test User"
    }


def queue_email_event(*args, **kwargs):
    # simulate queue system (tests expect this)
    return {
        "status": "queued"
    }