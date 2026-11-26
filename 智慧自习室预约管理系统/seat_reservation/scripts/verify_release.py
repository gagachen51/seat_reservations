"""网页交付自检：在项目根目录运行 python scripts/verify_release.py。"""
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=root, check=True)
subprocess.run(["node", "--test", *[str(p.relative_to(root)) for p in (root / "tests").glob("*.test.cjs")]], cwd=root, check=True)
