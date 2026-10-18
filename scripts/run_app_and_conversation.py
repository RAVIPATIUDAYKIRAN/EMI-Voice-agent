"""Run Uvicorn and the existing interactive conversation in one terminal."""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TextIO
from urllib.error import URLError
from urllib.request import urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONVERSATION_SCRIPT = PROJECT_ROOT / "scripts" / "run_terminal_conversation.py"


def _stream_server_output(stream: TextIO) -> None:
    """Forward Uvicorn output while the main thread remains available for input."""
    try:
        for line in iter(stream.readline, ""):
            print(f"[Uvicorn] {line.rstrip()}", flush=True)
    finally:
        stream.close()


def _wait_until_ready(process: subprocess.Popen, url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Uvicorn exited before the application became ready")
        try:
            with urlopen(url, timeout=0.5) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            time.sleep(0.2)
    raise RuntimeError(f"Uvicorn did not become ready within {timeout:g} seconds")


def _stop_server(process: subprocess.Popen | None, output_thread: threading.Thread | None) -> None:
    if process is not None and process.poll() is None:
        if sys.platform == "win32":
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
                process.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                process.terminate()
        else:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    if output_thread is not None:
        output_thread.join(timeout=1)


def run_launcher(
    customer_id: int | None = None,
    *,
    host: str = "localhost",
    port: int = 8000,
    startup_timeout: float = 30,
) -> int:
    """Start the API child, then run the existing conversation CLI interactively."""
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        host,
        "--port",
        str(port),
    ]
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    server = None
    output_thread = None
    conversation = None
    try:
        server = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )
        assert server.stdout is not None
        output_thread = threading.Thread(
            target=_stream_server_output,
            args=(server.stdout,),
            name="uvicorn-output",
            daemon=True,
        )
        output_thread.start()

        base_url = f"http://{host}:{port}"
        _wait_until_ready(server, f"{base_url}/health", startup_timeout)
        print(f"FastAPI is available at {base_url}/docs", flush=True)
        print("Starting the simulated conversation. Uvicorn logs are prefixed [Uvicorn].", flush=True)

        cli_command = [sys.executable, str(CONVERSATION_SCRIPT)]
        if customer_id is not None:
            cli_command.extend(["--customer-id", str(customer_id)])
        conversation = subprocess.Popen(cli_command, cwd=PROJECT_ROOT)
        return conversation.wait()
    except KeyboardInterrupt:
        print("\nStopping the conversation and FastAPI server...", flush=True)
        return 130
    except (OSError, RuntimeError) as exc:
        # Keep operational details local and avoid printing environment values.
        print(f"Could not start the combined workflow: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        if conversation is not None and conversation.poll() is None:
            conversation.terminate()
            try:
                conversation.wait(timeout=5)
            except subprocess.TimeoutExpired:
                conversation.kill()
                conversation.wait()
        _stop_server(server, output_thread)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run FastAPI and the simulated EMI conversation in one terminal."
    )
    parser.add_argument("--customer-id", type=int, help="Existing customer ID for the conversation")
    parser.add_argument("--host", default="localhost", help="Uvicorn bind address")
    parser.add_argument("--port", type=int, default=8000, help="Uvicorn port")
    args = parser.parse_args()
    if args.customer_id is not None and args.customer_id <= 0:
        parser.error("--customer-id must be positive")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    return run_launcher(args.customer_id, host=args.host, port=args.port)


if __name__ == "__main__":
    raise SystemExit(main())
