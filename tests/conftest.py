import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts" / "release"))
sys.path.insert(0, str(_ROOT / "epic" / "scripts"))
