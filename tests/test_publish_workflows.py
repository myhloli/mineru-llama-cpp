"""验证发布输入不会执行 shell 命令，以及 OIDC 凭据仅授予独立发布任务。"""
import fnmatch
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def load_workflow(filename):
    """读取真实工作流，使回归测试执行发布配置中的筛选脚本。"""
    return yaml.safe_load((WORKFLOWS / filename).read_text(encoding="utf-8"))


@pytest.mark.parametrize("pattern", [
    "*cp310-abi3-*",
    "*win_amd64.whl",
    "*",
    "",
    "$(touch injection-executed)*cp310-abi3-*",
    "`touch injection-executed`*cp310-abi3-*",
    '*" -delete; touch injection-executed; #',
    '*" -delete\ntouch injection-executed\n#',
])
@pytest.mark.parametrize("mode", ["workflow_dispatch", "workflow_run"])
@pytest.mark.skipif(os.name == "nt", reason="Linux 发布脚本需要 POSIX Bash 和 find")
def test_wheel_filter_treats_input_as_data(tmp_path, pattern, mode):
    """执行真实筛选步骤，验证手动输入不会执行代码且自动发布固定选择全部 abi3 wheel。"""
    workflow = load_workflow("publish-wheels.yml")
    step = next(step for step in workflow["jobs"]["prepare_wheels"]["steps"]
                if step.get("name") == "Keep only wheels matching the pattern")
    names = [
        "mineru_llama_cpp-0.1.2-cp310-abi3-win_amd64.whl",
        "mineru_llama_cpp-0.1.2-cp310-abi3-macosx_14_0_arm64.whl",
        "other-0.1.0-cp312-cp312-macosx_14_0_arm64.whl",
    ]
    dist = tmp_path / "dist"
    dist.mkdir()
    for name in names:
        (dist / name).write_bytes(b"temporary wheel fixture")
    environment = os.environ.copy()
    for key, value in step.get("env", {}).items():
        environment[key] = value.replace("${{ inputs.wheel_glob }}", pattern).replace(
            "${{ github.event_name }}", mode)
    # 模拟 GitHub 先替换表达式再交给 Bash，确保直接插值的旧代码会触发回归。
    script = step["run"].replace("${{ inputs.wheel_glob }}", pattern)
    subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script], cwd=tmp_path,
                   env=environment, check=True, capture_output=True, text=True)
    assert not (tmp_path / "injection-executed").exists()
    effective_pattern = "*cp310-abi3-*" if mode == "workflow_run" else pattern
    assert sorted(path.name for path in dist.iterdir()) == sorted(
        name for name in names if fnmatch.fnmatchcase(name, effective_pattern))


@pytest.mark.parametrize("mode", ["workflow_dispatch", "workflow_run"])
@pytest.mark.skipif(os.name == "nt", reason="Linux 发布脚本需要 POSIX Bash")
def test_automatic_verification_requires_all_platforms(tmp_path, mode):
    """用记录参数的工具替身执行校验脚本，确认仅手动发布允许平台子集。"""
    step = next(step for step in load_workflow("publish-wheels.yml")["jobs"]["prepare_wheels"]["steps"]
                if step.get("name") == "Verify selected stable-ABI wheels")
    commands = tmp_path / "commands"
    commands.mkdir()
    calls = tmp_path / "python-calls"
    for name in ("pip", "python", "abi3audit"):
        executable = commands / name
        script = '#!/bin/sh\nexit 0\n'
        if name == "python":
            script = '#!/bin/sh\nprintf "%s\\n" "$*" >> "$VERIFY_CALLS"\n'
        executable.write_text(script)
        executable.chmod(0o755)
    environment = os.environ.copy()
    environment.update(PATH=str(commands) + os.pathsep + environment["PATH"],
                       PUBLISH_MODE=mode, VERIFY_CALLS=str(calls))
    subprocess.run(["bash", "-e", "-o", "pipefail", "-c", step["run"]], cwd=tmp_path,
                   env=environment, check=True, capture_output=True, text=True)
    verification_calls = [line for line in calls.read_text().splitlines()
                          if line.startswith("tools/verify_wheels.py ")]
    expected = "tools/verify_wheels.py dist"
    if mode == "workflow_dispatch":
        expected += " --allow-subset"
    assert verification_calls == [expected]


