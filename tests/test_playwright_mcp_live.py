"""Opt-in real CDP/MCP/CLI test. Creates only disposable local fixture tabs."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import unittest
import uuid

from playwright_host.mcp import PlaywrightMCP
from playwright_host.ui import ChatGPTUI


HTML = b'''<!doctype html><title>CAH MCP fixture</title>
<label>Name<input id="name"></label><button id="apply" onclick="document.querySelector('#result').textContent=document.querySelector('#name').value;console.log('cah-applied');fetch('/ping')">Apply</button>
<p id="result">empty</p><input type="file" id="upload"><button id="dialog" onclick="alert('cah-dialog')">Dialog</button>'''


class Fixture(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Set-Cookie', 'cah_fixture_session=shared; Path=/')
        self.end_headers()
        self.wfile.write(HTML)

    def log_message(self, *_):
        pass


@unittest.skipUnless(os.environ.get('CAH_MCP_LIVE') == '1', 'Set CAH_MCP_LIVE=1 for the existing CDP browser')
class LivePlaywrightToolsTests(unittest.TestCase):
    def test_official_mcp_and_cli_share_native_cdp(self):
        root = Path(__file__).resolve().parents[1]
        cdp = os.environ.get('CAH_MCP_CDP', 'http://127.0.0.1:9222')
        output = Path(os.environ['CAH_MCP_EVIDENCE'])
        output.mkdir(parents=True, exist_ok=True)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f'http://127.0.0.1:{server.server_port}/cah-mcp-fixture'
        ui = ChatGPTUI(cdp).connect()
        original = list(ui.context.pages)
        page = ui.context.new_page()
        provider = PlaywrightMCP(cdp)
        results = []
        session = 'cah-test-' + uuid.uuid4().hex[:10]
        attached = False

        def call(name, arguments, target=url):
            print('MCP', name, flush=True)
            result = provider.execute_sync({'tool': 'browser.mcp.call', 'args': {'name': name, 'arguments': arguments}}, target)
            self.assertFalse(result.get('isError'), result)
            # Never put image bytes or unrelated browser contents into test logs.
            results.append({'name': name, 'ok': True})
            return result

        def cli(args, attach=False):
            escaped = lambda s: "'" + str(s).replace("'", "''") + "'"
            script = '& ' + escaped(root/'host/playwright_cli.ps1') + ' -Session ' + escaped(session)
            script += ' -WorkDir ' + escaped(output/'cli') + ' -CdpUrl ' + escaped(cdp)
            script += ' -Attach' if attach else ' -CliArgs @(' + ','.join(escaped(s) for s in args) + ')'
            label = 'CLI-' + ('attach' if attach else args[0])
            print(label, flush=True)
            # The CLI daemon can inherit handles on Windows. Wait for the real
            # command process, not EOF on a pipe held by that background daemon.
            with (output/(label+'.stdout.txt')).open('wb') as stdout, (output/(label+'.stderr.txt')).open('wb') as stderr:
                proc = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', base64.b64encode(script.encode('utf-16le')).decode()], stdout=stdout, stderr=stderr, timeout=90)
            stdout = (output/(label+'.stdout.txt')).read_bytes()
            stderr = (output/(label+'.stderr.txt')).read_bytes()
            (output/(label+'.exit.txt')).write_text(str(proc.returncode))
            self.assertEqual(proc.returncode, 0, stdout.decode(errors='replace') + stderr.decode(errors='replace'))
            results.append({'name': label, 'exit': proc.returncode})
            return stdout.decode('utf-8', errors='replace')

        try:
            page.goto(url, wait_until='domcontentloaded', timeout=15000)
            catalog = provider.execute_sync({'tool': 'browser.mcp.list', 'args': {'schemas': True}}, None)
            names = {t['name'] for t in catalog['tools']}
            self.assertTrue({'browser_click', 'browser_pdf_save', 'browser_mouse_click_xy', 'browser_start_tracing'} <= names)
            (output/'catalog.json').write_text(json.dumps(catalog, indent=2), encoding='utf-8')
            snap = call('browser_snapshot', {})
            self.assertIn('Apply', json.dumps(snap))
            call('browser_type', {'target': '#name', 'text': 'MCP-live'})
            call('browser_click', {'target': '#apply'})
            self.assertEqual(page.locator('#result').inner_text(), 'MCP-live')
            ev = call('browser_evaluate', {'function': '() => ({cookie:document.cookie,result:document.querySelector("#result").textContent})'})
            self.assertIn('cah_fixture_session=shared', json.dumps(ev))
            self.assertIn('cah-applied', json.dumps(call('browser_console_messages', {'level': 'info'})))
            self.assertIn('/ping', json.dumps(call('browser_network_requests', {'static': True})))
            shot = call('browser_take_screenshot', {'scale': 'css'})
            image = next(c for c in shot['content'] if c['type'] == 'image')
            pixels = base64.b64decode(image['data'])
            self.assertGreater(len(pixels), 100)
            (output/'fixture.png').write_bytes(pixels)
            pdf = call('browser_pdf_save', {})
            self.assertIn('.pdf', json.dumps(pdf))
            call('browser_click', {'target': '#dialog'})
            call('browser_handle_dialog', {'accept': True})
            call('browser_navigate', {'url': url+'?second'})
            url += '?second'
            # CLI sees and operates the exact same tab; no second browser/profile.
            cli([], attach=True)
            attached = True
            tabs = cli(['tab-list'])
            indices = [m[0] for m in re.findall(r'^- (\d+): .*\]\((.*)\)$', tabs, re.M) if m[1] == url]
            self.assertEqual(len(indices), 1, tabs)
            cli(['tab-select', indices[0]])
            cli(['fill', '#name', 'CLI-live'])
            cli(['click', '#apply'])
            self.assertEqual(page.locator('#result').inner_text(), 'CLI-live')
            value = cli(['eval', '() => document.cookie'])
            self.assertIn('cah_fixture_session=shared', value)
            cli(['snapshot'])
            cli(['detach'])
            attached = False
            provider.close()
            self.assertTrue(ui.browser.is_connected())
            self.assertTrue(all(not p.is_closed() for p in original))
            self.assertEqual(page.locator('#result').inner_text(), 'CLI-live')
            (output/'live-result.json').write_text(json.dumps({'status': 'PASS', 'tool_count': len(names), 'same_cdp_cookie': True, 'native_observed_mcp': 'MCP-live', 'native_observed_cli': 'CLI-live', 'external_browser_preserved': True, 'steps': results}, indent=2), encoding='utf-8')
        finally:
            provider.close()
            try:
                if attached:
                    cli(['detach'])
            finally:
                page.close()
                ui.close()
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    unittest.main()
