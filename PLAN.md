---
type: plan
status: active
tags: [tiktok, pipeline, strategy, plan]
created: 2026-04-09
updated: 2026-04-09
---

# TikTok Clip Pipeline — Master Plan

## Goal

Build a fully autonomous content engine that generates, posts, and optimizes TikTok clips from long-form video content — starting with Top Gear specials — to grow @stigscloset to 1,000+ followers and unlock monetization. Zero ongoing cost. Runs 24/7 on hermes (M4 Mac Mini) without human intervention.

Once proven, package as a standalone product ([[AI Clips Pipeline]]) and sell or license to other creators.

## Current State (Apr 9, 2026)

- **Account:** @stigscloset — 342 followers, 12.7K likes, 186 videos
- **Pipeline:** Fully autonomous on hermes. Processing 11 Top Gear specials.
- **Posting:** 10x/day, [[Playwright]] browser automation, visible for debugging
- **AI:** [[Claude Code]] Opus for hooks + captions, Anthropic API for visual QA review
- **Dashboard:** Live at hermes:8888
- **Repo:** github.com/LeeAaron702/ai-clips-pipeline
- **Cost:** $0/mo (uses Max subscription OAuth token, local transcription on Metal GPU)

---

## Phase 1: Growth Engine (NOW — Apr 2026)

**Goal:** Reach 1,000 followers with Top Gear content. Prove the pipeline works autonomously.

### Steps
1. ~~Build V4 caption engine~~ — DONE (Montserrat ExtraBold, ALL CAPS, bounce animation, drop shadow, Top Gear blue highlights)
2. ~~Build face tracking + blur-fill cropping~~ — DONE (MediaPipe + [[FFmpeg]] blur-fill fallback)
3. ~~Build persistent scheduler~~ — DONE (10x/day, auto-GC after posting)
4. ~~Remove trending audio~~ — DONE (was AI slop, TikTok showed "original audio")
5. ~~Fix Claude CLI auth for AI hooks~~ — DONE (`.credentials.json` from OAuth token)
6. ~~Build monitoring dashboard~~ — DONE (hermes:8888)
7. **Process all 11 Top Gear specials** — IN PROGRESS (5/11 done, ~130 clips when complete)
8. **Fix visual QA review** — IN PROGRESS (switched to direct API, needs rate limit management)
9. **Improve dynamic cropping** — TODO (current: C grade, target: A grade like Opus Clip. Zoom out for wide shots, better face tracking smoothing)
10. **Monitor engagement** — TODO (track which hooks/clips perform, feed back into AI prompts)
11. **Hit 1,000 followers** — target: late Apr / early May 2026

### Key Metrics
- Followers (target: 1,000)
- Views per clip (target: 1,000+ average)
- Post completion rate (target: 95%+ success)
- Queue depth (target: always 50+ clips ready)

---

## Phase 2: Monetization (May 2026)

**Goal:** Turn followers into revenue through TikTok Creator Fund + affiliate links.

### Steps
1. **Apply for TikTok Creator Fund** (requires 1,000 followers + 1,000 views in 30 days)
2. **Add affiliate links** to bio (Amazon auto parts, car accessories)
3. **Test product mention clips** — integrate product callouts into hook text
4. **Track revenue** per clip and per product
5. **A/B test content types** — pure comedy clips vs product-adjacent clips

### Key Metrics
- Revenue per 1,000 views
- Click-through rate on affiliate links
- Revenue per clip

---

## Phase 3: Content Expansion (Jun 2026)

**Goal:** Expand beyond Top Gear. Add more content sources. Scale to multiple niches.

### Steps
1. **Add more show sources** — Grand Tour, other car shows, tech content
2. **Multi-niche accounts** — test tech/gadget clips on separate account
3. **Improve AI clip selection** — use Claude to score moments by viral potential, not just keyword heuristics
4. **Add TikTok native sound selection** via [[Playwright]] during upload (algorithm boost)
5. **Speed ramps + advanced effects** on high-energy moments

---

## Phase 4: Product Packaging (Jul 2026)

**Goal:** Package the pipeline as a standalone product other creators can use.

### Steps
1. **Strip all Top Gear / @stigscloset specifics** — make configurable
2. **Write documentation** — setup guide, configuration, monitoring, troubleshooting
3. **Case study** — before/after metrics from @stigscloset growth
4. **Dockerize** — single `docker-compose up` deployment
5. **Pricing model** — one-time purchase or monthly license
6. **Launch on indie hacker channels** — Twitter/X, ProductHunt, Reddit

---

## Architecture

```
input/episodes/*.mkv
        │
        ▼
[1] Transcribe (mlx-whisper, Metal GPU, large-v3)
        │
        ▼
[2] Select moments (heuristic scoring: excitement, catchphrases, punctuation)
        │
        ▼
[3] Cut clips (face tracking via MediaPipe OR blur-fill fallback)
        │
        ▼
[4] Generate hook (Claude Opus) + post caption (Claude Opus)
        │
        ▼
[5] Add captions (Pillow frame-by-frame: bounce animation, keyword highlights)
        │
        ▼
[6] Add SFX (whoosh on hook, bass hits on dramatic moments)
        │
        ▼
[7] Auto-review (Anthropic API: extract frames, visual grade A-F)
        │
        ▼
[8] Queue in SQLite → Scheduler posts 10x/day → Playwright uploads
        │
        ▼
[9] GC: delete raw clips after captioning, delete all clips after posting
```

## Key Files

| File | Purpose |
|---|---|
| `scripts/pipeline_growth.py` | Main orchestrator |
| `scripts/add_captions.py` | V4 caption engine (~600 lines) |
| `scripts/cut_clips.py` | Face tracking + blur-fill |
| `scripts/clip_selector.py` | Moment selection |
| `scripts/generate_top_hook.py` | AI hook generation (Opus + heuristic fallback) |
| `scripts/generate_post_caption.py` | AI post caption + hashtags |
| `scripts/add_effects.py` | SFX + snap zooms |
| `scripts/upload_tiktok.py` | [[Playwright]] TikTok uploader |
| `scripts/scheduler.py` | 10x/day posting scheduler |
| `scripts/auto_review.py` | Visual QA via Anthropic API |
| `scripts/dashboard.py` | Monitoring UI at hermes:8888 |
| `scripts/fetch_followers.py` | TikTok follower tracking |
| `config.json` | Posting config, account settings |
| `data/pipeline.db` | [[SQLite]] state management |

## Decisions Log

- **2026-04-09** — Removed trending audio entirely; TikTok showed "original audio" regardless of mixing, providing zero algorithm benefit
- **2026-04-09** — Increased posting from 5x/day to 10x/day; under 1K followers TikTok doesn't penalize volume
- **2026-04-09** — Fixed Claude CLI auth via `.credentials.json` from OAuth token; enables AI hooks/captions/review from background processes
- **2026-04-09** — Switched auto_review from Claude CLI `--file` flag (broken for images) to direct Anthropic API with base64 frames
- **2026-04-09** — Used Sonnet for visual reviews (fast + cheap) while keeping Opus for hook/caption generation (quality)
- **2026-04-09** — Kept Playwright browser visible (not headless) for debugging
- **2026-04-09** — Caption highlight color set to Top Gear brand blue (#1F87FF)
- **2026-04-09** — Clip duration range changed from fixed 30s to 12-45s for natural endings
