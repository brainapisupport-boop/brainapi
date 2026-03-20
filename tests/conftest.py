import sys
from pathlib import Path


# Ensure the repository root is importable on CI runners where pytest does not
# automatically place the checkout directory on sys.path during collection.
ROOT = Path(__file__).resolve().parents[1]
root_str = str(ROOT)
if root_str not in sys.path:
    sys.path.insert(0, root_str)
