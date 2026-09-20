import json
import tempfile
import unittest
from pathlib import Path

from harness.dispatch import continue_after_result
from harness.single_thread import SingleThreadRuntime


class SingleThreadRuntimeTests(unittest.TestCase):
    def test_success_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "executors").mkdir()
            (root / "cl").mkdir()
            (root / "results").mkdir()
            (root / "actions").mkdir()
            (root / "executors" / "echo.py").write_text(
                "import argparse,json\nfrom pathlib import Path\np=argparse.ArgumentParser();p.add_argument('--action');p.add_argument('--result');a=p.parse_args();act=json.loads(Path(a.action).read_text());Path(a.result).write_text(json.dumps({'v':1,'result_id':'r1','action_id':act['action_id'],'task_id':act['task_id'],'round':1,'status':'PASS','summary':'ok','evidence':[{'type':'echo','value':'ok'}],'artifacts':[]}),encoding='utf-8')\n",
                encoding="utf-8",
            )
            fg = {
                "v":1,"cl_id":"fg","task_id":"t1","scope":"foreground_supervision","overall":"RUNNING",
                "created_at":"x","updated_at":"x","conditions":[
                    {"id":"harness_claimed","label":"h","state":"WAIT"},
                    {"id":"backend_execution","label":"b","state":"WAIT"},
                    {"id":"durable_result","label":"r","state":"WAIT"},
                    {"id":"verification","label":"v","state":"WAIT"},
                    {"id":"final_acceptance","label":"f","state":"WAIT"},
                ],
                "supervisor_guard":{"state":"HELD","detail":None},
            }
            bg = {
                "v":1,"cl_id":"bg","task_id":"t1","scope":"backend_execution","overall":"READY",
                "created_at":"x","updated_at":"x","conditions":[
                    {"id":"claimed","label":"c","state":"WAIT"},
                    {"id":"executor","label":"e","state":"WAIT"},
                    {"id":"durable_result","label":"r","state":"WAIT"},
                    {"id":"verification","label":"v","state":"WAIT"},
                    {"id":"terminal","label":"t","state":"WAIT"},
                ],
            }
            action = {
                "v":1,"action_id":"a1","task_id":"t1","round":1,"executor":"echo","operation":"echo",
                "payload":{"message":"x"},"expected_evidence":["echo"],"foreground_cl":"cl/fg.json",
                "backend_cl":"cl/bg.json","timeout_seconds":30
            }
            (root/"cl/fg.json").write_text(json.dumps(fg),encoding="utf-8")
            (root/"cl/bg.json").write_text(json.dumps(bg),encoding="utf-8")
            (root/"actions/a.json").write_text(json.dumps(action),encoding="utf-8")
            rt = SingleThreadRuntime(root)
            ap = root/"actions/a.json"
            rp = root/"results/t1.json"
            rt.claim(ap)
            rt.execute(ap,rp)
            rt.verify(ap,rp)
            got_fg = json.loads((root/"cl/fg.json").read_text(encoding="utf-8"))
            got_bg = json.loads((root/"cl/bg.json").read_text(encoding="utf-8"))
            self.assertEqual(got_bg["overall"], "GREEN")
            self.assertEqual(got_fg["overall"], "GREEN")
            self.assertEqual(got_fg["supervisor_guard"]["state"], "HELD")
            self.assertEqual(next(x for x in got_fg["conditions"] if x["id"]=="final_acceptance")["state"], "GREEN")


    def test_worker_continuation_is_not_task_terminal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "executors").mkdir()
            (root / "cl").mkdir()
            (root / "results").mkdir()
            (root / "actions").mkdir()
            (root / "executors" / "echo.py").write_text(
                "import argparse,json\nfrom pathlib import Path\np=argparse.ArgumentParser();p.add_argument('--action');p.add_argument('--result');a=p.parse_args();act=json.loads(Path(a.action).read_text());Path(a.result).write_text(json.dumps({'v':1,'result_id':'r-cont','action_id':act['action_id'],'task_id':act['task_id'],'round':act['round'],'status':'PASS','summary':'ok','evidence':[{'type':'echo','value':'ok'}],'artifacts':[]}),encoding='utf-8')\n",
                encoding="utf-8",
            )
            fg = {
                "v":1,"cl_id":"fg","task_id":"tc","scope":"foreground_supervision","overall":"RUNNING",
                "created_at":"x","updated_at":"x","conditions":[
                    {"id":"harness_claimed","label":"h","state":"WAIT"},
                    {"id":"backend_execution","label":"b","state":"WAIT"},
                    {"id":"durable_result","label":"r","state":"WAIT"},
                    {"id":"verification","label":"v","state":"WAIT"},
                    {"id":"final_acceptance","label":"f","state":"WAIT"},
                ],
                "supervisor_guard":{"state":"HELD","detail":None},
            }
            bg = {
                "v":1,"cl_id":"bg","task_id":"tc","scope":"backend_execution","overall":"RUNNING",
                "created_at":"x","updated_at":"x",
                "dispatch":{
                    "dispatch_id":"dispatch-old-0001",
                    "wake_id":"wake-old-0001",
                    "generation":1,
                    "fence_token":"fence-old-0001",
                    "state":"WAIT_RESULT",
                    "requested_at":"x",
                    "acked_at":"x",
                    "acked_by_worker_ref":"pool-worker-1",
                    "wait_ref":"a-cont"
                },
                "conditions":[
                    {"id":"claimed","label":"c","state":"WAIT"},
                    {"id":"executor","label":"e","state":"WAIT"},
                    {"id":"durable_result","label":"r","state":"WAIT"},
                    {"id":"verification","label":"v","state":"WAIT"},
                    {"id":"terminal","label":"t","state":"WAIT"},
                ],
            }
            action = {
                "v":1,"action_id":"a-cont","task_id":"tc","round":1,"executor":"echo","operation":"echo",
                "payload":{},"expected_evidence":["echo"],"foreground_cl":"cl/fg.json",
                "backend_cl":"cl/bg.json","timeout_seconds":30,"worker_continuation":True
            }
            (root/"cl/fg.json").write_text(json.dumps(fg),encoding="utf-8")
            (root/"cl/bg.json").write_text(json.dumps(bg),encoding="utf-8")
            (root/"actions/a.json").write_text(json.dumps(action),encoding="utf-8")
            rt = SingleThreadRuntime(root)
            ap = root/"actions/a.json"
            rp = root/"results/tc.json"
            rt.claim(ap)
            rt.execute(ap,rp)
            rt.verify(ap,rp)

            after_verify = json.loads((root/"cl/bg.json").read_text(encoding="utf-8"))
            self.assertEqual(after_verify["overall"], "RUNNING")
            self.assertEqual(next(x for x in after_verify["conditions"] if x["id"]=="terminal")["state"], "WAIT")

            next_dispatch = continue_after_result(root/"cl/bg.json", ap, rp, root=root)
            self.assertTrue(next_dispatch["scheduled"])
            after_continue = json.loads((root/"cl/bg.json").read_text(encoding="utf-8"))
            self.assertEqual(after_continue["overall"], "READY")
            self.assertEqual(after_continue["dispatch"]["state"], "READY")
            self.assertEqual(after_continue["dispatch"]["generation"], 2)
            self.assertNotEqual(after_continue["dispatch"]["dispatch_id"], "dispatch-old-0001")
            self.assertEqual(after_continue["dispatch"]["continuation_ref"], "results/tc.json")


    def test_worker_continuation_can_seed_first_dispatch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "cl").mkdir()
            (root / "actions").mkdir()
            (root / "results").mkdir()
            bg = {
                "v":1,
                "cl_id":"bg-seed",
                "task_id":"t-seed",
                "scope":"backend_execution",
                "overall":"RUNNING",
                "created_at":"x",
                "updated_at":"x",
                "conditions":[],
            }
            action = {
                "v":1,
                "action_id":"a-seed",
                "task_id":"t-seed",
                "round":1,
                "executor":"echo",
                "operation":"echo",
                "payload":{},
                "expected_evidence":["echo"],
                "backend_cl":"cl/bg.json",
                "worker_continuation":True,
            }
            result = {
                "v":1,
                "result_id":"r-seed",
                "action_id":"a-seed",
                "task_id":"t-seed",
                "round":1,
                "status":"PASS",
                "summary":"ok",
                "evidence":[{"type":"echo","value":"ok"}],
                "artifacts":[],
            }
            (root/"cl/bg.json").write_text(json.dumps(bg),encoding="utf-8")
            (root/"actions/a.json").write_text(json.dumps(action),encoding="utf-8")
            (root/"results/t-seed.json").write_text(json.dumps(result),encoding="utf-8")

            scheduled = continue_after_result(root/"cl/bg.json", root/"actions/a.json", root/"results/t-seed.json", root=root)
            self.assertTrue(scheduled["scheduled"])
            self.assertTrue(scheduled["seeded_from_executor"])
            self.assertEqual(scheduled["dispatch_generation"], 1)
            got = json.loads((root/"cl/bg.json").read_text(encoding="utf-8"))
            self.assertEqual(got["overall"], "READY")
            self.assertEqual(got["dispatch"]["state"], "READY")
            self.assertEqual(got["dispatch"]["generation"], 1)
            self.assertEqual(got["dispatch"]["continuation_ref"], "results/t-seed.json")


    def test_missing_expected_evidence_fails_pass_verification(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "cl").mkdir()
            (root / "actions").mkdir()
            (root / "results").mkdir()
            subprocess = __import__("subprocess")
            subprocess.check_call(["git","init",str(root)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git","-C",str(root),"config","user.name","test"])
            subprocess.check_call(["git","-C",str(root),"config","user.email","test@example.invalid"])
            fg = {
                "v":1,"cl_id":"fg","task_id":"te","scope":"foreground_supervision","overall":"RUNNING",
                "created_at":"x","updated_at":"x","conditions":[
                    {"id":"harness_claimed","label":"h","state":"GREEN"},
                    {"id":"backend_execution","label":"b","state":"GREEN"},
                    {"id":"durable_result","label":"r","state":"GREEN"},
                    {"id":"verification","label":"v","state":"RUNNING"},
                    {"id":"final_acceptance","label":"f","state":"WAIT"},
                ],"supervisor_guard":{"state":"HELD","detail":None},
            }
            bg = {
                "v":1,"cl_id":"bg","task_id":"te","scope":"backend_execution","overall":"RUNNING",
                "created_at":"x","updated_at":"x","conditions":[
                    {"id":"claimed","label":"c","state":"GREEN"},
                    {"id":"executor","label":"e","state":"GREEN"},
                    {"id":"durable_result","label":"r","state":"GREEN"},
                    {"id":"verification","label":"v","state":"RUNNING"},
                    {"id":"terminal","label":"t","state":"WAIT"},
                ],
            }
            action = {
                "v":1,"action_id":"ae","task_id":"te","round":1,"executor":"echo","operation":"echo",
                "payload":{},"expected_evidence":["must_have"],"foreground_cl":"cl/fg.json","backend_cl":"cl/bg.json"
            }
            result = {
                "v":1,"result_id":"re","action_id":"ae","task_id":"te","round":1,"status":"PASS",
                "summary":"claims pass","evidence":[{"type":"other","value":"x"}],"artifacts":[]
            }
            (root/"cl/fg.json").write_text(json.dumps(fg),encoding="utf-8")
            (root/"cl/bg.json").write_text(json.dumps(bg),encoding="utf-8")
            (root/"actions/a.json").write_text(json.dumps(action),encoding="utf-8")
            (root/"results/te.json").write_text(json.dumps(result),encoding="utf-8")
            subprocess.check_call(["git","-C",str(root),"add","cl","actions","results"])
            subprocess.check_call(["git","-C",str(root),"commit","-m","seed"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            rt = SingleThreadRuntime(root)
            rt.verify(root/"actions/a.json", root/"results/te.json")
            got = json.loads((root/"cl/bg.json").read_text(encoding="utf-8"))
            self.assertEqual(got["overall"], "ERROR")
            self.assertEqual(got["error"]["kind"], "expected_evidence_missing")
            self.assertIn("must_have", got["error"]["summary"])

    def test_missing_artifact_fails_verification(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "executors").mkdir()
            (root / "cl").mkdir()
            (root / "results").mkdir()
            (root / "actions").mkdir()
            subprocess = __import__("subprocess")
            subprocess.check_call(["git","init",str(root)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.check_call(["git","-C",str(root),"config","user.name","test"])
            subprocess.check_call(["git","-C",str(root),"config","user.email","test@example.invalid"])
            fg = {
                "v":1,"cl_id":"fg","task_id":"t2","scope":"foreground_supervision","overall":"RUNNING",
                "created_at":"x","updated_at":"x","conditions":[
                    {"id":"harness_claimed","label":"h","state":"GREEN"},
                    {"id":"backend_execution","label":"b","state":"GREEN"},
                    {"id":"durable_result","label":"r","state":"GREEN"},
                    {"id":"verification","label":"v","state":"RUNNING"},
                    {"id":"final_acceptance","label":"f","state":"WAIT"},
                ],"supervisor_guard":{"state":"HELD","detail":None},
            }
            bg = {
                "v":1,"cl_id":"bg","task_id":"t2","scope":"backend_execution","overall":"RUNNING",
                "created_at":"x","updated_at":"x","conditions":[
                    {"id":"claimed","label":"c","state":"GREEN"},
                    {"id":"executor","label":"e","state":"GREEN"},
                    {"id":"durable_result","label":"r","state":"GREEN"},
                    {"id":"verification","label":"v","state":"RUNNING"},
                    {"id":"terminal","label":"t","state":"WAIT"},
                ],
            }
            action = {
                "v":1,"action_id":"a2","task_id":"t2","round":1,"executor":"echo","operation":"echo",
                "payload":{},"expected_evidence":[],"foreground_cl":"cl/fg.json","backend_cl":"cl/bg.json"
            }
            result = {
                "v":1,"result_id":"r2","action_id":"a2","task_id":"t2","round":1,"status":"PASS",
                "summary":"claims an artifact","evidence":[{"type":"x","value":"y"}],
                "artifacts":["evidence/missing.json"]
            }
            (root/"cl/fg.json").write_text(json.dumps(fg),encoding="utf-8")
            (root/"cl/bg.json").write_text(json.dumps(bg),encoding="utf-8")
            (root/"actions/a.json").write_text(json.dumps(action),encoding="utf-8")
            (root/"results/t2.json").write_text(json.dumps(result),encoding="utf-8")
            subprocess.check_call(["git","-C",str(root),"add","cl","actions","results"])
            subprocess.check_call(["git","-C",str(root),"commit","-m","seed"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            rt = SingleThreadRuntime(root)
            rt.verify(root/"actions/a.json", root/"results/t2.json")
            got_fg = json.loads((root/"cl/fg.json").read_text(encoding="utf-8"))
            self.assertEqual(got_fg["overall"], "ERROR")
            self.assertEqual(got_fg["error"]["kind"], "artifact_not_durable")


    def test_semantic_branch_nested_executor_preserves_lane_barrier(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "executors").mkdir()
            (root / "cl").mkdir()
            (root / "results").mkdir()
            (root / "actions").mkdir()
            (root / "executors" / "echo.py").write_text(
                "import argparse,json\nfrom pathlib import Path\np=argparse.ArgumentParser();p.add_argument('--action');p.add_argument('--result');a=p.parse_args();act=json.loads(Path(a.action).read_text());Path(a.result).write_text(json.dumps({'v':1,'result_id':'r2','action_id':act['action_id'],'task_id':act['task_id'],'round':1,'status':'PASS','summary':'nested ok','evidence':[{'type':'echo','value':'ok'}],'artifacts':[]}),encoding='utf-8')\n",
                encoding="utf-8",
            )
            fg = {
                "v": 1,
                "cl_id": "fg-parent",
                "task_id": "parent",
                "scope": "foreground_supervision",
                "overall": "RUNNING",
                "result_ref": "cases/parent/final_result.json",
                "conditions": [
                    {"id": "lane00", "label": "lane-00", "state": "RUNNING"},
                    {"id": "lane01", "label": "lane-01", "state": "RUNNING"},
                    {"id": "barrier", "label": "barrier", "state": "WAIT"},
                    {"id": "final_acceptance", "label": "final", "state": "WAIT"},
                ],
            }
            bg = {
                "v": 1,
                "cl_id": "bg-child",
                "task_id": "parent-lane00",
                "scope": "backend_execution",
                "overall": "RUNNING",
                "conditions": [
                    {"id": "claimed", "label": "claimed", "state": "GREEN"},
                    {"id": "semantic_work", "label": "semantic", "state": "RUNNING"},
                    {"id": "deterministic_execution", "label": "det", "state": "WAIT"},
                    {"id": "branch_output", "label": "branch", "state": "WAIT"},
                    {"id": "barrier_acceptance", "label": "barrier", "state": "WAIT"},
                ],
            }
            action = {
                "v": 1,
                "action_id": "nested-a1",
                "task_id": "parent-lane00",
                "round": 1,
                "executor": "echo",
                "operation": "echo",
                "payload": {"message": "x", "parent_task_id": "parent"},
                "expected_evidence": ["echo"],
                "foreground_cl": "cl/fg.json",
                "backend_cl": "cl/bg.json",
                "timeout_seconds": 30,
                "lane_id": "lane-00",
                "worker_project_key": "g-p-test",
                "worker_continuation": True,
            }
            (root / "cl/fg.json").write_text(json.dumps(fg), encoding="utf-8")
            (root / "cl/bg.json").write_text(json.dumps(bg), encoding="utf-8")
            (root / "actions/a.json").write_text(json.dumps(action), encoding="utf-8")
            rt = SingleThreadRuntime(root)
            ap = root / "actions/a.json"
            rp = root / "results/parent-lane00.json"

            rt.claim(ap)
            rt.execute(ap, rp)
            out = rt.verify(ap, rp)

            got_fg = json.loads((root / "cl/fg.json").read_text(encoding="utf-8"))
            got_bg = json.loads((root / "cl/bg.json").read_text(encoding="utf-8"))
            self.assertTrue(out["semantic_branch"])
            self.assertEqual(got_bg["overall"], "RUNNING")
            self.assertEqual(next(x for x in got_bg["conditions"] if x["id"] == "deterministic_execution")["state"], "GREEN")
            self.assertEqual(next(x for x in got_bg["conditions"] if x["id"] == "semantic_work")["state"], "RUNNING")
            self.assertEqual(next(x for x in got_bg["conditions"] if x["id"] == "branch_output")["state"], "WAIT")
            self.assertEqual(next(x for x in got_bg["conditions"] if x["id"] == "barrier_acceptance")["state"], "WAIT")
            self.assertEqual(next(x for x in got_fg["conditions"] if x["id"] == "lane00")["state"], "RUNNING")
            self.assertEqual(next(x for x in got_fg["conditions"] if x["id"] == "barrier")["state"], "WAIT")
            self.assertEqual(got_fg["result_ref"], "cases/parent/final_result.json")


if __name__ == "__main__":
    unittest.main()
