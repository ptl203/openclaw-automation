import os
from utils import log_event

def main():
    log_event("Starting Log Maintenance (Weekly Cleanup)...")
    base_dir = os.path.dirname(os.path.abspath(__file__))
    log_path = os.path.join(base_dir, "automation.log")
    
    if os.path.exists(log_path):
        try:
            # Delete the file
            os.remove(log_path)
            log_event("Old log file deleted. Starting fresh.")
        except Exception as e:
            log_event(f"Error during log maintenance: {e}")
    else:
        log_event("No log file found to maintain.")

if __name__ == "__main__":
    main()
