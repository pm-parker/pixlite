"""PixLite server launcher: one uvicorn server, two listeners.

- TCP  (host:port, default 0.0.0.0:8300): direct/LAN/Docker access, protected by PIXLITE_TOKEN.
- Unix socket (PIXLITE_GATEWAY_SOCKET, fnOS: ${TRIM_APPDEST}/app.sock): the fnOS unified
  gateway (/app/fnnas.pixlite) forwards here only after it has verified the NAS login
  session, so requests arriving on this socket need no PIXLITE token (see server/api/main.py).

Usage:
    python -m server.run [--host 0.0.0.0] [--port 8300] [--uds /path/app.sock]
"""
import argparse, os, signal, socket, stat, sys

import uvicorn


def _bind_uds(path):
    if os.path.exists(path):
        if not stat.S_ISSOCK(os.lstat(path).st_mode):
            sys.exit(f'refusing to replace non-socket file: {path}')
        os.unlink(path)                      # stale socket from a previous run
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(path)
    # 网关进程需要连接该 socket; 与 uvicorn --uds 默认权限一致
    os.chmod(path, 0o666)
    return s


def _bind_tcp(host, port):
    fam = socket.AF_INET6 if ':' in host else socket.AF_INET
    s = socket.socket(fam, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
    return s


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default=os.environ.get('PIXLITE_HOST', '0.0.0.0'))
    ap.add_argument('--port', type=int, default=int(os.environ.get('PIXLITE_PORT', 8300)))
    ap.add_argument('--uds', default=os.environ.get('PIXLITE_GATEWAY_SOCKET', ''))
    ap.add_argument('--no-tcp', action='store_true', help='only listen on the unix socket')
    a = ap.parse_args(argv)
    if a.uds:
        os.environ['PIXLITE_GATEWAY_SOCKET'] = a.uds   # main.py uses it to recognise gateway traffic
    socks = []
    if not a.no_tcp:
        socks.append(_bind_tcp(a.host, a.port))
    if a.uds:
        socks.append(_bind_uds(a.uds))
    if not socks:
        sys.exit('nothing to listen on')
    for s in socks:
        s.set_inheritable(True)
    cfg = uvicorn.Config('server.api.main:app', proxy_headers=False, log_level='info')
    # uvicorn 优雅退出后会用"原处理器"重新抛出捕获的 SIGTERM; 默认处理器直接杀进程,
    # 下面的 finally 来不及删除 socket 文件。装一个转成 SystemExit 的处理器让清理得以执行。
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        uvicorn.Server(cfg).run(sockets=socks)
    finally:
        if a.uds and os.path.exists(a.uds):
            try:
                os.unlink(a.uds)
            except OSError:
                pass


if __name__ == '__main__':
    main()
