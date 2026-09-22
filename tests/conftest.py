import sys
from pathlib import Path

# make scripts/ importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
