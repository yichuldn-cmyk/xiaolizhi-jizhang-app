"""免费临时公网入口：独立账本、HTTPS通道、启动后保留后台进程。"""
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

from launcher import ROOT, VERSION, health

EXPECTED = {"app": "lizhi-ledger", "version": VERSION, "profile": "public"}


def tunnel_url(text):
    """仅从SSH的转发成功行提取地址，忽略帮助链接和管理后台地址。"""
    match = re.search(r"tunneled with tls termination,\s*(https://[a-zA-Z0-9.-]+\.(?:lhr\.life|localhost\.run))", text)
    return match.group(1) if match else None


def start_monitor(data):
    """后台进程通过独占本机端口保证只运行一个保活器。"""
    with (data / "monitor.log").open("ab") as log:
        subprocess.Popen([sys.executable, "-u", str(ROOT / "public_share.py"), "--keep-alive"],
                         cwd=ROOT, stdout=log, stderr=log,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def publish(open_browser=True, monitor=True):
    """复用有效公网链接；新通道链接写入data/public/public-url.txt。"""
    data = ROOT / "data/public"
    data.mkdir(parents=True, exist_ok=True)
    expected = EXPECTED
    url_file = data / "public-url.txt"
    if url_file.exists():
        previous = url_file.read_text(encoding="utf-8").strip()
        if previous.startswith("https://") and health(previous, timeout=12) == expected:
            if monitor:
                start_monitor(data)
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
        url = tunnel_url(text)
        if url:
            # 地址被分配不等于可以访问，验证HTTPS转发后再交给用户。
            ready_deadline = time.monotonic() + 45
            while health(url, timeout=8) != expected:
                if tunnel.poll() is not None or time.monotonic() > ready_deadline:
                    tunnel.terminate()
                    raise RuntimeError("公网地址已分配，但HTTPS验证失败，请重新连接。")
                time.sleep(1)
            url_file.write_text(url, encoding="utf-8")
            if monitor:
                start_monitor(data)
            print(f"临时公开链接：{url}\n电脑需保持开机，通道断开或重启后地址可能变化。")
            if open_browser:
                webbrowser.open(url)
            return url
        if tunnel.poll() is not None:
            raise RuntimeError("公网通道连接失败，请查看data/public/tunnel.log。")
        time.sleep(0.5)
    tunnel.terminate()
    raise RuntimeError("公网通道连接超时，请稍后重新运行。")


def refresh_connection(data):
    """产生真实HTTP访问以保持通道活跃；失效时重新建连并更新本机地址文件。"""
    path = data / "public-url.txt"
    url = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    if url.startswith("https://") and health(url, timeout=12) == EXPECTED:
        return url
    return publish(open_browser=False, monitor=False)


def keep_alive():
    """每30秒保活；断网等外部故障记录日志后重试，不退出后台服务。"""
    data = ROOT / "data/public"
    with socket.socket() as guard:
        if os.name == "nt":
            guard.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            guard.bind(("127.0.0.1", 8004))
        except OSError:
            return  # 已有同一应用保活器占用该本机端口。
        while True:
            try:
                print("保活：" + refresh_connection(data), flush=True)
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                print(f"通道重试：{error}", flush=True)
            time.sleep(30)


if __name__ == "__main__":
    if "--keep-alive" in sys.argv:
        keep_alive()
    else:
        publish("--no-browser" not in sys.argv)
