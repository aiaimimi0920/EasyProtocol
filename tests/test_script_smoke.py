import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
import yaml


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
                    "-RegisterOutputDirHost",
                    "C:/runtime/register-output",
                    "-RegisterTeamAuthDirHost",
                    "C:/runtime/team-auth",
                    "-RegisterTeamLocalDirHost",
                    "C:/runtime/team-local",
                    "-ProviderReleaseTag",
                    "providers-smoke",
                    "-MailboxServiceApiKey",
                    "mailbox-smoke",
                    "-EasyProxyApiKey",
                    "proxy-smoke",
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
            self.assertIn("-RegisterOutputDirHost", args)
            self.assertIn("C:/runtime/register-output", args)
            self.assertIn("-RegisterTeamAuthDirHost", args)
            self.assertIn("C:/runtime/team-auth", args)
            self.assertIn("-RegisterTeamLocalDirHost", args)
            self.assertIn("C:/runtime/team-local", args)
            self.assertIn("-ProviderReleaseTag", args)
            self.assertIn("providers-smoke", args)
            self.assertIn("-MailboxServiceApiKey", args)
            self.assertIn("mailbox-smoke", args)
            self.assertIn("-EasyProxyApiKey", args)
            self.assertIn("proxy-smoke", args)

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
                    "-MailboxServiceApiKey",
                    "mailbox-smoke",
                    "-EasyProxyApiKey",
                    "proxy-smoke",
                ],
                env={"EASYPROTOCOL_TEST_CAPTURE_EXTERNAL_COMMANDS_PATH": str(capture_path)},
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            records = read_json_lines(capture_path)
            self.assertEqual(len(records), 2)

            patch_record = records[0]
            self.assertEqual(patch_record["FilePath"].lower(), "python")
            patch_args = patch_record["Arguments"]
            self.assertTrue(patch_args[0].lower().endswith("patch-rendered-service-config.py"))
            self.assertIn("--config-path", patch_args)
            self.assertIn(str(rendered_config), patch_args)
            self.assertIn("--runtime-env-path", patch_args)
            self.assertIn(str(rendered_runtime_env), patch_args)
            self.assertIn("--mailbox-service-api-key", patch_args)
            self.assertIn("mailbox-smoke", patch_args)
            self.assertIn("--easy-proxy-api-key", patch_args)
            self.assertIn("proxy-smoke", patch_args)

            record = records[1]
            self.assertTrue(record["FilePath"].lower().endswith("deploy-ghcr-easy-protocol-service.ps1"))
            args = record["Arguments"]
            self.assertIn("-Image", args)
            self.assertIn("ghcr.io/test-owner/easy-protocol-service:smoke-release", args)
            self.assertIn("-ConfigPath", args)
            self.assertIn(str(rendered_config), args)
            self.assertIn("-RuntimeEnvPath", args)
            self.assertIn(str(rendered_runtime_env), args)
            self.assertIn("-SkipPull", args)

    def test_deploy_service_base_pulls_provider_image_for_ghcr_provider_tag(self):
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
                    "service-smoke",
                    "-ProviderReleaseTag",
                    "providers-smoke",
                    "-SkipRender",
                    "-ServiceOutput",
                    str(rendered_config),
                    "-ServiceEnvOutput",
                    str(rendered_runtime_env),
                ],
                env={"EASYPROTOCOL_TEST_CAPTURE_EXTERNAL_COMMANDS_PATH": str(capture_path)},
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            records = read_json_lines(capture_path)
            self.assertGreaterEqual(len(records), 2)
            self.assertEqual("docker", records[0]["FilePath"])
            self.assertEqual(
                ["pull", "ghcr.io/test-owner/easy-protocol-python:providers-smoke"],
                records[0]["Arguments"],
            )
            self.assertTrue(records[-1]["FilePath"].lower().endswith("deploy-ghcr-easy-protocol-service.ps1"))

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

    def test_service_base_publish_workflow_materializes_easybrowser_runtime_before_docker_build(self):
        workflow_path = REPO_ROOT / ".github" / "workflows" / "publish-service-base-ghcr.yml"
        content = workflow_path.read_text(encoding="utf-8")

        required_tokens = [
            "Check Out EasyBrowser Runtime Source",
            "repository: aiaimimi0920/EasyBrowser",
            "path: EasyBrowser",
            "Materialize EasyBrowser Runtime",
            "scripts/materialize-browser-runtime.ps1",
            "-EasyBrowserRepoRoot ./EasyBrowser",
            "-DestinationRoot .",
        ]

        for token in required_tokens:
            self.assertIn(token, content)

        materialize_index = content.index("Materialize EasyBrowser Runtime")
        smoke_build_index = content.index("Build Smoke Image")
        build_push_index = content.index("Build And Push Service Base Image")
        self.assertLess(materialize_index, smoke_build_index)
        self.assertLess(materialize_index, build_push_index)

    def test_materialize_browser_runtime_script_copies_easybrowser_runtime_inputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            easybrowser_root = temp_root / "EasyBrowser"
            runtime_src = easybrowser_root / "runtimes" / "chrome" / "src"
            (runtime_src / "browser_runtime").mkdir(parents=True)
            (runtime_src / "shared_auth").mkdir(parents=True)
            (runtime_src / "shared_mailbox").mkdir(parents=True)
            (runtime_src / "browser_runtime" / "__init__.py").write_text("# runtime\n", encoding="utf-8")
            (runtime_src / "shared_auth" / "__init__.py").write_text("# auth\n", encoding="utf-8")
            (runtime_src / "shared_mailbox" / "cloudflare_temp_email_client.py").write_text("# mailbox\n", encoding="utf-8")
            (easybrowser_root / "runtimes" / "chrome" / "requirements.txt").write_text("selenium\n", encoding="utf-8")

            destination_root = temp_root / "EasyProtocol"
            destination_root.mkdir()

            result = self.run_powershell(
                [
                    "-File",
                    str(REPO_ROOT / "scripts" / "materialize-browser-runtime.ps1"),
                    "-EasyBrowserRepoRoot",
                    str(easybrowser_root),
                    "-DestinationRoot",
                    str(destination_root),
                ]
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            self.assertTrue((destination_root / "python_browser_service" / "src" / "browser_runtime" / "__init__.py").exists())
            self.assertTrue((destination_root / "python_browser_service" / "src" / "shared_auth" / "__init__.py").exists())
            self.assertTrue(
                (
                    destination_root
                    / "python_browser_service"
                    / "src"
                    / "shared_mailbox"
                    / "cloudflare_temp_email_client.py"
                ).exists()
            )
            self.assertEqual(
                (destination_root / "browser_runtime_requirements.txt").read_text(encoding="utf-8"),
                "selenium\n",
            )

    def test_compile_service_base_image_materializes_browser_runtime_before_docker_build(self):
        script_path = REPO_ROOT / "scripts" / "compile-service-base-image.ps1"
        content = script_path.read_text(encoding="utf-8")

        required_tokens = [
            "materialize-browser-runtime.ps1",
            "-DestinationRoot $repoRoot",
            "docker build --platform $Platform",
        ]

        for token in required_tokens:
            self.assertIn(token, content)

        materialize_index = content.index("materialize-browser-runtime.ps1")
        docker_build_index = content.index("docker build --platform $Platform")
        self.assertLess(materialize_index, docker_build_index)

    def test_gitignore_excludes_materialized_browser_runtime_inputs(self):
        content = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

        required_tokens = [
            "python_browser_service/",
            "browser_runtime_requirements.txt",
            "EasyBrowser/",
            "providers/python/python_shared/src/shared_auth/",
            "providers/python/python_shared/src/shared_mailbox/cloudflare_temp_email_client.py",
        ]

        for token in required_tokens:
            self.assertIn(token, content)

    def test_dockerignore_excludes_easybrowser_checkout_but_keeps_materialized_runtime_inputs(self):
        dockerignore_path = REPO_ROOT / ".dockerignore"
        content = dockerignore_path.read_text(encoding="utf-8")

        excluded_tokens = [
            "EasyBrowser/",
            ".repo-cache/",
            "__pycache__/",
            "*.pyc",
        ]
        for token in excluded_tokens:
            self.assertIn(token, content)

        kept_tokens = [
            "python_browser_service/",
            "browser_runtime_requirements.txt",
            "providers/python/python_shared/src/shared_auth/",
            "providers/python/python_shared/src/shared_mailbox/cloudflare_temp_email_client.py",
        ]
        for token in kept_tokens:
            self.assertNotIn(token, content)

    def test_service_base_dockerfile_includes_browser_runtime_dependencies(self):
        dockerfile_path = REPO_ROOT / "deploy" / "service" / "base" / "Dockerfile"
        content = dockerfile_path.read_text(encoding="utf-8")

        required_tokens = [
            "COPY python_browser_service/src /opt/easy-protocol/python_browser_service/src",
            "COPY browser_runtime_requirements.txt /opt/easy-protocol/browser_runtime_requirements.txt",
            "-r /opt/easy-protocol/browser_runtime_requirements.txt",
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

    def test_managed_provider_process_pool_includes_browser_runtime_on_pythonpath(self):
        process_pool_path = REPO_ROOT / "service" / "base" / "services" / "provider_process_pool.go"
        content = process_pool_path.read_text(encoding="utf-8")

        required_tokens = [
            'filepath.Join(cwd, "..", "..", "python_browser_service", "src")',
            '"/opt/easy-protocol/python_browser_service/src"',
        ]

        for token in required_tokens:
            self.assertIn(token, content)

    def test_service_base_dockerfile_includes_import_patch_tooling(self):
        dockerfile_path = REPO_ROOT / "deploy" / "service" / "base" / "Dockerfile"
        content = dockerfile_path.read_text(encoding="utf-8")

        required_tokens = [
            "COPY scripts/patch-rendered-service-config.py /usr/local/bin/patch-rendered-service-config.py",
            "pyyaml",
            "patch-rendered-service-config.py",
        ]

        for token in required_tokens:
            self.assertIn(token, content)

    def test_r2_helper_scripts_suppress_dependency_debug_logging(self):
        script_paths = [
            REPO_ROOT / "deploy" / "service" / "base" / "bootstrap-service-config.py",
            REPO_ROOT / "scripts" / "upload-service-base-r2-config.py",
        ]

        for script_path in script_paths:
            content = script_path.read_text(encoding="utf-8")
            self.assertIn("import logging", content, msg=str(script_path))
            self.assertIn('"botocore"', content, msg=str(script_path))
            self.assertIn('"boto3"', content, msg=str(script_path))
            self.assertIn('"urllib3"', content, msg=str(script_path))
            self.assertIn("setLevel(logging.WARNING)", content, msg=str(script_path))

    def test_service_base_entrypoint_reapplies_local_overrides_after_import_sync(self):
        entrypoint_path = REPO_ROOT / "deploy" / "service" / "base" / "docker-entrypoint.sh"
        content = entrypoint_path.read_text(encoding="utf-8")

        self.assertIn("apply_local_runtime_overrides()", content)
        self.assertIn("--python-provider-image \"${EASY_PROTOCOL_PYTHON_PROVIDER_IMAGE:-}\"", content)
        sync_patch_index = content.index("apply_local_runtime_overrides", content.index("if [ -f \"$SYNC_FLAG_PATH\" ]"))
        restart_index = content.index("remote runtime config updated, restarting service")
        self.assertLess(sync_patch_index, restart_index)

    def test_root_deploy_host_defaults_to_easy_protocol(self):
        content = (REPO_ROOT / "deploy-host.ps1").read_text(encoding="utf-8")
        self.assertIn('[string]$Project = "easy-protocol"', content)

    def test_root_deploy_host_passes_default_owner_for_ghcr_provider_release(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            capture_path = Path(temp_dir) / "external.jsonl"
            temp_config = Path(temp_dir) / "config.yaml"
            temp_config.write_text(
                (REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            result = self.run_powershell(
                [
                    "-File",
                    str(REPO_ROOT / "deploy-host.ps1"),
                    "-Project",
                    "service-base-ghcr",
                    "-ConfigPath",
                    str(temp_config),
                    "-ReleaseTag",
                    "service-base-smoke",
                    "-ProviderReleaseTag",
                    "providers-smoke",
                    "-SkipRender",
                    "-SkipPull",
                ],
                env={"EASYPROTOCOL_TEST_CAPTURE_EXTERNAL_COMMANDS_PATH": str(capture_path)},
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            records = read_json_lines(capture_path)
            self.assertEqual(len(records), 1)
            args = records[0]["Arguments"]
            self.assertIn("-GhcrOwner", args)
            self.assertIn("aiaimimi0920", args)

    def test_render_derived_configs_expands_numbered_provider_hosts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root_config = Path(temp_dir) / "config.yaml"
            temp_service_output = Path(temp_dir) / "service-config.yaml"
            payload = yaml.safe_load((REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
            payload["providers"]["go"]["registry"]["enabled"] = True
            payload["providers"]["javascript"]["registry"]["enabled"] = True
            payload["providers"]["rust"]["registry"]["enabled"] = True
            temp_root_config.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

            result = subprocess.run(
                [
                    "python",
                    str(REPO_ROOT / "scripts" / "render-derived-configs.py"),
                    "--root-config",
                    str(temp_root_config),
                    "--service-output",
                    str(temp_service_output),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=120,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            rendered = yaml.safe_load(temp_service_output.read_text(encoding="utf-8"))
            services = rendered.get("services") or []
            endpoints = {entry.get("name"): entry.get("endpoint") for entry in services if isinstance(entry, dict)}
            self.assertEqual(endpoints.get("PythonProtocol-001"), "http://easy-protocol-python-001:9100")
            self.assertEqual(endpoints.get("GolangProtocol-001"), "http://easy-protocol-go-001:9100")
            self.assertEqual(endpoints.get("JSProtocol-001"), "http://easy-protocol-javascript-001:9100")
            self.assertEqual(endpoints.get("RustProtocol-001"), "http://easy-protocol-rust-001:9100")

    def test_render_derived_configs_can_override_python_provider_mounts_and_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root_config = Path(temp_dir) / "config.yaml"
            temp_service_output = Path(temp_dir) / "service-config.yaml"
            temp_runtime_env = Path(temp_dir) / "runtime.env"
            payload = yaml.safe_load((REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
            temp_root_config.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

            result = self.run_powershell(
                [
                    "-File",
                    str(REPO_ROOT / "scripts" / "render-derived-configs.ps1"),
                    "-ServiceBase",
                    "-ConfigPath",
                    str(temp_root_config),
                    "-ServiceOutput",
                    str(temp_service_output),
                    "-ServiceEnvOutput",
                    str(temp_runtime_env),
                    "-RegisterOutputDirHost",
                    "C:/runtime/register-output",
                    "-RegisterTeamAuthDirHost",
                    "C:/runtime/team-auth",
                    "-RegisterTeamLocalDirHost",
                    "C:/runtime/team-local",
                    "-PythonProviderImage",
                    "ghcr.io/test/easy-protocol-python:providers-smoke",
                ]
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            rendered = yaml.safe_load(temp_service_output.read_text(encoding="utf-8"))
            python_runtime = rendered["managed_provider_runtime"]["providers"]["python"]
            self.assertEqual("ghcr.io/test/easy-protocol-python:providers-smoke", python_runtime["image"])
            mounts = {item["target"]: item for item in python_runtime["host_mounts"]}
            self.assertEqual(
                "/run/desktop/mnt/host/c/runtime/register-output",
                mounts["/shared/register-output"]["source"],
            )
            self.assertFalse(mounts["/shared/register-output"]["read_only"])
            self.assertEqual("/run/desktop/mnt/host/c/runtime/team-auth", mounts["/shared/team-auth"]["source"])
            self.assertTrue(mounts["/shared/team-auth"]["read_only"])
            self.assertEqual("/run/desktop/mnt/host/c/runtime/team-local", mounts["/shared/local-team-store"]["source"])
            self.assertFalse(mounts["/shared/local-team-store"]["read_only"])

    def test_render_derived_configs_preserves_provider_dependency_keys_when_external_dependency_keys_are_blank(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root_config = Path(temp_dir) / "config.yaml"
            temp_service_output = Path(temp_dir) / "service-config.yaml"
            temp_stack_env = Path(temp_dir) / "stack.env"
            payload = yaml.safe_load((REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8"))
            python_env = payload["providers"]["python"]["containerEnvironment"]
            python_env["MAILBOX_SERVICE_API_KEY"] = "mailbox-smoke"
            python_env["EASY_PROXY_API_KEY"] = "proxy-smoke"
            dependencies = payload["stack"]["easyProtocol"]["externalDependencies"]
            dependencies["easyEmail"]["apiKey"] = ""
            dependencies["easyProxy"]["apiKey"] = ""
            temp_root_config.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

            result = subprocess.run(
                [
                    "python",
                    str(REPO_ROOT / "scripts" / "render-derived-configs.py"),
                    "--root-config",
                    str(temp_root_config),
                    "--service-output",
                    str(temp_service_output),
                    "--stack-env-output",
                    str(temp_stack_env),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=120,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            rendered = yaml.safe_load(temp_service_output.read_text(encoding="utf-8"))
            python_runtime = rendered["managed_provider_runtime"]["providers"]["python"]
            self.assertEqual("mailbox-smoke", python_runtime["environment"]["MAILBOX_SERVICE_API_KEY"])
            self.assertEqual("proxy-smoke", python_runtime["environment"]["EASY_PROXY_API_KEY"])
            env_lines = temp_stack_env.read_text(encoding="utf-8").splitlines()
            self.assertIn("MAILBOX_SERVICE_API_KEY=mailbox-smoke", env_lines)
            self.assertIn("EASY_PROXY_API_KEY=proxy-smoke", env_lines)

    def test_patch_rendered_service_config_preserves_imported_config_and_applies_local_overrides(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rendered_config = Path(temp_dir) / "service-config.yaml"
            runtime_env = Path(temp_dir) / "runtime.env"
            rendered_config.write_text(
                yaml.safe_dump(
                    {
                        "listen": "0.0.0.0:9788",
                        "managed_provider_runtime": {
                            "providers": {
                                "python": {
                                    "image": "easy-protocol/easy-protocol-python:local",
                                    "environment": {
                                        "MAILBOX_SERVICE_API_KEY": "old-mailbox-key",
                                        "EASY_PROXY_API_KEY": "old-proxy-key",
                                    },
                                    "host_mounts": [
                                        {
                                            "source": "C:/old/register-output",
                                            "target": "/shared/register-output",
                                            "read_only": False,
                                        }
                                    ],
                                }
                            }
                        },
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            runtime_env.write_text(
                "\n".join(
                    [
                        "MAILBOX_SERVICE_API_KEY=old-mailbox-key",
                        "EASY_PROXY_API_KEY=old-proxy-key",
                        "REGISTER_OUTPUT_DIR_HOST=C:/old/register-output",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    "python",
                    str(REPO_ROOT / "scripts" / "patch-rendered-service-config.py"),
                    "--config-path",
                    str(rendered_config),
                    "--runtime-env-path",
                    str(runtime_env),
                    "--python-provider-image",
                    "ghcr.io/test/easy-protocol-python:providers-smoke",
                    "--register-output-dir-host",
                    "C:/runtime/register-output",
                    "--register-team-auth-dir-host",
                    "C:/runtime/team-auth",
                    "--register-team-local-dir-host",
                    "C:/runtime/team-local",
                    "--mailbox-service-api-key",
                    "new-mailbox-key",
                    "--easy-proxy-api-key",
                    "new-proxy-key",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=120,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            patched = yaml.safe_load(rendered_config.read_text(encoding="utf-8"))
            python_runtime = patched["managed_provider_runtime"]["providers"]["python"]
            self.assertEqual("ghcr.io/test/easy-protocol-python:providers-smoke", python_runtime["image"])
            self.assertEqual("new-mailbox-key", python_runtime["environment"]["MAILBOX_SERVICE_API_KEY"])
            self.assertEqual("new-proxy-key", python_runtime["environment"]["EASY_PROXY_API_KEY"])
            mounts = {item["target"]: item for item in python_runtime["host_mounts"]}
            self.assertEqual(
                "/run/desktop/mnt/host/c/runtime/register-output",
                mounts["/shared/register-output"]["source"],
            )
            self.assertFalse(mounts["/shared/register-output"]["read_only"])
            self.assertEqual("/run/desktop/mnt/host/c/runtime/team-auth", mounts["/shared/team-auth"]["source"])
            self.assertTrue(mounts["/shared/team-auth"]["read_only"])
            self.assertEqual("/run/desktop/mnt/host/c/runtime/team-local", mounts["/shared/local-team-store"]["source"])
            self.assertFalse(mounts["/shared/local-team-store"]["read_only"])
            env_lines = runtime_env.read_text(encoding="utf-8").splitlines()
            self.assertIn("MAILBOX_SERVICE_API_KEY=new-mailbox-key", env_lines)
            self.assertIn("EASY_PROXY_API_KEY=new-proxy-key", env_lines)
            self.assertIn("EASY_PROTOCOL_PYTHON_PROVIDER_IMAGE=ghcr.io/test/easy-protocol-python:providers-smoke", env_lines)
            self.assertIn("REGISTER_OUTPUT_DIR_HOST=/run/desktop/mnt/host/c/runtime/register-output", env_lines)
            self.assertIn("REGISTER_TEAM_AUTH_DIR_HOST=/run/desktop/mnt/host/c/runtime/team-auth", env_lines)
            self.assertIn("REGISTER_TEAM_LOCAL_DIR_HOST=/run/desktop/mnt/host/c/runtime/team-local", env_lines)

    def test_patch_rendered_service_config_writes_docker_daemon_paths_to_runtime_env(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            rendered_config = Path(temp_dir) / "service-config.yaml"
            runtime_env = Path(temp_dir) / "runtime.env"
            rendered_config.write_text(
                yaml.safe_dump(
                    {
                        "listen": "0.0.0.0:9788",
                        "managed_provider_runtime": {"providers": {"python": {"host_mounts": []}}},
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            runtime_env.write_text("EASY_PROTOCOL_RESET_STORE_ON_BOOT=false\n", encoding="utf-8")

            result = subprocess.run(
                [
                    "python",
                    str(REPO_ROOT / "scripts" / "patch-rendered-service-config.py"),
                    "--config-path",
                    str(rendered_config),
                    "--runtime-env-path",
                    str(runtime_env),
                    "--register-output-dir-host",
                    r"C:\runtime\register-output",
                    "--register-team-auth-dir-host",
                    r"C:\runtime\team-auth",
                    "--register-team-local-dir-host",
                    r"C:\runtime\team-local",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=120,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            env_lines = runtime_env.read_text(encoding="utf-8").splitlines()
            self.assertIn(
                "REGISTER_OUTPUT_DIR_HOST=/run/desktop/mnt/host/c/runtime/register-output",
                env_lines,
            )
            self.assertIn("REGISTER_TEAM_AUTH_DIR_HOST=/run/desktop/mnt/host/c/runtime/team-auth", env_lines)
            self.assertIn("REGISTER_TEAM_LOCAL_DIR_HOST=/run/desktop/mnt/host/c/runtime/team-local", env_lines)

    def test_root_deploy_host_patches_bootstrapped_service_config_before_skip_render_deploy(self):
        content = (REPO_ROOT / "deploy-host.ps1").read_text(encoding="utf-8")
        self.assertIn("patch-rendered-service-config.py", content)
        self.assertIn("$shouldPatchBootstrappedServiceConfig", content)

    def test_isolated_instance_deploy_uses_single_easy_protocol_compose_project(self):
        content = (REPO_ROOT / "scripts" / "deploy-isolated-easyprotocol-instance.ps1").read_text(encoding="utf-8")
        self.assertIn("docker compose -p easy-protocol", content)
        self.assertIn('$gatewayContainerName = "easy-protocol"', content)
        self.assertIn(":/shared/register-output", content)

    def test_deploy_subproject_routes_easy_protocol_to_isolated_compose_deploy(self):
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
                    "easy-protocol",
                    "-ConfigPath",
                    str(temp_config),
                    "-InstanceName",
                    "smoke01",
                    "-GatewayHostPort",
                    "29789",
                    "-PythonManagerHostPort",
                    "29103",
                    "-ReleaseTag",
                    "release-20260503-001",
                    "-ProviderReleaseTag",
                    "providers-20260503-001",
                    "-SkipPull",
                ],
                env={"EASYPROTOCOL_TEST_CAPTURE_EXTERNAL_COMMANDS_PATH": str(capture_path)},
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)
            records = read_json_lines(capture_path)
            self.assertEqual(len(records), 1)
            record = records[0]
            self.assertTrue(record["FilePath"].lower().endswith("deploy-isolated-easyprotocol-instance.ps1"))
            args = record["Arguments"]
            self.assertIn("-InstanceName", args)
            self.assertIn("smoke01", args)
            self.assertIn("-NoBuild", args)
            self.assertIn("-ReleaseTag", args)
            self.assertIn("release-20260503-001", args)
            self.assertIn("-ProviderReleaseTag", args)
            self.assertIn("providers-20260503-001", args)
            self.assertIn("-SkipPull", args)


if __name__ == "__main__":
    unittest.main()
