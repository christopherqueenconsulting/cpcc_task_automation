#!/usr/bin/env python3
"""Manual test script for OpenAI debug mode.

This script demonstrates the debug mode functionality by making a simple
OpenAI API call and showing the debug output.

Usage:
    # Enable debug mode
    export CQC_OPENAI_DEBUG=1
    export CQC_OPENAI_DEBUG_SAVE_DIR=/tmp/openai_debug_test
    export OPENAI_API_KEY=your-key-here
    
    # Run script
    python scripts/test_openai_debug.py
"""

import asyncio
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pydantic import BaseModel, Field
from cqc_cpcc.utilities.AI.openai_client import get_structured_completion
from cqc_cpcc.utilities.AI.openai_exceptions import (
    OpenAISchemaValidationError,
    OpenAITransportError
)
from cqc_cpcc.utilities.env_constants import CQC_OPENAI_DEBUG, CQC_OPENAI_DEBUG_SAVE_DIR


class SimpleTest(BaseModel):
    """Simple test model."""
    message: str = Field(description="A simple message")
    number: int = Field(description="A number between 1 and 10")


async def main():
    """Run debug mode test."""
    print("=" * 70)
    print("OpenAI Debug Mode Manual Test")
    print("=" * 70)
    
    # Check if debug mode is enabled
    print(f"\nDebug Mode: {'ENABLED' if CQC_OPENAI_DEBUG else 'DISABLED'}")
    print(f"Save Directory: {CQC_OPENAI_DEBUG_SAVE_DIR or 'Not set'}")
    
    if not CQC_OPENAI_DEBUG:
        print("\n⚠️  Debug mode is OFF. Enable with: export CQC_OPENAI_DEBUG=1")
    
    # Check for API key
    from cqc_cpcc.utilities.env_constants import OPENAI_API_KEY
    if not OPENAI_API_KEY:
        print("\n❌ OPENAI_API_KEY not set. Cannot run test.")
        print("Set with: export OPENAI_API_KEY=your-key")
        return
    
    print("\n" + "=" * 70)
    print("Making OpenAI API call...")
    print("=" * 70)
    
    try:
        result = await get_structured_completion(
            prompt="Generate a simple test message with a random number between 1 and 10.",
            model_name="gpt-5-mini",
            schema_model=SimpleTest,
        )
        
        print(f"\n✅ Success!")
        print(f"Message: {result.message}")
        print(f"Number: {result.number}")
        
    except OpenAISchemaValidationError as e:
        print(f"\n❌ Schema Validation Error:")
        print(f"   Schema: {e.schema_name}")
        print(f"   Correlation ID: {e.correlation_id}")
        print(f"   Decision Notes: {e.decision_notes}")
        if e.validation_errors:
            print(f"   Validation Errors: {len(e.validation_errors)}")
        
    except OpenAITransportError as e:
        print(f"\n❌ Transport Error:")
        print(f"   Status Code: {e.status_code}")
        print(f"   Correlation ID: {e.correlation_id}")
        print(f"   Message: {e.message}")
    
    except Exception as e:
        print(f"\n❌ Unexpected Error: {e}")
    
    # Show debug file location
    if CQC_OPENAI_DEBUG_SAVE_DIR:
        save_dir = Path(CQC_OPENAI_DEBUG_SAVE_DIR)
        if save_dir.exists():
            files = list(save_dir.glob("*.json"))
            print(f"\n📁 Debug files saved to: {save_dir}")
            print(f"   Found {len(files)} JSON files")
            
            # Show most recent files
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            print("\n   Most recent files:")
            for f in files[:6]:  # Show up to 6 (2 requests worth)
                print(f"   - {f.name}")
        else:
            print(f"\n⚠️  Debug save directory doesn't exist yet: {save_dir}")
    
    print("\n" + "=" * 70)
    print("Check logs at:")
    print("  - logs/cpcc_YYYY_MM_DD.log (main log)")
    print("  - logs/openai_debug_YYYY_MM_DD.log (debug log)")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
