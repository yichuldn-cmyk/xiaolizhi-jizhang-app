"""双击启动器：复用本应用、后台启动、等待就绪、打开浏览器。"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = "2026.11.16.1"


def health(url):
    """绕过系统代理检查本机就绪状态；失败仅返回None。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url + "/health", timeout=1) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def launch(mode, open_browser=True):
    """启动独立账本模式；后台日志持久化，拒绝误用被其他服务占用的端口。"""
    port = 8001 if mode == "demo" else 8000
    data = ROOT / "data" / "demo" if mode == "demo" else ROOT / "data"
    data.mkdir(parents=True, exist_ok=True)
    url = f"http://127.0.0.1:{port}"
    expected = {"app": "lizhi-ledger", "version": VERSION, "profile": mode}
    state = health(url)
    if state != expected:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"端口 {port} 被其他服务或旧版本占用，请关闭旧服务窗口后重试。")
        env = {**os.environ, "HOST": "0.0.0.0", "PORT": str(port),
               "DATA_DIR": str(data), "OPEN_BROWSER": "0", "PYTHONUTF8": "1"}
        if mode == "demo":
            subprocess.run([sys.executable, str(ROOT / "manage.py"), "demo"], cwd=ROOT, env=env, check=True)
        # Windows隐藏后台服务窗口，启动器退出后服务继续运行。
        with (data / "server.log").open("ab") as log:
            process = subprocess.Popen([sys.executable, "-u", str(ROOT / "run.py")],
                                       cwd=ROOT, env=env, stdout=log, stderr=log,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        deadline = time.monotonic() + 20
        while health(url) != expected:
            if process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError(f"服务启动失败，请查看 {data / 'server.log'}。")
            time.sleep(0.25)
    print(f"小荔枝已就绪：{url}")
    if open_browser:
        webbrowser.open(url)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="小荔枝一键启动")
    parser.add_argument("--mode", choices=["demo", "personal"], default="personal")
    parser.add_argument("--no-browser", action="store_true", help="仅验证服务，不打开浏览器")
    args = parser.parse_args()
    launch(args.mode, not args.no_browser)
