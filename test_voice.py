#!/usr/bin/env python3
import sys
sys.path.insert(0, '/root/video_maker')

from voice_generator import generate_narration
import tempfile
import os

# Test text
test_text = "This is a test of the voice generation system."

# Create a temporary directory for output
with tempfile.TemporaryDirectory() as tmpdir:
    output_path = os.path.join(tmpdir, "test_narration.mp3")

    try:
        # Test with EXPLAINER format
        print("Testing EXPLAINER format...")
        result_path = generate_narration(test_text, output_path=output_path, format="EXPLAINER")
        print(f"✓ Generated: {result_path}")

        # Check if file exists and has content
        if os.path.exists(result_path) and os.path.getsize(result_path) > 0:
            print("✓ MP3 file created and not empty")
        else:
            print("✗ MP3 file missing or empty")

    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()