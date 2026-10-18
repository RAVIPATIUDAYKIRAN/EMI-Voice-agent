from io import StringIO
import socket
import threading
import time
from types import SimpleNamespace
from urllib.request import urlopen

from scripts import run_app_and_conversation as launcher


class _FakeProcess:
    def __init__(self, *, stdout=None, returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.running = True
        self.terminated = False
        self.signaled = False

    def poll(self):
        return None if self.running else self.returncode

    def wait(self, timeout=None):
        self.running = False
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.running = False

    def send_signal(self, _signal):
        self.signaled = True
        self.running = False

    def kill(self):
        self.running = False


def test_combined_launcher_keeps_server_for_conversation_then_stops_it(monkeypatch, capsys):
    server = _FakeProcess(stdout=StringIO("Uvicorn running\n"))
    conversation = _FakeProcess(returncode=0)
    processes = iter([server, conversation])
    commands = []

    def fake_popen(command, **kwargs):
        commands.append((command, kwargs))
        return next(processes)

    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(launcher, "_wait_until_ready", lambda *_args: None)

    result = launcher.run_launcher(2, host="127.0.0.1", port=8000)

    assert result == 0
    assert commands[0][0][1:5] == ["-m", "uvicorn", "app.main:app", "--host"]
    assert commands[0][1]["cwd"] == launcher.PROJECT_ROOT
    assert commands[0][1]["stdout"] is launcher.subprocess.PIPE
    assert commands[0][1]["stderr"] is launcher.subprocess.STDOUT
    assert commands[1][0][-2:] == ["--customer-id", "2"]
    assert commands[1][1]["cwd"] == launcher.PROJECT_ROOT
    assert server.terminated or server.signaled
    captured = capsys.readouterr().out
    assert "[Uvicorn] Uvicorn running" in captured
    assert "http://127.0.0.1:8000/docs" in captured


def test_combined_launcher_cleans_server_if_readiness_fails(monkeypatch):
    server = _FakeProcess(stdout=StringIO(""))
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *_args, **_kwargs: server)
    monkeypatch.setattr(
        launcher,
        "_wait_until_ready",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("server not ready")),
    )

    assert launcher.run_launcher(startup_timeout=0.01) == 1
    assert server.terminated or server.signaled


def test_health_and_swagger_are_reachable_while_conversation_child_runs(monkeypatch, tmp_path):
    # Use a waiting stand-in for the existing CLI so this integration check
    # exercises the real Uvicorn child without creating calls or invoking an LLM.
    marker = tmp_path / "conversation-active.txt"
    fake_cli = tmp_path / "fake_conversation.py"
    fake_cli.write_text(
        "import pathlib, time\n"
        f"pathlib.Path({str(marker)!r}).write_text('active')\n"
        "time.sleep(3)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(launcher, "CONVERSATION_SCRIPT", fake_cli)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    result = []
    runner = threading.Thread(
        target=lambda: result.append(launcher.run_launcher(port=port, startup_timeout=15)),
        daemon=True,
    )
    runner.start()
    base_url = f"http://localhost:{port}"
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline and not marker.exists():
        if not runner.is_alive():
            break
        time.sleep(0.1)

    assert marker.exists(), "conversation child did not start"
    assert runner.is_alive(), "conversation child should still be active"
    with urlopen(f"{base_url}/health", timeout=2) as response:
        assert response.status == 200
        assert b'"status":"ok"' in response.read()
    with urlopen(f"{base_url}/docs", timeout=2) as response:
        assert response.status == 200
        assert b"swagger-ui" in response.read().lower()

    runner.join(timeout=10)
    assert not runner.is_alive(), "launcher did not shut down after conversation exit"
    assert result == [0]
