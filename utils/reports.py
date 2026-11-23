"""Where dry-run / preview HTML reports are written."""

import os


def report_path(filename: str) -> str:
    """Absolute path for a report file: in REPORT_DIR if set (the VPS mounts /opt/agent/out there), else the current folder."""
    folder = os.getenv("REPORT_DIR") or "."
    os.makedirs(folder, exist_ok=True)
    return os.path.abspath(os.path.join(folder, filename))
