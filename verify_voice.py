#!/usr/bin/env python3
import sys
import subprocess
import tempfile
import os
sys.path.insert(0, '/root/video_maker')

from voice_generator import generate_narration, FORMAT_VOICES, FORMAT_LENGTH_SCALE

def test_voice_mapping():
    """Test that voice mapping returns correct values"""
    print("Testing voice mapping...")

    # Test EXPLAINER
    voice, length_scale = FORMAT_VOICES["EXPLAINER"], FORMAT_LENGTH_SCALE["EXPLAINER"]
    print(f"EXPLAINER: voice={voice}, length_scale={length_scale}")
    assert voice == "en_US-ryan-medium", f"Expected en_US-ryan-medium, got {voice}"
    assert abs(length_scale - (1.0/1.25)) < 0.001, f"Expected ~0.8, got {length_scale}"

    # Test DEBATE
    voice, length_scale = FORMAT_VOICES["DEBATE"], FORMAT_LENGTH_SCALE["DEBATE"]
    print(f"DEBATE: voice={voice}, length_scale={length_scale}")
    assert voice == "en_US-amy-medium", f"Expected en_US-amy-medium, got {voice}"
    assert abs(length_scale - (1.0/1.25)) < 0.001, f"Expected ~0.8, got {length_scale}"

    # Test URGENT_BREAK
    voice, length_scale = FORMAT_VOICES["URGENT_BREAK"], FORMAT_LENGTH_SCALE["URGENT_BREAK"]
    print(f"URGENT_BREAK: voice={voice}, length_scale={length_scale}")
    assert voice == "en_US-hfc_male-medium", f"Expected en_US-hfc_male-medium, got {voice}"
    assert abs(length_scale - (1.0/1.25)) < 0.001, f"Expected ~0.8, got {length_scale}"

    print("✓ Voice mapping tests passed")

def test_audio_generation():
    """Test actual audio generation with Piper and FFmpeg"""
    print("\nTesting audio generation...")

    test_text = "Hello world. This is a test."

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "test_narration.mp3")

        try:
            # Test with EXPLAINER format
            print("Generating narration with EXPLAINER format...")
            result_path = generate_narration(test_text, output_path=output_path, format="EXPLAINER")
            print(f"Generated: {result_path}")

            # Verify file exists and is not empty
            assert os.path.exists(result_path), "MP3 file was not created"
            file_size = os.path.getsize(result_path)
            assert file_size > 0, f"MP3 file is empty (size: {file_size})"
            print(f"✓ MP3 file created successfully (size: {file_size} bytes)")

            # Verify it's a valid MP3 by checking duration with ffprobe
            result = subprocess.run([
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                result_path
            ], capture_output=True, text=True, timeout=10)

            assert result.returncode == 0, f"ffprobe failed: {result.stderr}"
            duration_str = result.stdout.strip()
            assert duration_str, "ffprobe returned empty duration"

            try:
                duration = float(duration_str)
                assert duration > 0, f"Duration is not positive: {duration}"
                print(f"✓ MP3 validation passed (duration: {duration:.2f}s)")
            except ValueError:
                raise AssertionError(f"ffprobe returned invalid duration: '{duration_str}'")

        except Exception as e:
            print(f"✗ Audio generation test failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    return True

if __name__ == "__main__":
    print("Running voice generator verification tests...\n")

    try:
        test_voice_mapping()
        success = test_audio_generation()

        if success:
            print("\n✓ All tests passed!")
        else:
            print("\n✗ Some tests failed!")
            sys.exit(1)

    except Exception as e:
        print(f"\n✗ Test suite failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)