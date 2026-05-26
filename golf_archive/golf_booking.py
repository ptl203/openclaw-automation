import sys
import subprocess
from datetime import datetime, timedelta
import json
import os
from utils import notify, log_event

def main():
    log_event("Starting Golf Booking check...")
    
    # Calculate upcoming Saturday
    today = datetime.now()
    days_ahead = 5 - today.weekday() # Saturday is 5
    if days_ahead <= 0: # Target next week if today is Saturday or Sunday
        days_ahead += 7
    target_date_obj = today + timedelta(days=days_ahead)
    target_date = target_date_obj.strftime("%m-%d-%Y")
    
    script_path = os.path.join(os.path.dirname(__file__), "golf_scripts", "book_tee_time.py")
    
    cmd = [
        sys.executable, script_path,
        "--date", target_date,
        "--course", "torrey-north",
        "--players", "2",
        "--twilight",
        "--poll",
        "--fallback"
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        output = result.stdout.strip()
        last_line = output.split('\n')[-1] if output else "{}"
        
        data = None
        try:
            data = json.loads(last_line)
            if data:
                log_event(f"Golf Booking finished: {'Success' if data.get('success') else 'No times/Failed'}")
                
                if data.get("success"):
                    msg = f"Tee time successfully booked!\n\nCourse: {data.get('course')}\nDate: {data.get('date')}\nTime: {data.get('time')}\nPlayers: {data.get('players')}\nFee: {data.get('fee')}\nConfirmation ID: {data.get('confirmation_id')}"
                    notify("Golf Booking: Success", msg)
                else:
                    msg = f"Tee time booking failed.\n\nReason: {data.get('error', 'Unknown error')}\n\nFull Script Output:\n{output}"
                    notify("Golf Booking: Failed", msg)
            else:
                log_event("Golf Booking finished: No data returned.")
        except json.JSONDecodeError:
             log_event("Golf Booking Error: Could not parse script output.")
             notify("Golf Booking Error", f"Could not parse script output.\n\nStdout:\n{output}\n\nStderr:\n{result.stderr}")
             
    except subprocess.TimeoutExpired:
        log_event("Golf Booking Timeout")
        notify("Golf Booking Timeout", "The booking script timed out after 5 minutes of polling.")
    except Exception as e:
        log_event(f"Golf Booking Error: Execution failed: {e}")
        notify("Golf Booking Error", f"Execution failed: {e}")

if __name__ == "__main__":
    main()
