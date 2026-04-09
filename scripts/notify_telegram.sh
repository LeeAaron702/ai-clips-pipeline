#!/bin/bash
# Send Telegram notification with optional video attachment
# Usage: ./notify_telegram.sh --message "text" [--video file.mp4]

source "$HOME/Projects/tiktok-pipeline/.env" 2>/dev/null

MESSAGE=""
VIDEO=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --message) MESSAGE="$2"; shift 2 ;;
        --video) VIDEO="$2"; shift 2 ;;
        *) shift ;;
    esac
done

if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
    echo "WARNING: Telegram not configured (missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID in .env)"
    echo "Message: $MESSAGE"
    exit 0
fi

if [ -n "$VIDEO" ] && [ -f "$VIDEO" ]; then
    # Send video with caption
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendVideo" \
        -F "chat_id=${TELEGRAM_CHAT_ID}" \
        -F "video=@${VIDEO}" \
        -F "caption=${MESSAGE}" \
        -F "parse_mode=HTML" > /dev/null
else
    # Send text message
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d "chat_id=${TELEGRAM_CHAT_ID}" \
        -d "text=${MESSAGE}" \
        -d "parse_mode=HTML" > /dev/null
fi

echo "Telegram notification sent"
