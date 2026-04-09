#!/usr/bin/env python3
"""
Custom TikTok upload using Playwright. V3: Fixed post button selector.
"""

import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


def load_cookies(path: str) -> list[dict]:
    cookies = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            cookie = {
                "name": parts[5], "value": parts[6], "domain": parts[0],
                "path": parts[2], "secure": parts[3].upper() == "TRUE",
                "sameSite": "Lax",
            }
            try:
                exp = int(parts[4])
                if exp > 0:
                    cookie["expires"] = exp
            except ValueError:
                pass
            cookies.append(cookie)
    return cookies


def dismiss_modals(page):
    for sel in ["button:has-text('Got it')", "button:has-text('Accept all')",
                "[data-e2e='modal-close-inner-button']"]:
        try:
            btn = page.locator(sel)
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click()
                print(f"  Dismissed: {sel}")
                time.sleep(1)
        except Exception:
            pass


def upload_video(video_path: str, description: str, cookies_path: str = "cookies.txt",
                 headless: bool = False) -> bool:
    video_path = str(Path(video_path).resolve())
    cookies = load_cookies(cookies_path)

    print(f"Uploading: {Path(video_path).name}")
    print(f"Caption: {description[:80]}...")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 1200},  # Taller viewport to see Post button
        )

        for c in cookies:
            try:
                context.add_cookies([c])
            except Exception:
                pass

        page = context.new_page()
        page.goto("https://www.tiktok.com/upload", wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        if "login" in page.url:
            print("ERROR: Not logged in")
            browser.close()
            return False

        print(f"On: {page.url}")
        dismiss_modals(page)

        # Upload video file
        print("Uploading video...")
        file_input = page.locator("input[type='file']")
        file_input.wait_for(state="attached", timeout=15000)
        file_input.set_input_files(video_path)
        print("  File selected")

        # Wait for processing - check for upload complete indicator
        print("Waiting for video to process...")
        for i in range(30):
            time.sleep(2)
            dismiss_modals(page)
            # Check if the "Replace" button appears (means upload complete)
            try:
                if page.locator("button:has-text('Replace')").count() > 0:
                    print(f"  Video processed (took ~{(i+1)*2}s)")
                    break
            except Exception:
                pass
        else:
            print("  Proceeding after 60s wait")

        time.sleep(2)
        dismiss_modals(page)

        # Set description
        print("Setting description...")
        desc_set = False
        for sel in ["div[contenteditable='true']", "div[role='textbox']",
                     "div.public-DraftEditor-content", "div[data-text='true']"]:
            try:
                field = page.locator(sel).first
                if field.is_visible(timeout=3000):
                    field.click()
                    time.sleep(0.5)
                    page.keyboard.press("Meta+A")
                    page.keyboard.press("Backspace")
                    time.sleep(0.3)
                    # Type without delays for non-hashtag words, with delays for hashtags
                    for word in description.split():
                        if word.startswith("#"):
                            page.keyboard.type(word, delay=30)
                            time.sleep(0.3)
                            page.keyboard.press("Escape")
                            time.sleep(0.2)
                            page.keyboard.type(" ", delay=10)
                        else:
                            page.keyboard.type(word + " ", delay=10)
                    print(f"  Description set via: {sel}")
                    desc_set = True
                    break
            except Exception:
                continue

        if not desc_set:
            print("  WARNING: Could not set description")

        page.screenshot(path="/tmp/tiktok_after_desc.png")

        # Scroll down to find the Post button at the bottom of the form
        print("Scrolling to Post button...")
        # Scroll the main content area down
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1)

        # Also try scrolling the form container
        try:
            form_containers = page.locator("div[class*='upload']").all()
            for fc in form_containers:
                try:
                    fc.evaluate("el => el.scrollTop = el.scrollHeight")
                except Exception:
                    pass
        except Exception:
            pass

        time.sleep(1)
        page.screenshot(path="/tmp/tiktok_scrolled.png")

        # Find the actual Post button (NOT the sidebar "Posts" link)
        print("Looking for Post button...")
        posted = False

        # Strategy 1: Find buttons with exact "Post" text (not "Posts")
        try:
            buttons = page.locator("button").all()
            for btn in buttons:
                try:
                    text = (btn.text_content() or "").strip()
                    # Must be exactly "Post", not "Posts" or "Post video"
                    if text == "Post" and btn.is_visible():
                        # Additional check: should NOT be in the sidebar
                        bbox = btn.bounding_box()
                        if bbox and bbox["x"] > 100:  # Sidebar is on the left (~80px)
                            btn.scroll_into_view_if_needed()
                            time.sleep(0.5)
                            # Check disabled state
                            disabled = btn.get_attribute("disabled")
                            if disabled is not None:
                                print(f"  Post button found but disabled, waiting...")
                                for _ in range(20):
                                    time.sleep(3)
                                    if btn.get_attribute("disabled") is None:
                                        break
                            btn.click()
                            print(f"  Clicked Post button (x={bbox['x']:.0f}, y={bbox['y']:.0f})")
                            posted = True
                            break
                except Exception:
                    continue
        except Exception as e:
            print(f"  Button search error: {e}")

        # Strategy 2: data-e2e selector
        if not posted:
            for sel in ["[data-e2e='upload_post-button']", "[data-e2e='post-button']",
                        "button[class*='post' i]", "button[class*='Post']"]:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=2000):
                        btn.click()
                        print(f"  Clicked via: {sel}")
                        posted = True
                        break
                except Exception:
                    continue

        # Strategy 3: Find the last big button in the form area
        if not posted:
            try:
                # Look for buttons in the main content (right of sidebar)
                main_buttons = page.locator("button").all()
                candidate = None
                for btn in main_buttons:
                    try:
                        bbox = btn.bounding_box()
                        text = (btn.text_content() or "").strip().lower()
                        if bbox and bbox["x"] > 100 and text in ("post", "publish", "upload"):
                            candidate = btn
                    except Exception:
                        continue
                if candidate:
                    candidate.click()
                    print(f"  Clicked candidate button: {candidate.text_content()}")
                    posted = True
            except Exception:
                pass

        if posted:
            print("Waiting for post confirmation...")
            time.sleep(3)
            # Handle "Continue to post?" confirmation dialog
            try:
                post_now = page.locator("button:has-text('Post now')")
                if post_now.count() > 0 and post_now.first.is_visible():
                    post_now.first.click()
                    print("  Clicked 'Post now' confirmation")
            except Exception:
                pass
            time.sleep(2)
            # Also try for any other confirmation
            try:
                confirm = page.locator("button:has-text('Confirm')")
                if confirm.count() > 0 and confirm.first.is_visible():
                    confirm.first.click()
                    print("  Clicked Confirm")
            except Exception:
                pass
            print("Waiting for post to complete...")
            for i in range(45):
                time.sleep(2)
                try:
                    url = page.url
                    if "manage" in url or url != "https://www.tiktok.com/tiktokstudio/upload":
                        print(f"  Redirected: {url}")
                        break
                    # Check for success
                    if page.locator("text='Your video is being uploaded'").count() > 0:
                        print("  Upload confirmed!")
                        break
                    if page.locator("text='Manage your posts'").count() > 0:
                        print("  Post confirmed!")
                        break
                except Exception:
                    pass
        else:
            print("ERROR: Could not find Post button")

        page.screenshot(path="/tmp/tiktok_final.png")
        print(f"Final URL: {page.url}")
        print(f"Result: {'SUCCESS' if posted else 'FAILED'}")

        browser.close()
        return posted


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--caption", required=True)
    parser.add_argument("--cookies", default="cookies.txt")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()
    success = upload_video(args.video, args.caption, args.cookies, args.headless)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
