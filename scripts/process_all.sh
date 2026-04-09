#!/bin/bash
# Process all unprocessed episodes sequentially
# Run with: nohup bash scripts/process_all.sh > logs/process_all.log 2>&1 &

set -e
export PATH=/opt/homebrew/bin:$PATH
cd ~/Projects/tiktok-pipeline
source .venv/bin/activate

EPISODES_DIR="input/episodes"
LOG="logs/process_all.log"
mkdir -p logs

echo "========================================" 
echo "Processing all episodes: $(date)"
echo "========================================"

for episode in "$EPISODES_DIR"/*.mkv; do
    name=$(basename "$episode")
    
    # Skip Botswana (already processed)
    if [[ "$name" == *"Botswana"* ]]; then
        echo "SKIP: $name (already processed)"
        continue
    fi
    
    echo ""
    echo "========================================"
    echo "PROCESSING: $name"
    echo "Started: $(date)"
    echo "========================================"
    
    python3 scripts/pipeline_growth.py process "$episode" || {
        echo "FAILED: $name"
        continue
    }
    
    echo "DONE: $name at $(date)"
done

echo ""
echo "========================================"
echo "ALL EPISODES COMPLETE: $(date)"
echo "========================================"

# Show final stats
python3 scripts/daily_report.py --stdout-only
