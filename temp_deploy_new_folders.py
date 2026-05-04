import os
import posixpath
import stat
import sys
from pathlib import Path

import paramiko

HOST = "65.21.244.158"
USER = "root"
PASSWORD = "Cph181ko!!"

LOCAL_ROOT = Path(r"C:\Users\zma\Desktop\Testing-Module\Full-project-cybrian-qs")
LOCAL_BACKEND = LOCAL_ROOT / "backend"
LOCAL_FRONTEND = LOCAL_ROOT / "frontend"

REMOTE_BACKEND = "/root/new-backend"
REMOTE_FRONTEND = "/root/new-frontend"
WEB_ROOT = "/var/www/cybrain"

EXCLUDE_DIRS = {
    ".git",
    ".github",
    ".idea",
    ".vscode",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
}

EXCLUDE_FILES = {
    ".DS_Store",
    "Thumbs.db",
    ".env",
    "uvicorn_debug.log",
}


def run(ssh: paramiko.SSHClient, cmd: str, check: bool = True):
    print(f"\n$ {cmd}")
    _, stdout, stderr = ssh.exec_command(cmd)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    if out.strip():
        sys.stdout.buffer.write((out.strip() + "\n").encode("utf-8", errors="replace"))
    if err.strip():
        sys.stdout.buffer.write((err.strip() + "\n").encode("utf-8", errors="replace"))
    if check and code != 0:
        raise RuntimeError(f"Command failed ({code}): {cmd}")
    return code, out, err


def ensure_remote_dir(sftp: paramiko.SFTPClient, remote_dir: str):
    parts = [p for p in remote_dir.split("/") if p]
    curr = ""
    for p in parts:
        curr += "/" + p
        try:
            sftp.stat(curr)
        except FileNotFoundError:
            sftp.mkdir(curr)


def upload_tree(sftp: paramiko.SFTPClient, local_dir: Path, remote_dir: str):
    for root, dirs, files in os.walk(local_dir):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        rel = Path(root).relative_to(local_dir)
        remote_current = remote_dir if str(rel) == "." else posixpath.join(remote_dir, str(rel).replace("\\", "/"))
        ensure_remote_dir(sftp, remote_current)

        for file_name in files:
            if file_name in EXCLUDE_FILES:
                continue
            local_file = Path(root) / file_name
            remote_file = posixpath.join(remote_current, file_name)
            sftp.put(str(local_file), remote_file)
            mode = local_file.stat().st_mode
            if mode & stat.S_IXUSR:
                sftp.chmod(remote_file, 0o755)


def sha256_remote(ssh: paramiko.SSHClient, path: str) -> str:
    code, out, _ = run(ssh, f"if [ -e '{path}' ]; then sha256sum '{path}' | awk '{{print $1}}'; fi", check=False)
    if code != 0:
        return ""
    return out.strip()


def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PASSWORD, timeout=30)
    sftp = ssh.open_sftp()

    try:
        run(ssh, "set -e; mkdir -p /root/new-backend /root/new-frontend /var/www/cybrain")
        env_before = sha256_remote(ssh, "/root/new-backend/.env")

        run(
            ssh,
            "set -e; "
            "for p in /root/new-backend/* /root/new-backend/.[!.]* /root/new-backend/..?*; do "
            "[ -e \"$p\" ] || continue; "
            "[ \"$(basename \"$p\")\" = \".env\" ] && continue; "
            "rm -rf \"$p\"; "
            "done",
        )
        run(
            ssh,
            "set -e; "
            "for p in /root/new-frontend/* /root/new-frontend/.[!.]* /root/new-frontend/..?*; do "
            "[ -e \"$p\" ] || continue; "
            "rm -rf \"$p\"; "
            "done",
        )

        print("\nUploading backend...")
        upload_tree(sftp, LOCAL_BACKEND, REMOTE_BACKEND)
        print("Uploading frontend...")
        upload_tree(sftp, LOCAL_FRONTEND, REMOTE_FRONTEND)

        run(
            ssh,
            "set -e; "
            "if [ ! -e /root/new-backend/.env ]; then "
            "if [ -e /root/cybrain-backend/.env ]; then ln -s /root/cybrain-backend/.env /root/new-backend/.env; "
            "elif [ -e /root/backend/.env ]; then ln -s /root/backend/.env /root/new-backend/.env; "
            "fi; fi",
        )

        env_after = sha256_remote(ssh, "/root/new-backend/.env")
        print(f".env checksum before: {env_before or 'missing'}")
        print(f".env checksum after : {env_after or 'missing'}")
        if env_before and env_after and env_before != env_after:
            raise RuntimeError(".env checksum changed unexpectedly")

        run(
            ssh,
            "set -e; cd /root/new-backend; "
            "python3 -m venv .venv; "
            ". .venv/bin/activate; "
            "python -m pip install --upgrade pip; "
            "pip install -r requirements.txt",
        )

        run(
            ssh,
            "set -e; cd /root/new-frontend; "
            "npm install; npm run build",
        )

        run(
            ssh,
            "set -e; rm -rf /var/www/cybrain/*; cp -a /root/new-frontend/dist/. /var/www/cybrain/",
        )

        run(ssh, "set -e; systemctl daemon-reload; systemctl restart cybrain-backend.service; systemctl reload nginx")
        run(ssh, "systemctl is-active cybrain-backend.service && systemctl is-active nginx")
        run(ssh, "curl -sS -i http://127.0.0.1:8000/api/health")
        run(ssh, "ls -la /root/new-backend | sed -n '1,120p'")
        run(ssh, "ls -la /root/new-frontend | sed -n '1,120p'")
        run(ssh, "ls -la /var/www/cybrain | sed -n '1,120p'")
    finally:
        sftp.close()
        ssh.close()


if __name__ == "__main__":
    main()
