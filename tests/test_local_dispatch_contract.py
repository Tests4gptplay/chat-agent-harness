import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
SHELL = shutil.which("pwsh") or shutil.which("powershell.exe")


def powershell_steps(path):
    """Read literal run blocks without importing a YAML-specific bool resolver."""
    return re.findall(r"(?m)^        run: \|\n((?:          .*\n|\n)+)", path.read_text(encoding="utf-8"))


class LocalDispatchContractTests(unittest.TestCase):
    def test_historical_task_workflows_are_retired(self):
        remaining = [p.name for p in WORKFLOWS.glob("*.yml")
                     if p.name.startswith(("showcase-", "context-a6-"))]
        self.assertEqual(remaining, [], "historical task entry points must not be reactivated")

    def test_control_requests_have_no_github_automatic_consumer(self):
        retired = ("managed-worker-wake.yml", "durable-control-reconcile.yml",
                   "semantic-finalize.yml", "parallel-branch-finalize.yml")
        self.assertEqual([name for name in retired if (WORKFLOWS / name).exists()], [])
        for path in WORKFLOWS.glob("*.yml"):
            with self.subTest(workflow=path.name):
                text = path.read_text(encoding="utf-8").replace("\\", "/")
                for legacy in ("harness/request_drain.py", "requests/worker-wake/",
                               "requests/parallel-branch-finalize/", "results/*.analysis.json"):
                    self.assertNotIn(legacy, text, "local control must have one automatic consumer")

    def test_retired_diagnostic_and_case_carriers_are_absent(self):
        retired = ("blender-case-finalize.yml", "foreground-artifact-export.yml", "review-assets.yml",
                   "host-diagnostic.yml", "host-git-cli.yml", "toolbox-dsh-install-diagnostic.yml",
                   "toolbox-blender-local-script.yml", "toolbox-git-rollback.yml", "toolbox-github-discussion.yml")
        self.assertEqual([name for name in retired if (WORKFLOWS / name).exists()], [])

    def test_active_blender_skill_survives_case_retirement(self):
        from harness.skills import SkillRegistry

        skill = json.loads((ROOT / "skills/active/iterative-blender-visual-refinement-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(skill["status"], "ACTIVE")
        counts = SkillRegistry(ROOT).validate_skill(skill, check_evidence=True)
        self.assertEqual(counts, {"success_count": 1, "failure_count": 0})
        self.assertFalse((ROOT / "cases/blender-retro-camera-001").exists())

    def test_active_image_transfer_skill_uses_general_managed_execution(self):
        skill = json.loads((ROOT / "skills/active/cah-image-native-drive-local-v1.json").read_text(encoding="utf-8"))
        step = next(s for s in skill["procedure"] if s["step_id"] == "normal-runner-download-verify")
        refs = step["implementation_refs"]
        self.assertIn(".github/workflows/managed-host-script.yml", refs)
        self.assertFalse(any("/showcase-" in ref and ref.endswith(".yml") for ref in refs))
        self.assertEqual([ref for ref in refs if not (ROOT / ref).is_file()], [])
        # The reviewed historical source remains available as evidence; routing
        # a new task through Managed does not make its hard-pinned inputs generic.
        self.assertTrue(any(ref.endswith("p4r2e_rev18_normal_runner_drive_pic.py") for ref in refs))

    @unittest.skipUnless(SHELL, "PowerShell is required to exercise idle toolbox behavior")
    def test_default_toolbox_payloads_have_no_external_effects(self):
        for name in ("toolbox-short-local-command.yml", "foreground-direct-command.yml"):
            with self.subTest(workflow=name):
                path = WORKFLOWS / name
                blocks = powershell_steps(path)
                self.assertEqual(len(blocks), 1)
                script = re.sub(r"\$\{\{.*?\}\}", "offline-test", blocks[0])
                # An idle invocation must return before transport, local process,
                # or payload execution. These stubs fail if it crosses that bound.
                guards = "\n".join(
                    f"function {command} {{ throw 'Unexpected idle toolbox effect: {command}' }}"
                    for command in ("git", "gh", "py", "python", "Start-Process", "Stop-Process",
                                    "Invoke-RestMethod", "Invoke-WebRequest", "Get-CimInstance")
                )
                mode = re.search(r'^  DISCUSSION_MODE: "([^"\n]*)"$', path.read_text(encoding="utf-8"), re.M)
                env = dict(os.environ, DISCUSSION_MODE=mode.group(1) if mode else "", CAH_PREFLIGHT_MODE="")
                completed = subprocess.run(
                    [SHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", guards + "\n" + script],
                    capture_output=True, text=True, timeout=20, env=env,
                )
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                self.assertIn("Idle", completed.stdout)

    def _assert_preflight_rejects(self, **overrides):
        blocks = powershell_steps(WORKFLOWS / "foreground-direct-command.yml")
        self.assertEqual(len(blocks), 1)
        script = re.sub(r"\$\{\{.*?\}\}", "offline-test", blocks[0])
        guards = "\n".join(
            f"function {command} {{ throw 'Unexpected preflight effect: {command}' }}"
            for command in ("git", "gh", "py", "Get-Content", "Get-CimInstance", "Invoke-RestMethod",
                            "Invoke-WebRequest", "Get-Item", "Get-FileHash", "New-Item", "Set-Content")
        )
        env = dict(os.environ, CAH_PREFLIGHT_MODE="private_maintenance_deployment",
                   GITHUB_REPOSITORY="__CAH_REPOSITORY__",
                   GITHUB_REF="refs/heads/feat/cah-efficiency-upgrade-20261023", CAH_REPOSITORY_PRIVATE="true")
        env.update(overrides)
        completed = subprocess.run(
            [SHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", guards + "\n" + script],
            capture_output=True, text=True, timeout=20, env=env,
        )
        self.assertNotEqual(completed.returncode, 0)
        output = completed.stdout + completed.stderr
        self.assertIn("PRIVATE_EXACT_INSTANCE_REQUIRED", output)
        self.assertNotIn("Unexpected preflight effect", output)

    @unittest.skipUnless(SHELL, "PowerShell is required to exercise preflight admission")
    def test_preflight_rejects_public_repository_before_external_reads(self):
        self._assert_preflight_rejects(CAH_REPOSITORY_PRIVATE="false")

    @unittest.skipUnless(SHELL, "PowerShell is required to exercise preflight admission")
    def test_preflight_rejects_wrong_ref_before_external_reads(self):
        self._assert_preflight_rejects(GITHUB_REF="refs/heads/main")


if __name__ == "__main__":
    unittest.main()
