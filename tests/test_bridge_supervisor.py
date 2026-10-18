"""Process liveness supervision must stay out of the business queue."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from local_bridge.server import Handler, WakeStore

ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = ROOT / "host" / "bridge_supervisor.ps1"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")


def wake(number, lane="lane-00"):
    return {"v": 1, "wake_id": f"wake-supervisor-{number}",
            "project_id": "git-agent-harness", "state": "NEED_AGENT",
            "created_at": "2026-01-01T00:00:00Z", "lane_id": lane}


def request(port, path="/health/live", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data,
                                 headers={"X-GAH-Bridge": "1", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=2) as response:
        return json.load(response)


class BridgeLivenessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = WakeStore(self.root / "bridge", self.root)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.store = self.store
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)
        self.port = self.server.server_address[1]

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def test_probe_does_not_touch_store_or_write_access_log(self):
        class NoStoreAccess:
            def __getattr__(self, name):
                raise AssertionError("Liveness touched store: " + name)
        self.server.store = NoStoreAccess()
        with patch.object(Path, "open", side_effect=AssertionError("probe attempted disk IO")):
            for _ in range(3):
                self.assertEqual(request(self.port), {"ok": True})

    def test_probe_responds_while_both_queue_and_git_locks_are_held(self):
        # Handler runs on a different thread: taking either lock would time out.
        with self.store.lock, self.store.git_lock, patch.object(
                self.store, "_recover_expired", side_effect=AssertionError("unexpected recovery")):
            self.assertEqual(request(self.port), {"ok": True})

    def test_probe_preserves_expired_claim_and_existing_health_contract(self):
        value = wake("expired")
        claim = self.store._path(self.store.claimed, value["wake_id"])
        claim.write_text(json.dumps({"wake": value, "claim": {"client_id": "client-test", "expires_at": 0}}))
        before = claim.read_bytes()
        self.assertEqual(request(self.port), {"ok": True})
        self.assertEqual(claim.read_bytes(), before)
        self.assertFalse(list(self.store.inbox.iterdir()))
        # Explicit status API retains its original behavior.
        status = request(self.port, "/health")
        self.assertEqual(status["service"], "gah-local-wake-bridge")
        self.assertEqual(status["root"], str(self.store.root))
        self.assertEqual(status["pending"], 1)

    def test_queue_operations_work_between_probes(self):
        value = wake("normal")
        self.store.emit(value)
        self.assertEqual(request(self.port), {"ok": True})
        claimed = request(self.port, "/api", {"op": "claim", "client_id": "client-test"})
        self.assertEqual(claimed["wake"]["wake_id"], value["wake_id"])
        self.assertEqual(request(self.port), {"ok": True})
        consumed = request(self.port, "/api", {"op": "consume", "client_id": "client-test",
                                                "message_id": value["wake_id"]})
        self.assertTrue(consumed["ok"])


@unittest.skipUnless(POWERSHELL, "PowerShell is required for supervisor behavior tests")
class BridgeSupervisorLoopTests(unittest.TestCase):
    def test_real_powershell_loop_failure_count_and_exact_child_ownership(self):
        # Run the entire production script with only OS/HTTP/time boundaries mocked.
        # No production test-mode flags, Pester install or real process termination.
        cases = [
            ("healthy", [True, True, True, True], False, 0, []),
            ("one-failure", [False, True, True], False, 0, []),
            ("success-resets-count", [False, False, True, False, False, True], False, 0, []),
            ("three-failures", [False, False, False], False, 1, [3]),
            ("failed-stop-retried", [False, False, False, False], True, 2, [3, 4]),
            ("exited-child", [], False, 0, []),
        ]
        script = r'''
param($Source, $Repo, $Bridge, $Config)
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
[System.Management.Automation.Language.Parser]::ParseFile($Source, [ref]$tokens, [ref]$errors) | Out-Null
if ($errors.Count) { throw ($errors | Out-String) }
$global:c = Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
$global:starts=0; $global:probes=0; $global:kills=@(); $global:waits=@(); $global:delays=@()
function py { 'isolated-python' }
function Start-Process {
    param($FilePath,$ArgumentList,$WindowStyle,[switch]$PassThru,$RedirectStandardOutput,$RedirectStandardError,$ErrorAction)
    $global:starts++
    $global:child=[pscustomobject]@{Id=424242;HasExited=$false;ExitCode=0}
    $global:child | Add-Member ScriptMethod WaitForExit {
        param($ms)
        $global:waits += $ms
        if ($this.HasExited -or $global:probes -ge $global:c.responses.Count) { $this.HasExited=$true; return $true }
        return $false
    }
    $global:child | Add-Member ScriptMethod Kill {
        $global:kills += $global:probes
        if ($global:c.fail_stop_once -and $global:kills.Count -eq 1) { throw 'temporary stop failure' }
        $this.HasExited=$true
    }
    return $global:child
}
function Invoke-RestMethod {
    param($Uri,[switch]$UseBasicParsing,$TimeoutSec,$ErrorAction)
    if ($Uri -ne 'http://127.0.0.1:18888/health/live' -or $TimeoutSec -ne 2) { throw 'invalid probe configuration' }
    $healthy = $global:c.responses[$global:probes]
    $global:probes++
    if (-not $healthy) { throw 'simulated connection failure' }
    return @{ok=$true}
}
function Start-Sleep {
    param($Seconds)
    $global:delays += $Seconds
    if ($Seconds -eq 2) { throw 'TEST_FINISHED' }
}
try { & $Source -TargetRepo $Repo -BridgeRoot $Bridge -Port 18888 }
catch { if ($_.Exception.Message -notmatch 'TEST_FINISHED') { throw } }
@{starts=$global:starts;probes=$global:probes;kills=@($global:kills);waits=@($global:waits);delays=@($global:delays)} | ConvertTo-Json -Compress
'''
        for name, responses, fail_stop, kill_count, kill_at in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                (root / "repo" / "local_bridge").mkdir(parents=True)
                (root / "repo" / "local_bridge" / "server.py").write_text("# mock")
                config = root / "case.json"
                config.write_text(json.dumps({"responses": responses, "fail_stop_once": fail_stop}))
                driver = root / "test.ps1"
                driver.write_text(script)
                result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(driver),
                                         str(SUPERVISOR), str(root / "repo"), str(root / "bridge"), str(config)],
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                actual = json.loads(result.stdout.strip().splitlines()[-1])
                self.assertEqual(actual["starts"], 1)
                self.assertEqual(actual["kills"], kill_at)
                self.assertEqual(len(actual["kills"]), kill_count)
                self.assertTrue(all(wait == 10000 for wait in actual["waits"]))
                self.assertEqual(actual["delays"], [2])


@unittest.skipUnless(os.name == "nt" and POWERSHELL and shutil.which("py"), "Windows supervisor integration")
class BridgeSupervisorProcessTests(unittest.TestCase):
    def test_unresponsive_child_restarts_and_preserves_queue_files(self):
        # Disposable replica only: random loopback port, temporary BridgeRoot,
        # fixture server and unrelated sentinel child. Never touch installed CAH.
        with tempfile.TemporaryDirectory(prefix="bridge supervisor ") as td:
            root = Path(td)
            repo = root / "repo"
            (repo / "local_bridge").mkdir(parents=True)
            bridge = root / "bridge"
            store = WakeStore(bridge, repo)
            for number, lane in (("pending", "lane-00"), ("claimed", "lane-01"), ("consumed", "lane-02")):
                store.emit(wake(number, lane))
            store.claim({"client_id": "client-test", "lane_id": "lane-01", "lease_seconds": 120})
            done = store.claim({"client_id": "client-test", "lane_id": "lane-02"})
            store.consume({"client_id": "client-test", "message_id": done["message_id"]})
            before = {str(p.relative_to(bridge)): p.read_bytes() for p in (bridge / "wake").rglob("*.json")}
            fixture = '''import argparse, os, pathlib, sys
sys.path.insert(0, %r)
from http.server import ThreadingHTTPServer
from local_bridge.server import Handler, WakeStore
p=argparse.ArgumentParser()
for k in ("host","port","root","repo-root","git-branch"): p.add_argument("--"+k)
a=p.parse_args()
r=pathlib.Path(a.root)
starts=r/"starts.txt"
first=not starts.exists()
with starts.open("a") as f: f.write(str(os.getpid())+"\\n")
class FixtureHandler(Handler):
    def do_GET(self):
        if first and self.path == "/health/live": self._json(200, {"ok":False})
        else: super().do_GET()
s=ThreadingHTTPServer((a.host,int(a.port)),FixtureHandler)
s.store=WakeStore(r,pathlib.Path(a.repo_root),git_branch=a.git_branch)
s.serve_forever()
''' % str(ROOT)
            (repo / "local_bridge" / "server.py").write_text(fixture)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(100)"])
            supervisor = subprocess.Popen([POWERSHELL, "-NoProfile", "-NonInteractive", "-File", str(SUPERVISOR),
                                           "-TargetRepo", str(repo), "-BridgeRoot", str(bridge), "-Port", str(port)],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 65
                pids = []
                while time.monotonic() < deadline:
                    self.assertIsNone(supervisor.poll(), "Supervisor exited unexpectedly")
                    starts = bridge / "starts.txt"
                    if starts.exists():
                        pids = starts.read_text().splitlines()
                    if len(pids) >= 2:
                        try:
                            if request(port).get("ok") is True:
                                break
                        except (OSError, ValueError):
                            pass
                    time.sleep(0.2)
                else:
                    self.fail("Second responsive Bridge did not start: " + repr(pids))
                self.assertEqual(len(pids), 2)
                self.assertNotEqual(pids[0], pids[1])
                self.assertIsNone(sentinel.poll(), "Unrelated process was terminated")
                after = {str(p.relative_to(bridge)): p.read_bytes() for p in (bridge / "wake").rglob("*.json")}
                self.assertEqual(after, before)
                claim = request(port, "/api", {"op": "claim", "client_id": "client-test", "lane_id": "lane-01"})
                self.assertEqual(claim["wake"]["wake_id"], "wake-supervisor-claimed")
            finally:
                supervisor.terminate()
                supervisor.wait(timeout=10)
                starts = bridge / "starts.txt"
                if starts.exists():
                    # Last PID belongs to this disposable fixture, not a CAH process.
                    try:
                        os.kill(int(starts.read_text().splitlines()[-1]), 15)
                    except (ProcessLookupError, OSError):
                        pass
                sentinel.terminate()
                sentinel.wait(timeout=10)
                time.sleep(0.2)


if __name__ == "__main__":
    unittest.main()
