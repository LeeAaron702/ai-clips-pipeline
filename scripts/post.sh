#!/bin/bash
# TikTok Auto-Post via Playwright tiktok-uploader
# Usage: ./post.sh --video output.mp4 --caption "caption text" --cookies cookies.json [--product-id ID]

set -e

VIDEO=""
CAPTION=""
COOKIES="$HOME/Projects/tiktok-pipeline/cookies.json"
PRODUCT_ID=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --video) VIDEO="$2"; shift 2 ;;
        --caption) CAPTION="$2"; shift 2 ;;
        --cookies) COOKIES="$2"; shift 2 ;;
        --product-id) PRODUCT_ID="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [ -z "$VIDEO" ] || [ -z "$CAPTION" ]; then
    echo "Usage: $0 --video output.mp4 --caption 'caption text' [--product-id ID]"
    exit 1
fi

# Random delay 30-120 seconds (anti-detection)
DELAY=$((RANDOM % 91 + 30))
echo "Waiting ${DELAY}s before posting (anti-detection)..."
sleep $DELAY

echo "Posting to TikTok..."
echo "  Video: $VIDEO"
echo "  Caption: ${CAPTION:0:80}..."

# Build command
CMD="python3 -m tiktok_uploader -v \"$VIDEO\" -d \"$CAPTION\" -c \"$COOKIES\""
if [ -n "$PRODUCT_ID" ]; then
    CMD="$CMD --product-id \"$PRODUCT_ID\""
fi

# Execute with retry
if eval $CMD 2>&1; then
    echo "SUCCESS: Video posted to TikTok"
    exit 0
else
    echo "RETRY: First attempt failed, waiting 60s..."
    sleep 60
    if eval $CMD 2>&1; then
        echo "SUCCESS: Video posted to TikTok (retry)"
        exit 0
    else
        echo "FAILED: Upload failed after retry"
        exit 1
    fi
fi
