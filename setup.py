import os
import sys

DAEMON_DIR = "/Library/LaunchDaemons"

PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>UserName</key>
    <string>{run_as_user}</string>
    <key>GroupName</key>
    <string>staff</string>
    <key>WorkingDirectory</key>
    <string>{base_dir}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>{script_path}</string>
        {extra_args}
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>{hour}</integer>
        <key>Minute</key>
        <integer>{minute}</integer>
        {weekday_key}
    </dict>
    <key>StandardOutPath</key>
    <string>{log_path}.out</string>
    <key>StandardErrorPath</key>
    <string>{log_path}.err</string>
</dict>
</plist>
"""

def generate_plist(name, script_name, hour, minute, weekday=None, extra_args=""):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    run_as_user = os.environ.get("SUDO_USER") or os.environ.get("USER") or "admin"
    python_path = os.path.join(base_dir, ".venv", "bin", "python")
    if not os.path.exists(python_path):
         python_path = sys.executable if 'sys' in globals() else "/usr/bin/python3"

    script_path = os.path.join(base_dir, script_name)
    log_path = os.path.join(base_dir, f"{name}.log")

    weekday_key = f"<key>Weekday</key>\n        <integer>{weekday}</integer>" if weekday is not None else ""
    extra = f"<string>{extra_args}</string>" if extra_args else ""

    content = PLIST_TEMPLATE.format(
        label=name,
        run_as_user=run_as_user,
        base_dir=base_dir,
        python_path=python_path,
        script_path=script_path,
        extra_args=extra,
        hour=hour,
        minute=minute,
        weekday_key=weekday_key,
        log_path=log_path
    )

    # Remove empty lines that might break plist
    content = "\n".join([line for line in content.split("\n") if line.strip() != ""])

    plist_path = os.path.join(DAEMON_DIR, f"{name}.plist")
    with open(plist_path, "w") as f:
        f.write(content)
    # LaunchDaemons must be owned by root:wheel and not group/other-writable,
    # or launchd will refuse to load them.
    os.system(f"chown root:wheel {plist_path}")
    os.system(f"chmod 644 {plist_path}")
    print(f"Created {plist_path} (runs as {run_as_user})")

    # Idempotent (re)load: bootout any prior instance, then bootstrap fresh.
    os.system(f"launchctl bootout system/{name} 2>/dev/null")
    os.system(f"launchctl bootstrap system {plist_path}")

def main():
    if os.geteuid() != 0:
        print("This installs system LaunchDaemons under /Library/LaunchDaemons,")
        print("which requires root. Please re-run with:")
        print(f"    sudo {sys.executable} {os.path.abspath(__file__)}")
        sys.exit(1)

    print("Generating launchd daemon plists (run independent of GUI login)...")
    # Lithrop Ledger: 7:00 AM daily
    generate_plist("com.openclaw.lithrop_ledger", "lithrop_ledger.py", 7, 0)
    # Job Scraper: Wed (3) 4:00 PM (16)
    generate_plist("com.openclaw.job_scraper", "job_scraper.py", 16, 0, weekday=3)
    # Smart Irrigation: 5:00 AM daily (Script skips Mon/Thu)
    generate_plist("com.openclaw.smart_irrigation", "smart_irrigation.py", 5, 0)
    # Surf Compare AM: 5:00 AM daily — ranks all beaches in beaches.json
    generate_plist("com.openclaw.surf_compare_am", "surf_compare.py", 5, 0, extra_args="--am")
    # Surf Compare PM: 3:00 PM (15) daily
    generate_plist("com.openclaw.surf_compare_pm", "surf_compare.py", 15, 0, extra_args="--pm")
    # Timecard Reminder: 6:00 PM (18) daily
    generate_plist("com.openclaw.timecard_reminder", "timecard_reminder.py", 18, 0)

    print("\n--- PMSET Wake Instructions ---")
    print("LaunchDaemons still won't fire while the Mac is fully asleep (only while")
    print("logged out at the login window, which they now handle fine). To ensure")
    print("time-sensitive jobs run exactly on time even if the Mac is asleep:")
    print("Run the following command in your terminal to schedule wake events 1-2 minutes before jobs:")
    print("sudo pmset repeat wake MTWRFSU 04:58:00")
    print("Note: pmset repeat only supports a single repeating schedule. If you want multiple wake events,")
    print("you must use a tool like 'Power Manager' or just leave the Mac on during target hours.")

if __name__ == "__main__":
    main()
