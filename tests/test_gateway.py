"""fnOS unified-gateway access: requests arriving on the gateway unix socket are
authenticated by the NAS login session and need no PIXLITE token; TCP clients cannot
impersonate the gateway (forged X-Trim-* headers are stripped, token still required)."""
import os, sys, importlib, pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'server', 'api'))
SOCK = '/tmp/pixlite-test-gw.sock'
P = '/app/fnnas.pixlite'
ADMIN = {'X-Trim-Userid': '1000', 'X-Trim-Username': 'admin', 'X-Trim-Isadmin': 'true'}
USER = {'X-Trim-Userid': '1001', 'X-Trim-Username': 'bob', 'X-Trim-Isadmin': 'false'}
TOKEN = 't' * 40


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv('PIXLITE_WORK_DIR', str(tmp_path / 'work'))
    monkeypatch.setenv('PIXLITE_ALLOWED_ROOTS', str(tmp_path))
    monkeypatch.setenv('PIXLITE_TOKEN', TOKEN)
    monkeypatch.setenv('PIXLITE_GATEWAY_SOCKET', SOCK)
    import main
    importlib.reload(main)
    from starlette.testclient import TestClient
    remote = TestClient(main.app, client=('203.0.113.9', 5000))

    # TestClient cannot set scope['server']; wrap the app so the scope looks like a
    # connection accepted on the gateway unix socket (what uvicorn produces in server/run.py)
    async def as_gateway(scope, receive, send):
        scope = dict(scope, server=(SOCK, None), client=None)
        return await main.app(scope, receive, send)
    gw = TestClient(as_gateway)
    return main, gw, remote


def test_gateway_needs_no_token(srv):
    _, gw, _ = srv
    assert gw.get(P + '/api/health').json()['auth'] == 'gateway'
    assert gw.get(P + '/api/presets').status_code == 200


def test_gateway_prefix_redirect_and_strip(srv):
    _, gw, _ = srv
    r = gw.get(P, follow_redirects=False)
    assert r.status_code == 308 and r.headers['location'] == P + '/'
    assert gw.get(P + '/api/health').status_code == 200          # prefix stripped
    assert gw.get('/api/health').status_code == 200              # unprefixed also works


def test_remote_tcp_still_requires_token(srv):
    _, _, remote = srv
    assert remote.get('/api/health').json()['auth'] == 'token'
    assert remote.get('/api/presets').status_code == 401
    assert remote.get('/api/presets', headers={'X-API-Key': TOKEN}).status_code == 200


def test_forged_trim_headers_on_tcp_do_not_bypass(srv):
    _, _, remote = srv
    assert remote.get('/api/presets', headers=ADMIN).status_code == 401
    assert remote.get(P + '/api/presets', headers=ADMIN).status_code == 401
    info = remote.get('/api/auth/info', headers=ADMIN)
    assert info.status_code == 401


def test_direct_token_only_for_gateway_admin(srv):
    _, gw, remote = srv
    assert gw.get(P + '/api/auth/info', headers=ADMIN).json()['direct_token'] == TOKEN
    assert 'direct_token' not in gw.get(P + '/api/auth/info', headers=USER).json()
    assert 'direct_token' not in gw.get(P + '/api/auth/info').json()


def test_gateway_disabled_when_socket_unset(srv, monkeypatch):
    main, gw, _ = srv
    monkeypatch.setattr(main, 'GATEWAY_SOCKET', '')
    assert gw.get(P + '/api/health').json()['auth'] != 'gateway'


def test_ui_config_uses_gateway():
    src = open(os.path.join(ROOT, 'fnpack', 'build_pkg.py'), encoding='utf-8').read()
    assert "'gatewayPrefix': prefix" in src and "'gatewaySocket': 'app.sock'" in src
    main_sh = open(os.path.join(ROOT, 'fnpack', 'cmd', 'main'), encoding='utf-8').read()
    assert 'PIXLITE_GATEWAY_SOCKET="${TRIM_APPDEST}/app.sock"' in main_sh
    assert '-m server.run' in main_sh and '--uds "${PIXLITE_GATEWAY_SOCKET}"' in main_sh


def test_frontend_uses_relative_api_paths():
    import glob, re
    for f in glob.glob(os.path.join(ROOT, 'webapp', 'src', '**', '*.*'), recursive=True):
        if f.endswith(('.js', '.vue')):
            s = open(f, encoding='utf-8').read()
            assert not re.search(r"['\"`]/api/", s), f'absolute /api/ path breaks gateway prefix: {f}'
