"""The page's own logic (static/app.js and static/series.js), run in Node against a small stand-in for the browser.

The scenarios are in page_tests.js. They are skipped where Node is not installed.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("page_tests.js")
NODE = shutil.which("node")
NAMES = subprocess.run([NODE, str(SCRIPT), "--list"], capture_output=True, text=True, check=True).stdout.split() if NODE else ["needs Node"]


@pytest.mark.skipif(NODE is None, reason="Node is not installed")
@pytest.mark.parametrize("name", NAMES)
def test_page(name):
    done = subprocess.run([NODE, str(SCRIPT), name], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
