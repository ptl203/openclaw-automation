import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from dotenv import load_dotenv

load_dotenv(override=True)

def send_email(subject, message):
    server = os.getenv("SMTP_SERVER")
    port = os.getenv("SMTP_PORT", 587)
    user = os.getenv("EMAIL_ADDRESS")
    pwd = os.getenv("EMAIL_PASSWORD")
    to_email = os.getenv("TO_EMAIL")
    
    if not all([server, user, pwd, to_email]):
        print(f"Email configuration missing for: {subject}")
        return
        
    msg = EmailMessage()
    msg.set_content(message)
    msg['Subject'] = f"[LobsterClaw] {subject}"
    msg['From'] = user
    msg['To'] = to_email
    
    try:
        s = smtplib.SMTP(server, int(port))
        s.starttls()
        s.login(user, pwd)
        s.send_message(msg)
        s.quit()
        print(f"Sent Email: {subject}")
    except Exception as e:
        print(f"Email failed for {subject}: {e}")

def send_html_email(subject, html_body):
    server = os.getenv("SMTP_SERVER")
    port = os.getenv("SMTP_PORT", 587)
    user = os.getenv("EMAIL_ADDRESS")
    pwd = os.getenv("EMAIL_PASSWORD")
    to_email = os.getenv("TO_EMAIL")

    if not all([server, user, pwd, to_email]):
        print(f"HTML email configuration missing for: {subject}")
        return

    msg = EmailMessage()
    msg['Subject'] = f"[LobsterClaw] {subject}"
    msg['From'] = user
    msg['To'] = to_email
    msg.set_content("This email requires an HTML-capable email client.")
    msg.add_alternative(html_body, subtype='html')

    try:
        s = smtplib.SMTP(server, int(port))
        s.starttls()
        s.login(user, pwd)
        s.send_message(msg)
        s.quit()
        print(f"Sent HTML Email: {subject}")
    except Exception as e:
        print(f"HTML email failed for {subject}: {e}")

def log_event(message):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    log_path = os.path.join(base_dir, "automation.log")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a") as f:
        f.write(f"[{timestamp}] {message}\n")

def notify(subject, message):
    log_event(f"NOTIFY [{subject}]: {message[:100]}...")
    print(f"--- {subject} ---")
    print(message)
    send_email(subject, message)
