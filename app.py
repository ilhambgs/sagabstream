import os
import json
import asyncio
import time
import threading
from flask import Flask, Response
from playwright.async_api import async_playwright
import urllib.request
import base64
from urllib.parse import urlparse, parse_qs, unquote

app = Flask(__name__)

SOURCE_URL = "https://livelive24.com/test/ntv/ntv.json"

# Variabel global untuk menyimpan cache playlist
playlist_cache = "#EXTM3U\n# Sedang memproses data di latar belakang, silakan refresh beberapa saat lagi..."
is_updating = False

def decode_stream_url(url: str) -> str:
    if not url:
        return ""
    url = unquote(url.strip())
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "url" in qs:
        b64_str = qs["url"][0]
        try:
            padding = 4 - len(b64_str) % 4
            if padding != 4:
                b64_str += "=" * padding
            decoded = base64.b64decode(b64_str).decode("utf-8", errors="ignore")
            if decoded.startswith("http"):
                return decoded
        except Exception:
            pass
    if url.startswith("aHR0c"):
        try:
            padding = 4 - len(url) % 4
            if padding != 4:
                url += "=" * padding
            decoded = base64.b64decode(url).decode("utf-8", errors="ignore")
            if decoded.startswith("http"):
                return decoded
        except Exception:
            pass
    return url

async def capture_m3u8(page_url):
    if not page_url.startswith("http"):
        return page_url
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, 
                args=[
                    "--no-sandbox", 
                    "--disable-setuid-sandbox", 
                    "--disable-dev-shm-usage", 
                    "--single-process"
                ]
            )
            context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            page = await context.new_page()
            
            real_url = page_url
            
            async def intercept(route, request):
                nonlocal real_url
                if ".m3u8" in request.url:
                    real_url = request.url
                await route.continue_()

            await page.route("**/*", intercept)
            try:
                await page.goto(page_url, timeout=8000)
                await asyncio.sleep(2)
            except Exception:
                pass
            await browser.close()
            return real_url
    except Exception:
        return page_url

async def generate_m3u_async():
    req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return f"#EXTM3U\n# Error fetching JSON: {e}"

    lines = ['#EXTM3U', '']
    items = data if isinstance(data, list) else []

    for item in items:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or item.get("name") or "Live Match"
        group = item.get("tournament") or item.get("category") or "Sports"
        logo = item.get("image") or item.get("logo") or "https://livelive24.com/fav.png"
        item_id = str(item.get("id") or "")

        sources = item.get("sources") or item.get("streams") or []
        for src in sources:
            if not isinstance(src, dict):
                continue
            raw_url = src.get("url") or src.get("stream_url") or ""
            if not raw_url:
                continue

            stream_name = src.get("channelName") or src.get("name") or src.get("quality") or "Stream"
            channel_title = f"{title} ({stream_name})"
            
            decoded_url = decode_stream_url(raw_url)
            final_url = await capture_m3u8(decoded_url)

            extinf = (
                f'#EXTINF:-1 '
                f'tvg-id="{item_id}" '
                f'tvg-name="{channel_title}" '
                f'tvg-logo="{logo}" '
                f'group-title="{group}",'
                f'{channel_title}'
            )
            lines.append(extinf)
            lines.append(final_url)
            lines.append("")

    return "\n".join(lines)

def background_updater():
    global playlist_cache, is_updating
    while True:
        is_updating = True
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            new_content = loop.run_until_complete(generate_m3u_async())
            if new_content and "#EXTM3U" in new_content:
                playlist_cache = new_content
        except Exception as e:
            print(f"Background update error: {e}")
        is_updating = False
        
        # Update ulang setiap 30 menit sekali di latar belakang
        time.sleep(1800)

# Jalankan background thread otomatis saat aplikasi nyala
threading.Thread(target=background_updater, daemon=True).Current = True

@app.route("/")
def index():
    # Langsung berikan teks playlist dari cache secara instan (tanpa timeout)
    return Response(playlist_cache, mimetype="text/plain; charset=utf-8")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
