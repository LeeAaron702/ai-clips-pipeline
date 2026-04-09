#!/bin/bash
# Generate voiceover using Inworld TTS (Nate) via fal.ai, sped up 1.1x
# Usage: ./generate_voiceover.sh "narration text here" output.wav

TEXT="$1"
OUTPUT="$2"

if [ -z "$TEXT" ] || [ -z "$OUTPUT" ]; then
    echo "Usage: $0 'narration text' output.wav"
    exit 1
fi

WORKDIR=$(mktemp -d)
trap "rm -rf $WORKDIR" EXIT

cd ~/Projects/tiktok-pipeline/mcp-video-agent

# Generate TTS
RAW_URL=$(uv run python -c "
import fal_client, os
from dotenv import load_dotenv
load_dotenv()
os.environ['FAL_KEY'] = os.getenv('FALAI_API_KEY', '')

result = fal_client.subscribe(
    'fal-ai/inworld-tts',
    arguments={
        'text': '''$TEXT''',
        'voice': 'Nate (en)'
    }
)
audio = result.get('audio', {})
url = audio.get('url', '') if isinstance(audio, dict) else audio
print(url)
" 2>/dev/null)

if [ -z "$RAW_URL" ]; then
    echo "ERROR: TTS generation failed"
    exit 1
fi

# Download raw audio
curl -sL "$RAW_URL" -o "$WORKDIR/raw.wav"

# Speed up 1.1x
# Resolve to absolute path
OUTPUT_ABS="$(cd "$(dirname "$OUTPUT")" 2>/dev/null && pwd)/$(basename "$OUTPUT")"
ffmpeg -y -i "$WORKDIR/raw.wav" -filter:a "atempo=1.1" "$OUTPUT_ABS" 2>/dev/null

echo "Voiceover saved to $OUTPUT"
