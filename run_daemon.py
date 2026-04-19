#!/usr/bin/env python3
"""Simple script to run the ingestion daemon locally for testing."""

import asyncio
import sys
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

from petascale.ingest.daemon import main

if __name__ == "__main__":
    print("Starting petascale ingestion daemon...")
    print("Make sure you have:")
    print("1. Created a .env file from .env.example")
    print("2. Filled in your HA_TOKEN and other configuration")
    print("3. HA is running and accessible")
    print("4. MQTT broker is running (optional for basic testing)")
    print()
    asyncio.run(main())