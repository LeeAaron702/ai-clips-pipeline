#!/bin/bash
# Converts any product image to 9:16 portrait (1080x1920) with padding
# Usage: ./prep_image.sh input.jpg output.jpg

INPUT="$1"
OUTPUT="$2"

if [ -z "$INPUT" ] || [ -z "$OUTPUT" ]; then
    echo "Usage: $0 input.jpg output.jpg"
    exit 1
fi

# Center the product image on a white background, fitted to 9:16
ffmpeg -y -i "$INPUT" \
    -vf "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:white" \
    -q:v 2 "$OUTPUT" 2>/dev/null

echo "Converted to 9:16: $OUTPUT"
