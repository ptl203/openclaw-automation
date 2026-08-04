from utils import notify, log_event, wait_for_network, now_local

def main():
    log_event("Starting Timecard Reminder...")

    # Timecards are a business-day chore — skip Saturday (5) and Sunday (6).
    # Uses now_local() (America/Los_Angeles) rather than a naive datetime.now():
    # this job runs as a Claude Code routine in a UTC sandbox, and 6PM PT is
    # already the next day in UTC, so a naive check would drop Friday's
    # reminder and mislabel every other day's date.
    if now_local().weekday() >= 5:
        log_event("Skipping Timecard Reminder: weekend.")
        return

    wait_for_network()
    subject = "ACTION REQUIRED: Enter/Sign your Booz Allen Timecard"
    now = now_local().strftime("%A, %B %d, %Y")
    body = (
        f"This is your daily reminder for {now}.\n\n"
        "Enter and sign your Booz Allen timecard.\n\n"
        "Log in to the timekeeping system and make sure today's hours are "
        "entered and your timecard is signed before the deadline.\n\n"
        "- Automated reminder from OpenClaw"
    )

    try:
        if notify(subject, body):
            log_event("Timecard Reminder finished: email sent.")
        else:
            log_event("Timecard Reminder finished: email FAILED (see prior log line).")
    except Exception as e:
        log_event(f"Timecard Reminder failed: {e}")

if __name__ == "__main__":
    main()
