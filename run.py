"""本地/局域网启动入口；环境变量详见 docs/PARAMETERS.md。"""
import os
import webbrowser

from waitress import create_server

from app import create_app

if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    # 先完成数据库初始化和端口绑定，再报告启动成功。
    server = create_server(create_app(), host=host, port=port, threads=4)
    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{server.effective_port}"
    print(f"小荔枝已启动：{url}\n保持此窗口运行，关闭窗口或按 Ctrl+C 会停止服务。", flush=True)
    if os.getenv("OPEN_BROWSER", "0") == "1":
        webbrowser.open(url)
    server.run()
