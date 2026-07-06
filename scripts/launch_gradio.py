#!/usr/bin/env python
"""Launch the Gradio chat + analytics UI.

Usage: python scripts/launch_gradio.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.gradio.app import main

if __name__ == "__main__":
    main()
