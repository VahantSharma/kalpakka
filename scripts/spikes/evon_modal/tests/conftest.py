import sys
from pathlib import Path

# The helpers sit next to the spike scripts, which are run as plain files (`uv run --script`).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
