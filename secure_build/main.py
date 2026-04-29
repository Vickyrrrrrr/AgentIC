#!/usr/bin/env python3
import sys
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# If running as raw python, inject src into path. 
# If frozen by PyInstaller, the modules are already unpacked into sys.path!
if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from agentic.cli import app

if __name__ == "__main__":
    app()
