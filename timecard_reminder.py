from datetime import datetime
from utils import notify, log_event

def main():
    log_event("Starting Timecard Reminder...")

    subject = "ACTION REQUIRED: Enter/Sign your Booz Allen Timecard"
    now = datetime.now().strftime("%A, %B %d, %Y")
    body = (
        f"This is your daily reminder for {now}.\n\n"
        "Enter and sign your Booz Allen timecard.\n\n"
        "Log in to the timekeeping system and make sure today's hours are "
        "entered and your timecard is signed before the deadline.\n\n"
        "- Automated reminder from OpenClaw"
    )

    try:
        notify(subject, body)
        log_event("Timecard Reminder finished: email sent.")
    except Exception as e:
        log_event(f"Timecard Reminder failed: {e}")

if __name__ == "__main__":
    main()
