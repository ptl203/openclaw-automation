import os
import glob
from utils import log_event

# Rotated copies of automation.log to keep (automation.log.1 = most recent)
KEEP_ROTATIONS = 2
# launchd stdout/stderr files are truncated once they pass this size
LAUNCHD_LOG_MAX_BYTES = 1_000_000


def rotate_automation_log(base_dir):
    """Rotate automation.log -> .1 -> .2 instead of deleting history outright."""
    log_path = os.path.join(base_dir, "automation.log")
    if not os.path.exists(log_path):
        return

    oldest = f"{log_path}.{KEEP_ROTATIONS}"
    if os.path.exists(oldest):
        os.remove(oldest)
    for i in range(KEEP_ROTATIONS - 1, 0, -1):
        src = f"{log_path}.{i}"
        if os.path.exists(src):
            os.rename(src, f"{log_path}.{i + 1}")
    os.rename(log_path, f"{log_path}.1")


def truncate_launchd_logs(base_dir):
    """Truncate oversized launchd stdout/stderr files (launchd appends forever)."""
    truncated = []
    for pattern in ("com.openclaw.*.log.out", "com.openclaw.*.log.err"):
        for path in glob.glob(os.path.join(base_dir, pattern)):
            try:
                if os.path.getsize(path) > LAUNCHD_LOG_MAX_BYTES:
                    with open(path, "w"):
                        pass
                    truncated.append(os.path.basename(path))
            except OSError as e:
                log_event(f"Log maintenance could not truncate {path}: {e}")
    return truncated


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        rotate_automation_log(base_dir)
        truncated = truncate_launchd_logs(base_dir)
        summary = f"rotated automation.log (keeping {KEEP_ROTATIONS} old copies)"
        if truncated:
            summary += f"; truncated {', '.join(truncated)}"
        log_event(f"Log Maintenance finished: {summary}.")
    except Exception as e:
        log_event(f"Error during log maintenance: {e}")


if __name__ == "__main__":
    main()
