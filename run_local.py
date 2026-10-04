"""Start Xvimo locally with one command: bot server + Cloudflare tunnel + webhook registration.

Usage (with the venv active):   python run_local.py
Stop everything:                Ctrl + C
"""
import os
import re
import shutil
import subprocess
import sys
import threading
import time

from dotenv import load_dotenv

load_dotenv(override=True)
PORT = 8000
URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def find_cloudflared() -> str:
    found = shutil.which("cloudflared")
    if found:
        return found
    local = os.path.join(os.getcwd(), "cloudflared.exe")
    if os.path.exists(local):
        return local
    sys.exit("cloudflared not found. Install it with:  winget install --id Cloudflare.cloudflared  "
             "then reopen VS Code, or put cloudflared.exe in this folder.")


def main():
    if not os.getenv("D360_API_KEY"):
        sys.exit("D360_API_KEY is missing from .env. Send START to the 360dialog sandbox number on WhatsApp first.")

    print("Starting the Xvimo bot server...")
    bot = subprocess.Popen([sys.executable, "-m", "uvicorn", "dialog360_bot:app", "--port", str(PORT)])

    print("Opening a public tunnel...")
    tunnel = subprocess.Popen([find_cloudflared(), "tunnel", "--url", f"http://localhost:{PORT}"],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)

    public_url = {}

    def watch_tunnel():
        for line in tunnel.stdout:
            match = URL_PATTERN.search(line)
            if match and "url" not in public_url:
                public_url["url"] = match.group(0)

    threading.Thread(target=watch_tunnel, daemon=True).start()

    for _ in range(60):
        if "url" in public_url or bot.poll() is not None:
            break
        time.sleep(1)
    if "url" not in public_url:
        print("Could not get a tunnel address. Check your connection, then run this again.")
        bot.terminate(); tunnel.terminate()
        sys.exit(1)

    time.sleep(3)  # give the tunnel a moment to start routing traffic
    url = public_url["url"]
    from dialog360_bot import set_webhook
    set_webhook(f"{url}/webhook")

    token = os.getenv("ADMIN_TOKEN")
    print("\n" + "=" * 64)
    print(" Xvimo is running.")
    print(f" WhatsApp webhook : {url}/webhook")
    print(f" Live web demo    : {url}/demo")
    print(f" Admin dashboard  : {url}/admin?token={token}" if token else
          " Admin dashboard  : set ADMIN_TOKEN in .env to enable it")
    print(" Press Ctrl + C to stop everything.")
    print("=" * 64 + "\n")

    try:
        while bot.poll() is None and tunnel.poll() is None:
            time.sleep(1)
        print("A process stopped unexpectedly; shutting down.")
    except KeyboardInterrupt:
        print("\nStopping Xvimo...")
    finally:
        for proc in (tunnel, bot):
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()


if __name__ == "__main__":
    main()