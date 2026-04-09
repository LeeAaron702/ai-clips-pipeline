-- TikTok Pipeline Database Schema

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asin TEXT,
    tiktok_shop_id TEXT,
    title TEXT NOT NULL,
    price REAL,
    commission_pct REAL,
    image_url TEXT,
    affiliate_link TEXT,
    source TEXT DEFAULT 'amazon',
    times_used INTEGER DEFAULT 0,
    priority INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER REFERENCES products(id),
    hook_text TEXT,
    scene_prompts TEXT,  -- JSON array of scene descriptions
    narration TEXT,
    cta TEXT,
    caption TEXT,
    hashtags TEXT,
    hook_template TEXT,
    status TEXT DEFAULT 'draft',  -- draft, video_generating, ready, posted, failed
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    script_id INTEGER REFERENCES scripts(id),
    video_path TEXT,
    audio_path TEXT,
    duration_seconds REAL,
    status TEXT DEFAULT 'generating',  -- generating, assembled, ready, posted, failed
    posted_at TIMESTAMP,
    tiktok_url TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS variation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER REFERENCES videos(id),
    hook_template TEXT,
    font TEXT,
    color_scheme TEXT,
    music_track TEXT,
    video_length_sec INTEGER,
    format_type TEXT,  -- review, comparison, top5, before_after
    camera_angle TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS analytics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER REFERENCES videos(id),
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    estimated_revenue REAL DEFAULT 0,
    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
