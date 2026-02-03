#!/usr/bin/env python3
import sys
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Add src to path so we can import the package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from agentic.cli import app

if __name__ == "__main__":
    app()