def test_manual_publish_preserves_and_transfers_prepared_wheels():
    """源码清理先于下载，发布任务只获取当前运行中校验后上传的 wheel 集合。"""
    jobs = load_workflow("publish-wheels.yml")["jobs"]
    steps = jobs["prepare_wheels"]["steps"]
    checkout = next(index for index, step in enumerate(steps)
                    if step.get("uses", "").startswith("actions/checkout@"))
    download = next(index for index, step in enumerate(steps)
                    if step.get("uses", "").startswith("actions/download-artifact@"))
    verify = next(index for index, step in enumerate(steps)
                  if step.get("name") == "Verify selected stable-ABI wheels")
    upload = next(index for index, step in enumerate(steps)
                  if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert checkout < download < verify < upload
    artifact = steps[upload]["with"]
    assert artifact["path"] == "dist/*.whl"
    assert artifact["if-no-files-found"] == "error"
    publishing_download = jobs["publish"]["steps"][0]["with"]
    assert publishing_download["name"] == artifact["name"]
    assert publishing_download["path"] == "dist"
    assert "run-id" not in publishing_download


def test_publishing_uses_one_isolated_oidc_workflow():
    """唯一发布入口使用 OIDC，构建工作流没有发布步骤或获取发布身份的权限。"""
    build = load_workflow("build-wheels.yml")
    assert build.get("permissions", {}).get("id-token") != "write"
    for job in build["jobs"].values():
        assert job.get("permissions", {}).get("id-token") != "write"
        assert not any(step.get("uses", "").startswith("pypa/gh-action-pypi-publish@")
                       for step in job["steps"])
    workflow = load_workflow("publish-wheels.yml")
    assert workflow.get("permissions", {}).get("id-token") != "write"
    jobs = workflow["jobs"]
    publishing_jobs = [name for name, job in jobs.items()
                       if any(step.get("uses", "").startswith("pypa/gh-action-pypi-publish@")
                              for step in job["steps"])]
    assert publishing_jobs == ["publish"]
    job = jobs["publish"]
    assert job["if"] == "github.repository == 'opendatalab/mineru-llama-cpp'"
    assert job["permissions"]["id-token"] == "write"
    assert job["environment"]["name"] == "pypi"
    assert "prepare_wheels" in job["needs"]
    for name, other in jobs.items():
        if name != "publish":
            assert other.get("permissions", {}).get("id-token") != "write"
    # 发布任务只下载已校验产物并上传，不执行构建、安装依赖或自定义脚本。
    for step in job["steps"]:
        assert "run" not in step
        assert step["uses"].startswith(("actions/download-artifact@", "pypa/gh-action-pypi-publish@"))
        if step["uses"].startswith("pypa/gh-action-pypi-publish@"):
            assert not {"user", "password"}.intersection(step.get("with", {}))


def test_automatic_publish_requires_successful_upstream_tag_build():
    """自动入口监听构建完成，并保留成功状态、上游仓库与版本标签三项限制。"""
    workflow = load_workflow("publish-wheels.yml")
    triggers = workflow.get("on", workflow.get(True))  # PyYAML 的 YAML 1.1 将 on 解析为布尔值。
    assert triggers["workflow_run"] == {"workflows": ["Build wheels"], "types": ["completed"]}
    assert "workflow_dispatch" in triggers
    prepare = workflow["jobs"]["prepare_wheels"]
    for required in (
        "github.repository == 'opendatalab/mineru-llama-cpp'",
        "github.event.workflow_run.conclusion == 'success'",
        "github.event.workflow_run.event == 'push'",
        "github.event.workflow_run.head_repository.full_name == github.repository",
        "startsWith(github.event.workflow_run.head_branch, 'v')",
    ):
        assert required in prepare["if"]
    steps = prepare["steps"]
    check = next(index for index, step in enumerate(steps) if step.get("id") == "source")
    download = next(index for index, step in enumerate(steps)
                    if step.get("uses", "").startswith("actions/download-artifact@"))
    assert check < download
    assert steps[download]["with"]["run-id"] == "${{ steps.source.outputs.run_id }}"


@pytest.fixture
def source_run():
    """提供成功的同仓库标签构建元数据，供自动及手动入口验证使用。"""
    return {"id": 123, "path": ".github/workflows/build-wheels.yml",
            "repository": {"full_name": "opendatalab/mineru-llama-cpp"},
            "head_repository": {"full_name": "opendatalab/mineru-llama-cpp"},
            "status": "completed", "conclusion": "success", "event": "push", "head_branch": "v0.2.0"}


def run_source_check(source_run, event_name="workflow_dispatch", run_id="123", trigger_id=123):
    """用真实 Node 执行生产验证脚本，模拟只读 GitHub API，避免访问网络或触发发布。"""
    node = shutil.which("node")
    if node is None:
        pytest.skip("GitHub Actions 脚本验证需要 Node.js")
    step = next(step for step in load_workflow("publish-wheels.yml")["jobs"]["prepare_wheels"]["steps"]
                if step.get("id") == "source")
    payload = {"script": step["with"]["script"], "run": source_run, "run_id": run_id,
               "context": {"repo": {"owner": "opendatalab", "repo": "mineru-llama-cpp"},
                           "eventName": event_name, "payload": {"workflow_run": {"id": trigger_id}}}}
    runner = r"""
        const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
        process.env.SOURCE_RUN_ID = input.run_id;
        const outputs = {};
        // 模拟 Actions 输出收集方法，供断言确认已验证的 run ID。
        const core = { setOutput: (name, value) => { outputs[name] = value; } };
        // 模拟只读 API，并检查脚本确实按当前仓库和用户指定的 run 查询。
        const github = { rest: { actions: { getWorkflowRun: async (request) => {
            if (request.run_id !== input.run.id || request.owner !== input.context.repo.owner ||
                request.repo !== input.context.repo.repo) throw new Error('Unexpected API query');
            return { data: input.run };
        } } } };
        // 执行真实生产脚本，把成功输出或拒绝原因交回 Python 测试。
        const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
        new AsyncFunction('github', 'context', 'core', input.script)(github, input.context, core)
            .then(() => process.stdout.write(JSON.stringify(outputs)))
            .catch(error => { console.error(error.message); process.exitCode = 1; });
    """
    return subprocess.run([node, "-e", runner], input=json.dumps(payload), capture_output=True, text=True)


@pytest.mark.parametrize("event_name, source_event, branch", [
    ("workflow_run", "push", "v0.2.0"),
    ("workflow_dispatch", "push", "main"),
    ("workflow_dispatch", "workflow_dispatch", "candidate"),
])
def test_source_run_accepts_successful_builds(source_run, event_name, source_event, branch):
    """自动发布接受成功标签构建，手动发布可复用成功的普通或手动构建。"""
    source_run.update(event=source_event, head_branch=branch)
    result = run_source_check(source_run, event_name)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"run_id": "123"}


