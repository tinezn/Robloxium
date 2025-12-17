import os
from typing import Optional

def detect_custom_launcher() -> Optional[str]:
    """
    Detects the presence of Fishstrap or Bloxstrap launchers.
    Returns the path to the preferred launcher executable, or None if not found.
    Fishstrap is preferred if both are present.
    """
    # Common install locations for Bloxstrap and Fishstrap
    possible_paths = [
        # Fishstrap (preferred)
        os.path.expandvars(r"%LOCALAPPDATA%\\Fishstrap\\Fishstrap.exe"),
        os.path.expandvars(r"%ProgramFiles%\\Fishstrap\\Fishstrap.exe"),
        # Bloxstrap
        os.path.expandvars(r"%LOCALAPPDATA%\\Bloxstrap\\Bloxstrap.exe"),
        os.path.expandvars(r"%ProgramFiles%\\Bloxstrap\\Bloxstrap.exe"),
    ]
    for path in possible_paths:
        if os.path.isfile(path):
            return path
    return None
