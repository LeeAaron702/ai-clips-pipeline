-- Migration 001: Add clip pipeline support + tracking tables

ALTER TABLE videos ADD COLUMN content_type TEXT DEFAULT 'clip';
ALTER TABLE videos ADD COLUMN cost_usd REAL DEFAULT 0.0;
ALTER TABLE videos ADD COLUMN source_episode TEXT;
ALTER TABLE videos ADD COLUMN clip_start_sec REAL;
ALTER TABLE videos ADD COLUMN clip_end_sec REAL;

CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL UNIQUE,
    title TEXT,
    duration_seconds REAL,
    transcript_path TEXT,
    clips_extracted INTEGER DEFAULT 0,
    clips_posted INTEGER DEFAULT 0,
    processed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS follower_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    count INTEGER NOT NULL,
    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS budget_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    month TEXT NOT NULL,
    item_type TEXT NOT NULL,
    video_id INTEGER REFERENCES videos(id),
    cost_usd REAL NOT NULL,
    service TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
