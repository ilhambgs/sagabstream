import os
import json
import asyncio
import time
from flask import Flask, Response
from playwright.async_api import async_playwright
import urllib.request
import base64
from urllib.parse import urlparse, parse_qs, unquote

app = Flask(__name__)

SOURCE_URL = "https://livelive24.com/test/ntv/ntv.json"

# Cache untuk menyimpan hasil M3U (Cache selama 15 menit agar tidak berat di server)
cache_playlist = {"content": "", "timestamp": 0}

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
    
    async def run_browser():
        async with async_playwright() as p:
            # Gunakan chromium headless dengan argumen server
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
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
                await page.goto(page_url, timeout=12000)
                await asyncio.sleep(4) # Waktu tunggu player meload file m3u8
            except Exception:
                pass
            await browser.close()
            return real_url

    try:
        return await run_browser()
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
            # Tangkap link .m3u8 asli secara otomatis menggunakan Playwright
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

@app.route("/")
def index():
    global cache_playlist
    current_time = time.time()
    
    # Jika cache kosong atau sudah lebih dari 15 menit,generate ulang
    if not cache_playlist["content"] or (current_time - cache_playlist["timestamp"] > 900):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        cache_playlist["content"] = loop.run_until_complete(generate_m3u_async())
        cache_playlist["timestamp"] = current_time

    return Response(cache_playlist["content"], mimetype="text/plain; charset=utf-8")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
