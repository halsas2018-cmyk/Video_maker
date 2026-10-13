"""
voice_generator.py
Step 4 of the Shorts pipeline: generate narration audio from script text.

Uses local Piper TTS (free, no API key, runs locally).
Voice mapping:
- EXPLAINER: en_US-ryan-medium
- DEBATE: en_US-amy-medium
- URGENT_BREAK: en_US-hfc_male-medium
- Default: en_US-ryan-medium

Usage:
    python voice_generator.py --script "Hello world" --output narration.mp3
    python voice_generator.py --file script.txt --output narration.mp3
"""

import argparse
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Piper TTS Configuration
# ---------------------------------------------------------------------------

# Voice mapping for different formats (preserving existing format names)
FORMAT_VOICES = {
    "URGENT_BREAK": "en_US-hfc_male-medium",
    "DEBATE":       "en_US-amy-medium",
    "EXPLAINER":    "en_US-ryan-medium",
}
DEFAULT_VOICE = "en_US-ryan-medium"

# Length scale mapping for speech rate (Piper uses length_scale where <1.0 is faster)
# Edge TTS used "+20%" rate which means 1.2x speed
# Piper length_scale: 1.0 = normal speed, <1.0 = faster, >1.0 = slower
# Approximate conversion: length_scale = 1.0 / (1.0 + rate_percent/100)
FORMAT_LENGTH_SCALE = {
    "URGENT_BREAK": 1.0 / 1.25,  # +25% rate -> 0.8
    "DEBATE":       1.0 / 1.25,  # +25% rate -> 0.8
    "EXPLAINER":    1.0 / 1.25,  # +25% rate -> 0.8
}
DEFAULT_LENGTH_SCALE = 1.0 / 1.20  # +20% rate -> 0.8333

# Model directory relative to project root
MODELS_DIR = Path(__file__).parent / "models" / "piper"

# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def _resolve_voice_and_length_scale(format_tag: str | None) -> tuple[str, float]:
    """Return (voice_model, length_scale) for a given format tag, falling back to defaults."""
    if format_tag and format_tag in FORMAT_VOICES:
        voice = FORMAT_VOICES[format_tag]
        length_scale = FORMAT_LENGTH_SCALE.get(format_tag, DEFAULT_LENGTH_SCALE)
        return voice, length_scale
    if format_tag:
        print(f"  [voice] unknown format '{format_tag}', falling back to defaults")
    return DEFAULT_VOICE, DEFAULT_LENGTH_SCALE


def _get_model_paths(voice_name: str) -> tuple[Path, Path]:
    """Get the paths to the .onnx model and .json config files for a given voice."""
    model_path = MODELS_DIR / f"{voice_name}.onnx"
    config_path = MODELS_DIR / f"{voice_name}.onnx.json"

    if not model_path.exists():
        raise FileNotFoundError(f"Piper model not found: {model_path}")
    if not config_path.exists():
        raise FileNotFoundError(f"Piper model config not found: {config_path}")

    return model_path, config_path


def _generate_wav_with_piper(text: str, wav_output_path: Path, voice_name: str, length_scale: float) -> None:
    """Generate WAV audio using Piper TTS."""
    model_path, config_path = _get_model_paths(voice_name)

    # Piper command: echo "text" | piper --model model.onnx --config model.onnx.json --output_file output.wav --length-scale X.X
    cmd = [
        "piper",
        "--model", str(model_path),
        "--config", str(config_path),
        "--output-file", str(wav_output_path),
        "--length-scale", str(length_scale)
    ]

    # Run Piper with the text as stdin input
    result = subprocess.run(
        cmd,
        input=text,
        text=True,
        capture_output=True,
        timeout=120  # 2 minute timeout
    )

    if result.returncode != 0:
        raise RuntimeError(f"Piper TTS failed: {result.stderr}")

    # Verify the WAV file was created and is not empty
    if not wav_output_path.exists():
        raise RuntimeError(f"Piper did not generate output file: {wav_output_path}")

    if wav_output_path.stat().st_size == 0:
        raise RuntimeError(f"Piper generated empty output file: {wav_output_path}")


def _convert_wav_to_mp3(wav_path: Path, mp3_path: Path) -> None:
    """Convert WAV file to MP3 using FFmpeg with libmp3lame."""
    cmd = [
        "ffmpeg",
        "-y",  # Overwrite output file if it exists
        "-i", str(wav_path),
        "-codec:a", "libmp3lame",
        "-qscale:a", "2",  # Good quality VBR
        str(mp3_path)
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        timeout=60  # 1 minute timeout for conversion
    )

    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg conversion failed: {result.stderr}")


