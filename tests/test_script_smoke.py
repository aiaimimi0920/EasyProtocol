import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def read_json_lines(path: Path):
    if not path.exists():
        return []
    lines = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


class ScriptSmokeTests(unittest.TestCase):
    def run_powershell(self, args, env=None):
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        return subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", *args],
            cwd=REPO_ROOT,
            env=merged_env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_deploy_subproject_dispatches_service_base_ghcr(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            capture_path = Path(temp_dir) / "external.jsonl"
            temp_config = Path(temp_dir) / "config.yaml"
            template = (REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8")
            temp_config.write_text(template.replace('owner: ""', 'owner: test-owner'), encoding="utf-8")

            result = self.run_powershell(
                [
                    "-File",
                    str(REPO_ROOT / "scripts" / "deploy-subproject.ps1"),
                    "-Project",
                    "service-base-ghcr",
                    "-ConfigPath",
                    str(temp_config),
                    "-ReleaseTag",
                    "smoke-release",
                    "-SkipRender",
                    "-SkipPull",
                ],
                env={"EASYPROTOCOL_TEST_CAPTURE_EXTERNAL_COMMANDS_PATH": str(capture_path)},
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            records = read_json_lines(capture_path)
            self.assertEqual(len(records), 1)
            record = records[0]
            self.assertTrue(record["FilePath"].lower().endswith("deploy-service-base.ps1"))
            args = record["Arguments"]
            self.assertIn("-FromGhcr", args)
            self.assertIn("-ReleaseTag", args)
            self.assertIn("smoke-release", args)
            self.assertIn("-SkipRender", args)
            self.assertIn("-SkipPull", args)

    def test_deploy_service_base_dispatches_deploy_ghcr_helper(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            capture_path = Path(temp_dir) / "external.jsonl"
            temp_config = Path(temp_dir) / "config.yaml"
            rendered_config = Path(temp_dir) / "service-config.yaml"
            rendered_runtime_env = Path(temp_dir) / "runtime.env"
            rendered_config.write_text("listen: 0.0.0.0:9788\n", encoding="utf-8")
            rendered_runtime_env.write_text("EASY_PROTOCOL_RESET_STORE_ON_BOOT=false\n", encoding="utf-8")

            template = (REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8")
            temp_config.write_text(template.replace('owner: ""', 'owner: test-owner'), encoding="utf-8")

            result = self.run_powershell(
                [
                    "-File",
                    str(REPO_ROOT / "scripts" / "deploy-service-base.ps1"),
                    "-ConfigPath",
                    str(temp_config),
                    "-FromGhcr",
                    "-ReleaseTag",
                    "smoke-release",
                    "-SkipRender",
                    "-ServiceOutput",
                    str(rendered_config),
                    "-ServiceEnvOutput",
                    str(rendered_runtime_env),
                    "-SkipPull",
                ],
                env={"EASYPROTOCOL_TEST_CAPTURE_EXTERNAL_COMMANDS_PATH": str(capture_path)},
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            records = read_json_lines(capture_path)
            self.assertEqual(len(records), 1)
            record = records[0]
            self.assertTrue(record["FilePath"].lower().endswith("deploy-ghcr-easy-protocol-service.ps1"))
            args = record["Arguments"]
            self.assertIn("-Image", args)
            self.assertIn("ghcr.io/test-owner/easy-protocol-service:smoke-release", args)
            self.assertIn("-ConfigPath", args)
            self.assertIn(str(rendered_config), args)
            self.assertIn("-RuntimeEnvPath", args)
            self.assertIn(str(rendered_runtime_env), args)
            self.assertIn("-SkipPull", args)

    def test_external_command_helper_runs_powershell_script_with_named_arguments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            helper_script = Path(temp_dir) / "echo-params.ps1"
            helper_script.write_text(
                "\n".join(
                    [
                        "param(",
                        "    [int]$GatewayHostPort,",
                        "    [string]$ConfigPath",
                        ")",
                        "$payload = @{",
                        "    GatewayHostPort = $GatewayHostPort",
                        "    ConfigPath = $ConfigPath",
                        "}",
                        "$payload | ConvertTo-Json -Compress",
                    ]
                ),
                encoding="utf-8",
            )
            runner_script = Path(temp_dir) / "runner.ps1"
            runner_script.write_text(
                "\n".join(
                    [
                        f". '{(REPO_ROOT / 'scripts' / 'lib' / 'easyprotocol-common.ps1').as_posix()}'",
                        "$externalArgs = @(",
                        "    '-GatewayHostPort', '19788',",
                        "    '-ConfigPath', 'C:\\\\demo\\\\config.yaml'",
                        ")",
                        f"Invoke-EasyProtocolExternalCommand -FilePath '{helper_script.as_posix()}' -Arguments $externalArgs",
                    ]
                ),
                encoding="utf-8",
            )

            result = self.run_powershell(
                [
                    "-File",
                    str(runner_script),
                ]
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            payload = json.loads(result.stdout.strip())
            self.assertEqual(payload["GatewayHostPort"], 19788)
            self.assertEqual(payload["ConfigPath"], r"C:\\demo\\config.yaml")

    def test_python_provider_dockerfile_includes_browser_runtime_dependencies(self):
        dockerfile_path = REPO_ROOT / "deploy" / "providers" / "python" / "Dockerfile"
        content = dockerfile_path.read_text(encoding="utf-8")

        required_tokens = [
            "chromium",
            "chromium-driver",
            "libnspr4",
            "libnss3",
            "libdbus-1-3",
            "CHROMEDRIVER_PATH=/usr/bin/chromedriver",
            "BROWSER_BINARY_PATH=/usr/bin/chromium",
            "USE_UNDETECTED_CHROMEDRIVER=0",
        ]

        for token in required_tokens:
            self.assertIn(token, content)


if __name__ == "__main__":
    unittest.main()
