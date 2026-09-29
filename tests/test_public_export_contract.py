import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PublicExportContractTests(unittest.TestCase):
    def test_required_paths_exist(self):
        manifest = json.loads((ROOT / "harness/public_export_required.json").read_text(encoding="utf-8"))
        missing = [rel for rel in manifest["required_paths"] if not (ROOT / rel).exists()]
        self.assertEqual(missing, [], f"public export core paths missing: {missing}")

    def test_skill_is_explicit_core_capability(self):
        manifest = json.loads((ROOT / "harness/public_export_required.json").read_text(encoding="utf-8"))
        required = set(manifest["required_skill_capabilities"])
        self.assertTrue({
            "candidate_active_deprecated_lifecycle",
            "evidence_gated_promotion",
            "capability_aware_retrieval",
            "post_task_distillation",
            "pass_and_nonpass_history",
            "external_import_contract",
        }.issubset(required))

        contract = (ROOT / "docs/PUBLIC_EXPORT_CONTRACT.md").read_text(encoding="utf-8")
        self.assertIn("Skill Registry as a first-class Agent capability", contract)
        self.assertIn("Removing Skill accumulation", contract)

    def test_installation_surface_is_explicit_core_capability(self):
        manifest = json.loads((ROOT / "harness/public_export_required.json").read_text(encoding="utf-8"))
        required = set(manifest["required_install_capabilities"])
        self.assertTrue({
            "windows_one_click_launcher",
            "playwright_existing_browser_attachment",
            "self_hosted_runner_setup",
            "localhost_bridge_health_check",
            "user_owned_lane_project_onboarding",
            "first_run_topology_verification",
            "maintainer_specific_config_sanitization",
        }.issubset(required))

        install = (ROOT / "installation/README.md").read_text(encoding="utf-8")
        self.assertIn("Start_CAH.bat", install)
        self.assertIn("install_playwright_tools.ps1", install)
        self.assertIn("CAH Task Cell", install)
        self.assertIn("CAH Sandbox0", install)
        self.assertIn("AI-led onboarding", install)
        self.assertIn("Playwright", install)
        self.assertIn("PlaywrightChrome", install)
        self.assertIn("cah-shot", install)

        contract = (ROOT / "docs/PUBLIC_EXPORT_CONTRACT.md").read_text(encoding="utf-8")
        self.assertIn("one-click Windows launcher", contract)
        self.assertIn("Playwright existing-browser attachment", contract)

    def test_export_retains_current_browser_provider_dependencies(self):
        manifest = json.loads((ROOT / "harness/public_export_required.json").read_text(encoding="utf-8"))
        self.assertTrue({
            "installation/README.md", "Start_CAH.ps1", "playwright_host/mcp.py",
            "host/install_playwright_tools.ps1", "host/playwright_cli.ps1",
            "host/playwright-tools/package.json", "host/playwright-tools/package-lock.json",
        }.issubset(manifest["required_paths"]))

    def test_active_repo_map_resolves_to_existing_sources(self):
        routing = json.loads((ROOT / "ai/repo-map.json").read_text(encoding="utf-8"))
        def refs(value):
            if isinstance(value, dict):
                for item in value.values():
                    yield from refs(item)
            elif isinstance(value, list):
                for item in value:
                    yield from refs(item)
            elif isinstance(value, str) and "/" in value and " " not in value:
                yield value
        missing = [ref for ref in refs(routing) if not list(ROOT.glob(ref))]
        self.assertEqual(missing, [])

    def test_lane_maintenance_is_explicit_core_capability(self):
        manifest = json.loads((ROOT / "harness/public_export_required.json").read_text(encoding="utf-8"))
        required = set(manifest["required_runtime_maintenance_capabilities"])
        self.assertTrue({
            "lane_scoped_conversation_reset",
            "preserve_project_and_topology",
            "preserve_canonical_git_state",
            "durable_done_result_with_deleted_and_remaining_counts",
            "fail_closed_project_identity_scope",
        }.issubset(required))

        maintenance = (ROOT / "docs/LIFECYCLE_MAINTENANCE.md").read_text(encoding="utf-8")
        self.assertIn("lane_clear", maintenance)
        self.assertIn("remaining_count = 0", maintenance)
        self.assertIn("Project itself", maintenance)

        contract = (ROOT / "docs/PUBLIC_EXPORT_CONTRACT.md").read_text(encoding="utf-8")
        self.assertIn("lane-scoped conversational reset", contract)
        self.assertIn("Lifecycle maintenance export rule", contract)

    def test_context_economy_is_explicit_core_guidance(self):
        manifest = json.loads((ROOT / "harness/public_export_required.json").read_text(encoding="utf-8"))
        required = set(manifest["required_context_economy_capabilities"])
        self.assertTrue({
            "minimal_hot_path_contracts",
            "routed_focused_docs",
            "selective_skill_retrieval",
            "case_evidence_separation",
            "single_canonical_explanation_with_pointers",
        }.issubset(required))

        guidance = (ROOT / "docs/CONTEXT_ECONOMY.md").read_text(encoding="utf-8")
        self.assertIn("context cost is an architectural cost", guidance)
        self.assertIn("What belongs in AGENTS.md", guidance)
        self.assertIn("When to create a Skill", guidance)

        contract = (ROOT / "docs/PUBLIC_EXPORT_CONTRACT.md").read_text(encoding="utf-8")
        self.assertIn("Context-economy export rule", contract)

    def test_host_capability_runtime_is_explicit_core_capability(self):
        manifest = json.loads((ROOT / "harness/public_export_required.json").read_text(encoding="utf-8"))
        required = set(manifest["required_host_capability_runtime_capabilities"])
        self.assertTrue({
            "machine_local_path_cache",
            "sanitized_git_projection",
            "provider_based_discovery",
            "validated_version_not_directory_name",
            "recoverable_wait_resource_need_host_config",
            "fresh_generation_fence_resume",
        }.issubset(required))

        contract = (ROOT / "docs/PUBLIC_EXPORT_CONTRACT.md").read_text(encoding="utf-8")
        self.assertIn("Host capability export rule", contract)
        self.assertIn("WAIT_RESOURCE / NEED_HOST_CONFIG", contract)

        host_contract = (ROOT / "docs/HOST_CAPABILITIES.md").read_text(encoding="utf-8")
        self.assertIn("Host-local authority", host_contract)
        self.assertIn("Sanitized projection", host_contract)

    def test_staging_omits_personal_skill_contents(self):
        index = json.loads((ROOT / "skills/index.json").read_text(encoding="utf-8"))
        self.assertEqual(index["skills"], [])
        self.assertEqual(index["skill_count"], 0)
        self.assertFalse(list((ROOT / "skills/active").glob("*.json")))



if __name__ == "__main__":
    unittest.main()
