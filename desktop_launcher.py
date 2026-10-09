"""Windows desktop entry point; runs a loopback-only server and opens a browser."""
import argparse
import atexit
import json
import logging
import os
import socket
import sys
import threading
import time
from urllib.parse import urlparse
import webbrowser

from product_paths import DATA_DIR, ENGINE_DIR

VERSION = '1.0.0'


class InstanceLock:
    """One database writer service per Windows user data directory."""
    def __init__(self, directory):
        import msvcrt
        self.handle = (directory / 'app.lock').open('a+b')
        self.handle.seek(0)
        try:
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.handle.close()
            raise RuntimeError('弈习已经在运行。')

    def close(self):
        if not self.handle.closed:
            self.handle.close()


def existing_url(directory):
    try:
        value = json.loads((directory / 'instance.json').read_text(encoding='utf-8'))['url']
        url = urlparse(value)
        if url.scheme == 'http' and url.hostname == '127.0.0.1' and url.port and url.path in ('', '/'):
            return value
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def start_service():
    import uvicorn
    import server
    # CREATE TABLE IF NOT EXISTS initializes a fresh private database without demo data.
    with server.database():
        pass
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    service = uvicorn.Server(uvicorn.Config(
        server.app, host='127.0.0.1', port=port, loop='asyncio', http='h11',
        ws='none', lifespan='on', log_config=None, access_log=False))
    thread = threading.Thread(target=service.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    for _ in range(200):
        if service.started:
            url = f'http://127.0.0.1:{port}'
            (DATA_DIR / 'instance.json').write_text(json.dumps({'version': VERSION, 'url': url, 'pid': os.getpid()}), encoding='utf-8')
            return service, thread, sock, url
        if not thread.is_alive():
            break
        time.sleep(.05)
    sock.close()
    raise RuntimeError('本地服务启动失败，请查看数据目录中的 launcher.log。')


def self_test(url):
    """Opt-in packaged acceptance check; requires a separate empty test directory."""
    import base64
    import urllib.request
    from product_paths import RESOURCE_ROOT
    def request(path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(url+path, data=data, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.load(response)
    for asset in ('/', '/app.js', '/style.css'):
        with urllib.request.urlopen(url+asset, timeout=10) as response:
            assert response.status == 200 and len(response.read()) > 100
    assert request('/api/games') == [], 'Acceptance requires a fresh data directory'
    game = request('/api/import', {'content': base64.b64encode((RESOURCE_ROOT/'examples/study-demo.sgf').read_bytes()).decode()})
    analysis = request(f"/api/games/{game['id']}/analysis", {'node': game['cursor'], 'visits': 100})
    point = request(f"/api/games/{game['id']}/study-points", {'node': 'root/0', 'revision': game['revision']})
    assert point['saved'], point
    session = request(f"/api/study/{point['point']['id']}/start", {})
    result = request(f"/api/study-attempts/{session['attempt_id']}/answer", {'action': None})
    assert result['verdict'] == 'revealed'
    comparison = request(f"/api/study-attempts/{session['attempt_id']}/comparison", {})
    assert comparison['lines']['recommended']['frames']
    summary = {'ok': True, 'version': VERSION, 'data_dir': str(DATA_DIR),
               'fresh_database': True, 'assets': True, 'real_katago': bool(analysis),
               'study_and_comparison': True, 'games': len(request('/api/games'))}
    (DATA_DIR/'self-test.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')


def run_window(url, service, thread):
    import tkinter as tk
    from tkinter import ttk
    window = tk.Tk()
    window.title(f'弈习 · {VERSION}')
    window.geometry('510x300')
    window.resizable(False, False)
    window.configure(bg='#f8f5ef')
    frame = tk.Frame(window, bg='#f8f5ef', padx=28, pady=24)
    frame.pack(fill='both', expand=True)
    tk.Label(frame, text='弈习  GO STUDY', font=('Microsoft YaHei', 22, 'bold'), fg='#193a32', bg='#f8f5ef').pack(anchor='w')
    tk.Label(frame, text='本地服务已启动，棋谱仅保存在这台电脑。', font=('Microsoft YaHei', 11), bg='#f8f5ef').pack(anchor='w', pady=(12, 5))
    tk.Label(frame, text=url, font=('Segoe UI', 11), fg='#24745b', bg='#f8f5ef').pack(anchor='w')
    tk.Label(frame, text='首次分析可能需要显卡调优，请稍候。\n关闭此窗口会停止服务，不会删除棋谱。', justify='left', font=('Microsoft YaHei', 10), fg='#657268', bg='#f8f5ef').pack(anchor='w', pady=14)
    buttons = ttk.Frame(frame)
    buttons.pack(anchor='w')
    ttk.Button(buttons, text='打开弈习', command=lambda: webbrowser.open(url)).pack(side='left', padx=(0, 8))
    ttk.Button(buttons, text='打开数据目录', command=lambda: os.startfile(DATA_DIR)).pack(side='left', padx=(0, 8))
    def close():
        service.should_exit = True
        thread.join(timeout=8)
        window.destroy()
    ttk.Button(buttons, text='退出', command=close).pack(side='left')
    window.protocol('WM_DELETE_WINDOW', close)
    window.after(300, lambda: webbrowser.open(url))
    window.mainloop()


def main():
    parser = argparse.ArgumentParser(description='弈习 Windows 启动器')
    parser.add_argument('--self-test', action='store_true', help='仅用于独立空数据目录的打包验收')
    args = parser.parse_args()
    if args.self_test and not os.environ.get('YIXI_DATA_DIR'):
        raise RuntimeError('--self-test 必须通过 YIXI_DATA_DIR 指定独立空目录')
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=DATA_DIR/'launcher.log', level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s', encoding='utf-8')
    try:
        lock = InstanceLock(DATA_DIR)
    except RuntimeError:
        url = existing_url(DATA_DIR)
        if url and not args.self_test:
            webbrowser.open(url)
            return 0
        raise
    atexit.register(lock.close)
    service = thread = sock = None
    try:
        if not (ENGINE_DIR/'katago.exe').is_file():
            raise RuntimeError('缺少引擎，请完整解压整个文件夹后再运行 YixiGo.exe。')
        service, thread, sock, url = start_service()
        if args.self_test:
            self_test(url)
        else:
            run_window(url, service, thread)
        return 0
    finally:
        if service:
            service.should_exit = True
            thread.join(timeout=8)
            # Ensure an in-flight analysis cannot leave an orphan engine on exit.
            import server
            server.engine.close()
        if sock:
            sock.close()
        lock.close()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        logging.exception('Launcher failed')
        if '--self-test' not in sys.argv:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror('弈习启动失败', f'{exc}\n\n日志目录：{DATA_DIR}', parent=root)
            root.destroy()
        sys.exit(1)
