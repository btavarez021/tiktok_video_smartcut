import os

def enforce_mp4(filename: str) -> str:
    """
    Ensures filename is basename only, lowercase, and ends with .mp4.
    """
    base = os.path.basename(filename).lower()
    name, _ext = os.path.splitext(base)
    return f"{name}.mp4"
