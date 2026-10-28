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

playlist_cache = "#EXTM3U\n# Sedang memproses data di latar belakang, silakan refresh beberapa saat lagi..."
stats_info = {
    "total_sources": 0,
    "success_m3u8": 0,
    "failed_m3u8": 0,
    "last_updated": "Belum pernah"
}

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
        return page_url, False
    
    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, 
                args=[
                    "--no-sandbox", 
                    "--disable-setuid-sandbox", 
                    "--disable-dev-shm-usage", 
                    "--disable-gpu",
                    "--disable-extensions",
                    "--no-zygote"
                ]
            )
            context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            page = await context.new_page()
            
            real_url = page_url
            found = False
            
            # Pantau semua request jaringan yang mengandung m3u8 atau manifest
            async def handle_request(request):
                nonlocal real_url, found
                req_url = request.url
                if any(ext in req_url.lower() for ext in [".m3u8", ".ts", "playlist.m3u8", "manifest"]):
                    real_url = req_url
                    found = True

            page.on("request", handle_request)
            
            try:
                await page.goto(page_url, timeout=9000)
                # Tunggu sebentar agar script player sempat mengeksekusi fetch/XHR stream
                await asyncio.sleep(3)
                
                # Cek juga isi sumber halaman (source code) kalau-kalau link m3u8 tertanam di dalam script JS
                content = await page.content()
                import re
                m3u8_matches = re.findall(r'https?://[^\s<>"]+?\.m3u8[^\s<>"]*', content)
                if m3u8_matches:
                    real_url = m3u8_matches[0]
                    found = True
            except Exception:
                pass
            
            await browser.close()
            return real_url, found
    except Exception as e:
        print(f"⚠️ Error tangkap stream: {e}")
        if browser:
            try:
                await browser.close()
            except:
                pass
        return page_url, False

async def generate_m3u_async():
    global stats_info
    print("📡 Mengunduh data JSON sumber...")
    req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"❌ Gagal mengambil JSON: {e}")
        return f"#EXTM3U\n# Error fetching JSON: {e}"

    success_count = 0
    fail_count = 0
    total_count = 0

    lines = ['#EXTM3U']
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

            total_count += 1
            stream_name = src.get("channelName") or src.get("name") or src.get("quality") or "Stream"
            channel_title = f"{title} ({stream_name})"
            
            decoded_url = decode_stream_url(raw_url)
            final_url, is_success = await capture_m3u8(decoded_url)
            
            if is_success:
                success_count += 1
            else:
                fail_count += 1

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

    stats_info["total_sources"] = total_count
    stats_info["success_m3u8"] = success_count
    stats_info["failed_m3u8"] = fail_count
    stats_info["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

    header_info = (
        f"# 📊 STATISTIK M3U8:\n"
        f"# Total Channel : {total_count}\n"
        f"# Berhasil (.m3u8): {success_count}\n"
        f"# Gagal/Fallback: {fail_count}\n"
        f"# Diperbarui    : {stats_info['last_updated']}\n"
        f"{'='*50}\n"
    )
    lines.insert(1, header_info)

    return "\n".join(lines)

def background_updater():
    global playlist_cache
    while True:
        print("🔄 Background worker mulai merayapi link .m3u8...")
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            new_content = loop.run_until_complete(generate_m3u_async())
            if new_content and "#EXTM3U" in new_content:
                playlist_cache = new_content
            print("✅ Selesai! Cache M3U berhasil diperbarui.")
        except Exception as e:
            print(f"❌ Error pada background worker: {e}")
        
        time.sleep(1800)

updater_thread = threading.Thread(target=background_updater, daemon=True)
updater_thread.start()

@app.route("/")
def index():
    return Response(playlist_cache, mimetype="text/plain; charset=utf-8")

@app.route("/status")
def status():
    return stats_info

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
