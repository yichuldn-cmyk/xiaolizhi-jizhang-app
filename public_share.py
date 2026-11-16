"""免费临时公网入口：独立账本、HTTPS通道、启动后保留后台进程。"""
import os
import re
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from launcher import ROOT, VERSION, health


def publish(open_browser=True):
    """复用有效公网链接；新通道链接写入data/public/public-url.txt。"""
    data = ROOT / "data/public"
    data.mkdir(parents=True, exist_ok=True)
    expected = {"app": "lizhi-ledger", "version": VERSION, "profile": "public"}
    url_file = data / "public-url.txt"
    if url_file.exists():
        previous = url_file.read_text(encoding="utf-8").strip()
        if previous.startswith("https://") and health(previous, timeout=12) == expected:
            if open_browser:
                webbrowser.open(previous)
            return previous
    if health("http://127.0.0.1:8003") != expected:
        env = {**os.environ, "DATA_DIR": str(data), "HOST": "127.0.0.1", "PORT": "8003",
               "COOKIE_SECURE": "1", "OPEN_BROWSER": "0", "PYTHONUTF8": "1"}
        with (data / "server.log").open("ab") as log:
            server = subprocess.Popen([sys.executable, "-u", str(ROOT / "run.py")], cwd=ROOT,
                                      env=env, stdout=log, stderr=log,
                                      creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        deadline = time.monotonic() + 20
        while health("http://127.0.0.1:8003") != expected:
            if server.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("公网账本服务启动失败，请查看data/public/server.log。")
            time.sleep(0.25)
    ssh = shutil.which("ssh")
    if ssh is None:
        raise RuntimeError("需要系统的OpenSSH客户端。")
    arguments = [ssh, "-T", "-o", "StrictHostKeyChecking=accept-new", "-o",
                 "UserKnownHostsFile=data/public/known_hosts", "-o", "ExitOnForwardFailure=yes",
                 "-o", "ServerAliveInterval=30", "-o", "ConnectTimeout=15",
                 "-R", "80:127.0.0.1:8003", "nokey@localhost.run"]
    output = data / "tunnel.log"
    with output.open("wb") as log:
        tunnel = subprocess.Popen(arguments, cwd=ROOT, stdout=log, stderr=log,
                                  creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        text = output.read_text(encoding="utf-8", errors="replace")
        # 只接受实际转发成功行，不能把服务商帮助/管理网址误当成应用链接。
        match = re.search(r"tunneled with tls termination,\s*(https://[a-zA-Z0-9.-]+\.(?:lhr\.life|localhost\.run))", text)
        if match:
            url = match.group(1)
            url_file.write_text(url, encoding="utf-8")
            print(f"临时公开链接：{url}\n电脑需保持开机，通道断开或重启后地址可能变化。")
            if open_browser:
                webbrowser.open(url)
            return url
        if tunnel.poll() is not None:
            raise RuntimeError("公网通道连接失败，请查看data/public/tunnel.log。")
        time.sleep(0.5)
    tunnel.terminate()
    raise RuntimeError("公网通道连接超时，请稍后重新运行。")


if __name__ == "__main__":
    publish("--no-browser" not in sys.argv)
