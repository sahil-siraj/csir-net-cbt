import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
ENV_PATH = ROOT_DIR / ".env"
BACKEND_DIR = ROOT_DIR / "backend"
FRONTEND_DIR = ROOT_DIR / "frontend"


def ensure_env_defaults() -> dict[str, str]:
    """Ensure .env exists and contains JWT_SECRET without overwriting existing keys."""
    existing_lines: list[str] = []
    parsed: dict[str, str] = {}

    if ENV_PATH.exists():
        raw_text = ENV_PATH.read_text(encoding="utf-8")
        existing_lines = raw_text.splitlines()
        for line in existing_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, val = stripped.partition("=")
            parsed[key.strip()] = val.strip()

    additions: list[str] = []
    if not parsed.get("JWT_SECRET"):
        jwt_secret = secrets.token_urlsafe(32)
        parsed["JWT_SECRET"] = jwt_secret
        additions.append(f"JWT_SECRET={jwt_secret}")

    if additions:
        prefix = ""
        if ENV_PATH.exists():
            current = ENV_PATH.read_text(encoding="utf-8")
            if current and not current.endswith(("\n", "\r")):
                prefix = "\n"
            with ENV_PATH.open("a", encoding="utf-8") as f:
                f.write(prefix + "\n".join(additions) + "\n")
        else:
            ENV_PATH.write_text("\n".join(additions) + "\n", encoding="utf-8")
        print(f"[start.py] Added missing defaults to .env: {', '.join(a.split('=')[0] for a in additions)}")

    return parsed


def find_venv_python() -> str:
    if os.name == "nt":
        candidate = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = ROOT_DIR / ".venv" / "bin" / "python"
    if candidate.exists():
        return str(candidate)
    return sys.executable


def build_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    if os.name == "nt":
        local_node = Path(env.get("LOCALAPPDATA", "")) / "nodejs"
        if local_node.exists():
            env["PATH"] = f"{local_node};{env.get('PATH', '')}"
    return env


def terminate_process_tree(proc: subprocess.Popen, name: str) -> None:
    if proc.poll() is not None:
        return
    print(f"[start.py] Stopping {name} (PID {proc.pid})...")
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def main() -> int:
    env_vars = ensure_env_defaults()
    venv_python = find_venv_python()
    sub_env = build_subprocess_env()

    npm_bin = "npm.cmd" if os.name == "nt" else "npm"
    npm_cmd = shutil.which(npm_bin, path=sub_env.get("PATH"))
    if not npm_cmd:
        print(f"[start.py] Error: Could not find {npm_bin} in PATH.", file=sys.stderr)
        return 1

    popen_kwargs: dict = {"env": sub_env}
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True

    print("[start.py] Generate single-use invite codes via: python manage_invites.py generate --count 12")
    print("[start.py] Starting FastAPI backend on http://localhost:8000 ...")
    backend_proc = subprocess.Popen(
        [venv_python, "-m", "uvicorn", "app.main:app", "--reload", "--port", "8000"],
        cwd=str(BACKEND_DIR),
        **popen_kwargs,
    )

    print("[start.py] Starting Vite dev server on http://localhost:5173 ...")
    try:
        frontend_proc = subprocess.Popen(
            [npm_cmd, "run", "dev", "--", "--port", "5173"],
            cwd=str(FRONTEND_DIR),
            **popen_kwargs,
        )
    except Exception:
        terminate_process_tree(backend_proc, "FastAPI backend")
        raise

    def handle_signal(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_signal)

    try:
        while True:
            b_code = backend_proc.poll()
            f_code = frontend_proc.poll()
            if b_code is not None:
                print(f"[start.py] Backend exited with code {b_code}. Shutting down...")
                return b_code
            if f_code is not None:
                print(f"[start.py] Frontend exited with code {f_code}. Shutting down...")
                return f_code
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[start.py] Ctrl+C received. Shutting down servers...")
        return 0
    finally:
        terminate_process_tree(frontend_proc, "Vite frontend")
        terminate_process_tree(backend_proc, "FastAPI backend")
        print("[start.py] All processes stopped.")


if __name__ == "__main__":
    raise SystemExit(main())
