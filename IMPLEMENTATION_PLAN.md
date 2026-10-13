# Implementation Plan: Replace Edge TTS with Local Piper TTS

## Files to Modify

1. **voice_generator.py** - Replace Edge TTS implementation with Piper TTS
2. **requirements.txt** - Update dependencies for Piper TTS
3. **.github/workflows/full-pipeline.yml** - Add model download steps
4. **run_pipeline.py** - Improve error handling for narration failures

## Detailed Changes

### 1. voice_generator.py
- Replace `edge_tts` import with Piper TTS interface (subprocess call to `piper` CLI or Python API)
- Remove Edge TTS specific constants (VOICE, RATE, PITCH, VOLUME, FORMAT_PACING)
- Implement Piper-specific voice mapping:
  - EXPLAINER → en_US-ryan-medium
  - DEBATE → en_US-amy-medium  
  - URGENT_BREAK → en_US-hfc_male-medium
  - Default → en_US-ryan-medium (matching current EXPLAINER fallback)
- Generate WAV audio with Piper, then convert to MP3 using FFmpeg/libmp3lame
- Validate output MP3 exists, is non-empty, and has positive duration
- Preserve existing function signature: `generate_narration(text: str, output_path: str = None, project_dir: Path = None, format: str = None) -> Path`
- Handle text preparation and format handling similarly to existing code
- Add proper error checking and reporting

### 2. requirements.txt
- Remove `edge-tts>=7.0.0`
- Add `piper-tts>=` (appropriate version)
- Ensure other dependencies remain unchanged

### 3. .github/workflows/full-pipeline.yml
- Add steps before "Install Python dependencies" to download Piper ONNX models:
  - Create models/piper/ directory
  - Download from Hugging Face rhasspy/piper-voices:
    - en/en_US/ryan/medium/en_US-ryan-medium.onnx
    - en/en_US/ryan/medium/en_US-ryan-medium.onnx.json
    - en/en_US/amy/medium/en_US-amy-medium.onnx
    - en/en_US/amy/medium/en_US-amy-medium.onnx.json
    - en/en_US/hfc_male/medium/en_US-hfc_male-medium.onnx
    - en/en_US/hfc_male/medium/en_US-hfc_male-medium.onnx.json
- Use `curl -fL` or similar to fail on HTTP errors
- Ensure workflow fails if downloads are incomplete

### 4. run_pipeline.py
- In the narration generation section (around line 448), modify error handling:
  - Currently: narration failure is caught and pipeline continues (can cause downstream failures)
  - Proposed: narration failure should raise an exception that prevents downstream stages
  - However, preserve batch-processing behavior - only the failed project should be marked as failed
  - Other projects in the batch should continue processing

## Implementation Approach

### Piper TTS Integration
Piper can be invoked via:
1. Command-line interface: `echo "text" | piper --model model.onnx --output_file output.wav`
2. Python API: If available, use direct Python bindings

Given the requirement to use "installed package/CLI or its supported API", I'll use the CLI approach for reliability.

### Audio Pipeline
1. Generate WAV file using Piper CLI
2. Convert WAV to MP3 using FFmpeg: `ffmpeg -i input.wav -codec:a libmp3lame -qscale:a 2 output.mp3`
3. Validate MP3 with ffprobe to ensure positive duration
4. Clean up temporary WAV file on success or failure

### Error Handling Improvements
In run_pipeline.py, modify the narration generation try/catch block to:
- Re-raise narration exceptions after logging
- Ensure failed projects don't proceed to asset collection/remotion stages
- Allow other projects in batch to continue

## Testing Plan
- Test each voice mapping with sample text
- Verify MP3 conversion and duration checking
- Test error cases (missing models, etc.)
- Validate GitHub Actions workflow downloads models correctly
- Ensure existing function signature and behavior is preserved