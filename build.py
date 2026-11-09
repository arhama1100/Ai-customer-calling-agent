"""Prepare the existing static frontend for Vercel's public asset CDN."""

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
shutil.copytree(ROOT / "static", ROOT / "public" / "static", dirs_exist_ok=True)