@pytest.mark.parametrize("changes", [
    {"status": "in_progress"},
    {"conclusion": "failure"},
    {"conclusion": "cancelled"},
    {"path": ".github/workflows/other.yml"},
    {"repository": {"full_name": "myhloli/mineru-llama-cpp"}},
    {"head_repository": {"full_name": "myhloli/mineru-llama-cpp"}},
    {"event": "pull_request"},
])
def test_source_run_rejects_failed_foreign_or_untrusted_builds(source_run, changes):
    """阻止失败、未完成、其他工作流、跨仓库和 PR 产物进入发布流程。"""
    source_run.update(changes)
    result = run_source_check(source_run)
    assert result.returncode != 0
    assert "Source must be a successful build-wheels run" in result.stderr


@pytest.mark.parametrize("branch, trigger_id", [("main", 123), ("v0.2.0", 456)])
def test_automatic_source_requires_triggering_tag_run(source_run, branch, trigger_id):
    """自动发布拒绝普通分支以及与 workflow_run 事件不一致的构建 ID。"""
    source_run["head_branch"] = branch
    result = run_source_check(source_run, "workflow_run", trigger_id=trigger_id)
    assert result.returncode != 0
    assert "Automatic publishing requires" in result.stderr


@pytest.mark.parametrize("run_id", ["", "0", "-1", "123; echo injected", "9007199254740992"])
def test_source_run_id_is_validated_as_data(source_run, run_id):
    """run ID 必须是正整数，脚本内容和超出安全整数范围的输入均被拒绝。"""
    result = run_source_check(source_run, run_id=run_id)
    assert result.returncode != 0
    assert "Build run ID must be a positive integer" in result.stderr