def _validate_mp3(mp3_path: Path) -> float:
    """Validate that the MP3 file exists, is not empty, and has positive duration.
    Returns the duration in seconds."""
    if not mp3_path.exists():
        raise RuntimeError(f"MP3 file not found: {mp3_path}")

    if mp3_path.stat().st_size == 0:
        raise RuntimeError(f"MP3 file is empty: {mp3_path}")

    # Use ffprobe to get duration
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(mp3_path)
        ],
        capture_output=True,
        text=True,
        timeout=10
    )

    if result.returncode != 0:
        raise RuntimeError(f"FFprobe failed to read MP3 duration: {result.stderr}")

    duration_str = result.stdout.strip()
    if not duration_str:
        raise RuntimeError(f"FFprobe returned empty duration for: {mp3_path}")

    try:
        duration = float(duration_str)
    except ValueError:
        raise RuntimeError(f"FFprobe returned invalid duration '{duration_str}' for: {mp3_path}")

    if duration <= 0:
        raise RuntimeError(f"MP3 has non-positive duration ({duration}s): {mp3_path}")

    return duration


# ---------------------------------------------------------------------------
# Main Function (preserving existing signature)
# ---------------------------------------------------------------------------

def generate_narration(text: str, output_path: str = None,
                       project_dir: Path = None, format: str = None) -> Path:
    """
    Generate a narration MP3 from text using local Piper TTS.

    Args:
        text: The script text to narrate.
        output_path: Direct output path (overrides project_dir).
        project_dir: If set, saves narration.mp3 inside this directory.
        format: Optional story format (URGENT_BREAK, DEBATE, EXPLAINER) that drives voice and rate.

    Returns:
        Path to the generated MP3 file.
    """
    if output_path is None and project_dir is not None:
        project_dir = Path(project_dir)
        project_dir.mkdir(parents=True, exist_ok=True)
        output_path = str(project_dir / "narration.mp3")
    elif output_path is None:
        output_path = "narration.mp3"

    # Clean text for better TTS delivery
    clean_text = text.strip()
    if not clean_text:
        raise ValueError("Text is empty or whitespace only")

    # Ensure output directory exists
    output_path_obj = Path(output_path)
    output_path_obj.parent.mkdir(parents=True, exist_ok=True)

    # Resolve voice and length scale based on optional format tag
    voice_name, length_scale = _resolve_voice_and_length_scale(format)
    print(f"  Generating narration ({len(clean_text.split())} words, voice={voice_name}, length_scale={length_scale:.3f}, format={format or 'default'})...")

    # Create temporary WAV file in the same directory as the output
    wav_path = output_path_obj.with_suffix('.wav')

    try:
        # Generate WAV with Piper
        _generate_wav_with_piper(clean_text, wav_path, voice_name, length_scale)

        # Convert WAV to MP3
        _convert_wav_to_mp3(wav_path, output_path_obj)

        # Validate the resulting MP3
        duration = _validate_mp3(output_path_obj)

        # Sanity check: provide feedback on duration
        expected_min = len(clean_text.split()) / 4.5  # ~4.5 wps at +20% rate (approximate)
        if duration < expected_min * 0.6:
            print(f"  ⚠ WARNING: Narration duration ({duration:.1f}s) seems too short for {len(clean_text.split())} words (expected ~{expected_min:.0f}s)")

        print(f"  Narration saved: {output_path} ({duration:.1f}s)")
        return output_path_obj

    finally:
        # Clean up temporary WAV file
        if wav_path.exists():
            try:
                wav_path.unlink()
            except OSError:
                pass  # Ignore cleanup errors


# ---------------------------------------------------------------------------
# Duration Helper (keeping existing function for compatibility)
# ---------------------------------------------------------------------------

def _get_mp3_duration(filepath: str) -> float:
    """Estimate duration from file size / bitrate or return 0."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
             filepath],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return 0.0
        return float(result.stdout.strip()) if result.stdout.strip() else 0.0
    except (subprocess.SubprocessError, ValueError, FileNotFoundError):
        return 0.0


# ---------------------------------------------------------------------------
# Command Line Interface
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate narration audio")
    parser.add_argument("--script", type=str, help="Script text directly")
    parser.add_argument("--file", type=str, help="Path to script.txt file")
    parser.add_argument("--output", type=str, default="narration.mp3",
                        help="Output audio file path")
    args = parser.parse_args()

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.script:
        text = args.script
    else:
        print("Provide --script or --file")
        return

    path = generate_narration(text, output_path=args.output)
    print(f"Done: {path}")


if __name__ == "__main__":
    main()