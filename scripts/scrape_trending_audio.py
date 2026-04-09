#!/usr/bin/env python3
"""
Scrape trending TikTok sounds and download them for use in the pipeline.
Uses TikTok's discover/music page to find currently trending audio.

Usage:
    python3 scripts/scrape_trending_audio.py
    python3 scripts/scrape_trending_audio.py --count 5
"""

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRENDING_DIR = PROJECT_ROOT / "assets" / "trending_audio"


async def scrape_trending_sounds(count: int = 5) -> list[dict]:
    """
    Scrape trending sounds from TikTok using Playwright.
    Returns list of {title, author, play_url, music_id}.
    """
    from playwright.async_api import async_playwright

    sounds = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = await context.new_page()

        # Intercept API responses to capture music data
        music_data = []

        async def handle_response(response):
            url = response.url
            if "api" in url and "music" in url.lower():
                try:
                    data = await response.json()
                    music_data.append(data)
                except:
                    pass

        page.on("response", handle_response)

        print("Loading TikTok trending page...")
        try:
            await page.goto("https://www.tiktok.com/discover", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(3000)
        except Exception as e:
            print(f"  Discover page failed: {e}")
            # Try alternative: search for trending sounds
            try:
                await page.goto("https://www.tiktok.com/search?q=trending%20sounds&t=1", wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(3000)
            except:
                pass

        # Try to find music links on the page
        print("Extracting music links...")
        music_links = await page.query_selector_all('a[href*="/music/"]')

        for link in music_links[:count * 2]:
            try:
                href = await link.get_attribute("href")
                text = await link.inner_text()
                if href and "/music/" in href:
                    # Extract music ID from URL
                    music_id_match = re.search(r'/music/[^/]+-(\d+)', href)
                    if music_id_match:
                        sounds.append({
                            "title": text.strip()[:80] or "Unknown",
                            "url": href if href.startswith("http") else f"https://www.tiktok.com{href}",
                            "music_id": music_id_match.group(1),
                        })
            except:
                continue

        # If we didn't get enough from discover, try the trending hashtag approach
        if len(sounds) < count:
            print("Trying trending videos for audio extraction...")
            try:
                await page.goto("https://www.tiktok.com/foryou", wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(5000)

                # Get video elements
                videos = await page.query_selector_all('[data-e2e="recommend-list-item-container"]')
                for vid in videos[:count * 2]:
                    try:
                        music_link = await vid.query_selector('a[href*="/music/"]')
                        if music_link:
                            href = await music_link.get_attribute("href")
                            text = await music_link.inner_text()
                            music_id_match = re.search(r'/music/[^/]+-(\d+)', href)
                            if music_id_match:
                                sounds.append({
                                    "title": text.strip()[:80] or "Unknown",
                                    "url": href if href.startswith("http") else f"https://www.tiktok.com{href}",
                                    "music_id": music_id_match.group(1),
                                })
                    except:
                        continue
            except:
                pass

        await browser.close()

    # Deduplicate by music_id
    seen = set()
    unique = []
    for s in sounds:
        if s["music_id"] not in seen:
            seen.add(s["music_id"])
            unique.append(s)

    return unique[:count]


async def download_sound(sound: dict, output_dir: Path) -> str:
    """Download a TikTok sound using yt-dlp or Playwright audio extraction."""
    from playwright.async_api import async_playwright

    safe_title = re.sub(r'[^\w\s-]', '', sound["title"])[:50].strip()
    output_path = output_dir / f"{safe_title}_{sound['music_id']}.mp3"

    if output_path.exists():
        print(f"  Already downloaded: {output_path.name}")
        return str(output_path)

    # Method 1: Try yt-dlp
    try:
        result = subprocess.run(
            ["yt-dlp", "-x", "--audio-format", "mp3", "-o", str(output_path), sound["url"]],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0 and output_path.exists():
            print(f"  Downloaded (yt-dlp): {output_path.name}")
            return str(output_path)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Method 2: Extract audio from a video using the sound via Playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        )
        page = await context.new_page()

        audio_urls = []

        async def capture_audio(response):
            ct = response.headers.get("content-type", "")
            if "audio" in ct or response.url.endswith((".mp3", ".m4a", ".aac")):
                audio_urls.append(response.url)

        page.on("response", capture_audio)

        try:
            await page.goto(sound["url"], wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(5000)

            # Try to play a video to trigger audio loading
            play_btn = await page.query_selector('[data-e2e="browse-play"]')
            if play_btn:
                await play_btn.click()
                await page.wait_for_timeout(3000)
        except:
            pass

        await browser.close()

        # Download first audio URL found
        for url in audio_urls:
            try:
                result = subprocess.run(
                    ["curl", "-L", "-o", str(output_path), url],
                    capture_output=True, timeout=30
                )
                if output_path.exists() and output_path.stat().st_size > 10000:
                    # Convert to mp3 with ffmpeg
                    mp3_path = output_path.with_suffix(".mp3")
                    subprocess.run(
                        ["ffmpeg", "-y", "-i", str(output_path), "-c:a", "libmp3lame", "-q:a", "2", str(mp3_path)],
                        capture_output=True
                    )
                    if mp3_path.exists():
                        if output_path != mp3_path:
                            output_path.unlink(missing_ok=True)
                        print(f"  Downloaded (playwright): {mp3_path.name}")
                        return str(mp3_path)
            except:
                continue

    print(f"  FAILED to download: {sound['title']}")
    return None


async def main_async(count: int):
    TRENDING_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Scraping top {count} trending TikTok sounds...\n")
    sounds = await scrape_trending_sounds(count)

    if not sounds:
        print("No trending sounds found. TikTok may be blocking scraping.")
        print("Alternative: manually download trending sounds to assets/trending_audio/")
        return

    print(f"\nFound {len(sounds)} trending sounds:")
    for i, s in enumerate(sounds):
        print(f"  {i+1}. {s['title']} (ID: {s['music_id']})")

    print(f"\nDownloading to {TRENDING_DIR}...\n")
    downloaded = 0
    for sound in sounds:
        result = await download_sound(sound, TRENDING_DIR)
        if result:
            downloaded += 1

    print(f"\nDone: {downloaded}/{len(sounds)} sounds downloaded")

    # List what's in the folder
    audio_files = list(TRENDING_DIR.glob("*.mp3")) + list(TRENDING_DIR.glob("*.m4a"))
    print(f"Total trending audio files: {len(audio_files)}")
    for f in audio_files:
        print(f"  {f.name} ({f.stat().st_size / 1024:.0f}KB)")


def main():
    parser = argparse.ArgumentParser(description="Scrape trending TikTok sounds")
    parser.add_argument("--count", type=int, default=5, help="Number of sounds to scrape")
    args = parser.parse_args()

    asyncio.run(main_async(args.count))


if __name__ == "__main__":
    main()
