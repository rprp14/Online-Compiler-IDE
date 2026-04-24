import html as html_lib
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import secrets
from functools import wraps
from pathlib import Path
from threading import Lock

import psutil
from flask import Flask, jsonify, request, send_from_directory, session
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash

try:
    import boto3
except ImportError:  # pragma: no cover
    boto3 = None


BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
FRONTEND_DIR = PROJECT_DIR / "frontend"
TEMP_CODE_DIR = BASE_DIR / "temp_code"
DATA_DIR = BASE_DIR / "data"
OBJECT_STORAGE_DIR = DATA_DIR / "object_storage"
DATABASE_PATH = DATA_DIR / "cloud_ide.db"

TEMP_CODE_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
OBJECT_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "cloud-ide-dev-secret")
CORS(app, supports_credentials=True)

TIMEOUT_SECONDS = int(os.getenv("EXECUTION_TIMEOUT_SECONDS", "8"))
MAX_CODE_LENGTH = 25000
MAX_INPUT_LENGTH = 6000
SIMULATED_EXECUTION_SERVERS = ["docker-node-a", "docker-node-b", "docker-node-c"]
SERVER_LOCK = Lock()
SERVER_STATE = {
    name: {"active_jobs": 0, "total_jobs": 0, "last_duration": 0.0}
    for name in SIMULATED_EXECUTION_SERVERS
}
STORAGE_QUOTA_BYTES = int(os.getenv("CLOUD_STORAGE_QUOTA_MB", "10")) * 1024 * 1024

WORKSPACE_LANGUAGES = {
    "python",
    "c",
    "cpp",
    "java",
    "javascript",
    "html",
    "css",
    "json",
    "markdown",
    "text",
}
EXECUTABLE_LANGUAGES = {"python", "c", "cpp", "java", "javascript", "html"}
PERMISSIONS = {"read", "write", "both"}
DEFAULT_EDITOR_FILE = {
    "filename": "main.py",
    "language": "python",
    "code": 'print("Welcome to Cloud IDE")\n',
}

LANGUAGE_CONFIG = {
    "python": {
        "filename": "main.py",
        "compile": None,
        "run": lambda paths: ["python", str(paths["source"])],
        "docker_image": "python:3.12-alpine",
        "docker_command": lambda paths: ["python", f"/workspace/{paths['source'].name}"],
    },
    "c": {
        "filename": "main.c",
        "compile": lambda paths: ["gcc", str(paths["source"]), "-o", str(paths["executable"])],
        "run": lambda paths: [str(paths["executable"])],
        "docker_image": "gcc:14",
        "docker_command": lambda paths: [
            "sh",
            "-lc",
            f"gcc /workspace/{paths['source'].name} -o /workspace/program && /workspace/program",
        ],
    },
    "cpp": {
        "filename": "main.cpp",
        "compile": lambda paths: ["g++", str(paths["source"]), "-std=c++17", "-o", str(paths["executable"])],
        "run": lambda paths: [str(paths["executable"])],
        "docker_image": "gcc:14",
        "docker_command": lambda paths: [
            "sh",
            "-lc",
            f"g++ /workspace/{paths['source'].name} -std=c++17 -o /workspace/program && /workspace/program",
        ],
    },
    "java": {
        "filename": "Main.java",
        "compile": lambda paths: ["javac", str(paths["source"])],
        "run": lambda paths: ["java", "-cp", str(paths["workdir"]), "Main"],
        "docker_image": "eclipse-temurin:21-jdk",
        "docker_command": lambda paths: [
            "sh",
            "-lc",
            "javac /workspace/Main.java && java -cp /workspace Main",
        ],
    },
    "javascript": {
        "filename": "main.js",
        "compile": None,
        "run": lambda paths: ["node", str(paths["source"])],
        "docker_image": "node:22-alpine",
        "docker_command": lambda paths: ["node", f"/workspace/{paths['source'].name}"],
    },
}

SUGGESTION_RULES = {
    "python": [
        (r"SyntaxError", "Check for a missing colon, bracket, or quote near the highlighted line."),
        (r"IndentationError", "Python indentation must stay consistent. Verify tabs versus spaces."),
        (r"NameError", "A variable or function name is undefined. Define it before use."),
    ],
    "c": [
        (r"undefined reference", "A function is missing or is not linked correctly."),
        (r"expected", "There is likely a missing symbol such as `;`, `)` or `}` near the shown line."),
        (r"segmentation fault", "The program may be reading invalid memory or array indexes."),
    ],
    "cpp": [
        (r"undefined reference", "A function or method declaration may not match its definition."),
        (r"no matching function", "A function call signature does not match any available overload."),
        (r"segmentation fault", "Check pointer usage and vector or array bounds."),
    ],
    "java": [
        (r"cannot find symbol", "Java cannot resolve a class, variable, or method name."),
        (r"Exception in thread", "The program threw a runtime exception. Check null values and input parsing."),
        (r"class .* is public, should be declared in a file named", "The public class name must match the file name."),
    ],
    "javascript": [
        (r"ReferenceError", "A variable is being used before it exists."),
        (r"SyntaxError", "Check for a missing bracket, quote, or comma."),
        (r"TypeError", "A value is being used in a way that does not match its type."),
    ],
}


class StorageClient:
    def __init__(self):
        self.mode = os.getenv("STORAGE_BACKEND", "local").lower()
        self.bucket = os.getenv("AWS_S3_BUCKET", "").strip()
        self.region = os.getenv("AWS_REGION", "").strip()
        self.client = None
        if self.mode == "s3" and boto3 and self.bucket:
            self.client = boto3.client("s3", region_name=self.region or None)
        else:
            self.mode = "local"

    def write_text(self, key: str, content: str) -> str:
        if self.mode == "s3" and self.client:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=content.encode("utf-8"))
        else:
            target = OBJECT_STORAGE_DIR / key
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return key

    def read_text(self, key: str) -> str:
        if self.mode == "s3" and self.client:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read().decode("utf-8")
        target = OBJECT_STORAGE_DIR / key
        if not target.exists():
            return ""
        return target.read_text(encoding="utf-8")

    def descriptor(self) -> str:
        if self.mode == "s3":
            return f"AWS S3 ({self.bucket})"
        return "Local object storage"


storage_client = StorageClient()


def get_db_connection():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def column_exists(connection, table_name: str, column_name: str) -> bool:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row[1] == column_name for row in rows)


def init_db():
    connection = get_db_connection()
    cursor = connection.cursor()
    cursor.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS workspace_folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            parent_id INTEGER,
            name TEXT NOT NULL,
            is_deleted INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (parent_id) REFERENCES workspace_folders (id)
        );

        CREATE TABLE IF NOT EXISTS code_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            folder_id INTEGER,
            filename TEXT NOT NULL,
            language TEXT NOT NULL,
            code TEXT NOT NULL,
            storage_key TEXT,
            is_deleted INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (folder_id) REFERENCES workspace_folders (id)
        );

        CREATE TABLE IF NOT EXISTS code_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id INTEGER NOT NULL,
            version_number INTEGER NOT NULL,
            code TEXT NOT NULL,
            storage_key TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (file_id) REFERENCES code_files (id)
        );

        CREATE TABLE IF NOT EXISTS execution_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            file_id INTEGER,
            language TEXT NOT NULL,
            code TEXT NOT NULL,
            program_input TEXT DEFAULT '',
            output TEXT DEFAULT '',
            error TEXT DEFAULT '',
            suggestion TEXT DEFAULT '',
            execution_time REAL DEFAULT 0,
            assigned_server TEXT NOT NULL,
            status TEXT NOT NULL,
            execution_mode TEXT DEFAULT 'local-process',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (file_id) REFERENCES code_files (id)
        );

        CREATE TABLE IF NOT EXISTS workspace_shares (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id INTEGER NOT NULL,
            target_user_id INTEGER NOT NULL,
            resource_type TEXT NOT NULL,
            resource_id INTEGER NOT NULL,
            permission TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (owner_user_id) REFERENCES users (id),
            FOREIGN KEY (target_user_id) REFERENCES users (id)
        );

        CREATE TABLE IF NOT EXISTS workspace_state (
            user_id INTEGER PRIMARY KEY,
            selected_file_id INTEGER,
            language TEXT DEFAULT 'python',
            editor_code TEXT DEFAULT '',
            syntax_code TEXT DEFAULT '',
            stdin_text TEXT DEFAULT '',
            theme TEXT DEFAULT 'dark',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (selected_file_id) REFERENCES code_files (id)
        );

        CREATE TABLE IF NOT EXISTS workspace_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,
            file_id INTEGER,
            title TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (file_id) REFERENCES code_files (id)
        );

        CREATE TABLE IF NOT EXISTS deployments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,
            file_id INTEGER,
            language TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (file_id) REFERENCES code_files (id)
        );
        """
    )

    if not column_exists(connection, "workspace_folders", "parent_id"):
        connection.execute("ALTER TABLE workspace_folders ADD COLUMN parent_id INTEGER")
    if not column_exists(connection, "workspace_folders", "is_deleted"):
        connection.execute("ALTER TABLE workspace_folders ADD COLUMN is_deleted INTEGER DEFAULT 0")
    if not column_exists(connection, "code_files", "storage_key"):
        connection.execute("ALTER TABLE code_files ADD COLUMN storage_key TEXT")
    if not column_exists(connection, "code_files", "is_deleted"):
        connection.execute("ALTER TABLE code_files ADD COLUMN is_deleted INTEGER DEFAULT 0")
    if not column_exists(connection, "code_versions", "storage_key"):
        connection.execute("ALTER TABLE code_versions ADD COLUMN storage_key TEXT")
    if not column_exists(connection, "execution_history", "execution_mode"):
        connection.execute("ALTER TABLE execution_history ADD COLUMN execution_mode TEXT DEFAULT 'local-process'")
    if not column_exists(connection, "workspace_state", "theme"):
        connection.execute("ALTER TABLE workspace_state ADD COLUMN theme TEXT DEFAULT 'dark'")

    connection.commit()
    connection.close()


init_db()


def current_user_id():
    return session.get("user_id")


def login_required(route_function):
    @wraps(route_function)
    def wrapped(*args, **kwargs):
        if not current_user_id():
            return jsonify({"error": "Login required."}), 401
        return route_function(*args, **kwargs)

    return wrapped


def user_response_row(user_row):
    return {
        "id": user_row["id"],
        "username": user_row["username"],
        "created_at": user_row["created_at"],
    }


def validate_username(username: str) -> str | None:
    if len(username) < 3:
        return "Username must be at least 3 characters long."
    if not re.fullmatch(r"[A-Za-z0-9_]+", username):
        return "Username can contain only letters, numbers, and underscores."
    return None


def validate_password(password: str) -> str | None:
    if len(password) < 8:
        return "Password must be at least 8 characters long."
    if not re.search(r"[A-Z]", password):
        return "Password must include at least one uppercase letter."
    if not re.search(r"[a-z]", password):
        return "Password must include at least one lowercase letter."
    if not re.search(r"[0-9]", password):
        return "Password must include at least one number."
    return None


def sanitize_name(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "-", value or "").strip()
    return cleaned[:120]


def normalized_parent_key(parent_id):
    return -1 if parent_id in (None, "") else int(parent_id)


def dedupe_records(rows, key_fn):
    deduped = {}
    for row in rows:
        key = key_fn(row)
        existing = deduped.get(key)
        if existing is None or row["id"] > existing["id"]:
            deduped[key] = row
    return list(deduped.values())


def guess_language_from_filename(filename: str) -> str:
    mapping = {
        ".py": "python",
        ".c": "c",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".cxx": "cpp",
        ".java": "java",
        ".js": "javascript",
        ".html": "html",
        ".css": "css",
        ".json": "json",
        ".md": "markdown",
        ".txt": "text",
    }
    return mapping.get(Path(filename).suffix.lower(), "text")


def file_storage_key(user_id: int, file_id: int, version_number: int) -> str:
    return f"users/{user_id}/files/{file_id}/versions/{version_number}.txt"


def basic_security_check(code: str, language: str) -> str | None:
    blocked_patterns = [
        r"\bos\.system\s*\(",
        r"\bsubprocess\.",
        r"\beval\s*\(",
        r"\bexec\s*\(",
        r"\b__import__\s*\(",
        r"\bimport\s+socket\b",
        r"\bsystem\s*\(",
        r"\bfork\s*\(",
        r"\bpopen\s*\(",
        r"\bRuntime\.getRuntime\s*\(",
        r"\bProcessBuilder\b",
    ]
    if language in {"html", "css", "json", "markdown", "text"}:
        return None
    for pattern in blocked_patterns:
        if re.search(pattern, code):
            return f"Blocked potentially unsafe code pattern for {language}."
    return None


def run_command(command, user_input=None, cwd=None):
    return subprocess.run(
        command,
        input=user_input,
        text=True,
        capture_output=True,
        timeout=TIMEOUT_SECONDS,
        cwd=cwd,
    )


def build_metrics(start_time: float):
    process = psutil.Process()
    memory_usage_mb = round(process.memory_info().rss / (1024 * 1024), 2)
    return {
        "execution_time": round(time.perf_counter() - start_time, 4),
        "cpu_percent": round(psutil.cpu_percent(interval=0.1), 2),
        "memory_mb": memory_usage_mb,
    }


def pick_server():
    with SERVER_LOCK:
        server_name = sorted(
            SERVER_STATE.items(),
            key=lambda item: (item[1]["active_jobs"], item[1]["total_jobs"], item[0]),
        )[0][0]
        SERVER_STATE[server_name]["active_jobs"] += 1
        SERVER_STATE[server_name]["total_jobs"] += 1
        return server_name


def release_server(server_name: str, duration: float):
    with SERVER_LOCK:
        SERVER_STATE[server_name]["active_jobs"] = max(0, SERVER_STATE[server_name]["active_jobs"] - 1)
        SERVER_STATE[server_name]["last_duration"] = round(duration, 4)


def get_server_snapshot():
    with SERVER_LOCK:
        return [
            {
                "name": name,
                "active_jobs": data["active_jobs"],
                "total_jobs": data["total_jobs"],
                "last_duration": data["last_duration"],
            }
            for name, data in SERVER_STATE.items()
        ]


def suggest_fix(language: str, error_text: str) -> str:
    if not error_text:
        return ""
    for pattern, suggestion in SUGGESTION_RULES.get(language, []):
        if re.search(pattern, error_text, flags=re.IGNORECASE):
            return suggestion
    return "Review the highlighted line, make a small targeted fix, and run again."


def permission_allows_write(permission: str | None) -> bool:
    return permission in {"write", "both"}


def docker_available() -> bool:
    return shutil.which("docker") is not None


def should_use_docker() -> bool:
    return os.getenv("ENABLE_DOCKER_EXECUTION", "false").lower() == "true" and docker_available()


def execute_html_preview(code: str):
    return {
        "output": "HTML preview generated successfully.",
        "error": "",
        "execution_time": 0.001,
        "assigned_server": "preview-engine",
        "service": "html-preview-service",
        "execution_mode": "browser-preview",
        "monitoring": {"cpu_percent": 0, "memory_mb": 0},
        "server_loads": get_server_snapshot(),
        "suggestion": "Update the markup and rerun to refresh the preview.",
        "preview_html": code,
    }


def execute_with_local_runtime(config: dict, paths: dict, language: str, user_input: str, start_time: float, server: str):
    compile_command = config["compile"](paths) if config["compile"] else None
    if compile_command:
        compiler = compile_command[0]
        if shutil.which(compiler) is None:
            return {
                "output": "",
                "error": f"{compiler} is not installed on this system.",
                "execution_time": 0,
                "assigned_server": server,
                "service": "execution-service",
                "execution_mode": "local-process",
                "monitoring": {"cpu_percent": 0, "memory_mb": 0},
                "server_loads": get_server_snapshot(),
                "suggestion": f"Install {compiler} on the host machine to enable {language} execution.",
            }
        compile_result = run_command(compile_command, cwd=str(paths["workdir"]))
        if compile_result.returncode != 0:
            metrics = build_metrics(start_time)
            error_text = compile_result.stderr.strip() or "Compilation failed."
            return {
                "output": compile_result.stdout.strip(),
                "error": error_text,
                "execution_time": metrics["execution_time"],
                "assigned_server": server,
                "service": "execution-service",
                "execution_mode": "local-process",
                "monitoring": metrics,
                "server_loads": get_server_snapshot(),
                "suggestion": suggest_fix(language, error_text),
            }

    run_command_args = config["run"](paths)
    runner = run_command_args[0]
    if shutil.which(runner) is None and not Path(runner).exists():
        return {
            "output": "",
            "error": f"{runner} is not installed on this system.",
            "execution_time": 0,
            "assigned_server": server,
            "service": "execution-service",
            "execution_mode": "local-process",
            "monitoring": {"cpu_percent": 0, "memory_mb": 0},
            "server_loads": get_server_snapshot(),
            "suggestion": f"Install {runner} on the host machine to run {language} programs.",
        }

    result = run_command(run_command_args, user_input=user_input, cwd=str(paths["workdir"]))
    metrics = build_metrics(start_time)
    error_text = result.stderr.strip()
    return {
        "output": result.stdout.strip(),
        "error": error_text,
        "execution_time": metrics["execution_time"],
        "assigned_server": server,
        "service": "execution-service",
        "execution_mode": "local-process",
        "monitoring": metrics,
        "server_loads": get_server_snapshot(),
        "suggestion": suggest_fix(language, error_text),
    }


def execute_with_docker(config: dict, paths: dict, language: str, user_input: str, start_time: float, server: str):
    docker_command = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--cpus",
        "0.50",
        "--memory",
        "256m",
        "--pids-limit",
        "64",
        "-i",
        "-v",
        f"{paths['workdir']}:/workspace",
        "-w",
        "/workspace",
        config["docker_image"],
        *config["docker_command"](paths),
    ]
    result = run_command(docker_command, user_input=user_input, cwd=str(paths["workdir"]))
    metrics = build_metrics(start_time)
    error_text = result.stderr.strip()
    return {
        "output": result.stdout.strip(),
        "error": error_text,
        "execution_time": metrics["execution_time"],
        "assigned_server": server,
        "service": "docker-execution-service",
        "execution_mode": "docker-container",
        "monitoring": metrics,
        "server_loads": get_server_snapshot(),
        "suggestion": suggest_fix(language, error_text),
    }


def execute_on_remote_service(code: str, language: str, user_input: str):
    if language == "html":
        return execute_html_preview(code)
    if language not in LANGUAGE_CONFIG:
        return {"output": "", "error": "Unsupported language selected.", "execution_time": 0, "suggestion": ""}
    if len(code) > MAX_CODE_LENGTH:
        return {"output": "", "error": "Code is too long.", "execution_time": 0, "suggestion": "Reduce the program size and try again."}
    if len(user_input) > MAX_INPUT_LENGTH:
        return {"output": "", "error": "Input is too long.", "execution_time": 0, "suggestion": "Shorten stdin input before execution."}

    security_error = basic_security_check(code, language)
    if security_error:
        return {
            "output": "",
            "error": security_error,
            "execution_time": 0,
            "suggestion": "Remove unsafe operations such as shell access, process creation, or network calls.",
        }

    config = LANGUAGE_CONFIG[language]
    server = pick_server()
    start_time = time.perf_counter()
    with tempfile.TemporaryDirectory(dir=TEMP_CODE_DIR) as temp_dir:
        workdir = Path(temp_dir)
        source_path = workdir / config["filename"]
        executable_path = workdir / "program"
        source_path.write_text(code, encoding="utf-8")
        paths = {"source": source_path, "workdir": workdir, "executable": executable_path}

        try:
            if should_use_docker():
                return execute_with_docker(config, paths, language, user_input, start_time, server)
            return execute_with_local_runtime(config, paths, language, user_input, start_time, server)
        except subprocess.TimeoutExpired:
            metrics = build_metrics(start_time)
            return {
                "output": "",
                "error": f"Execution timed out after {TIMEOUT_SECONDS} seconds.",
                "execution_time": metrics["execution_time"],
                "assigned_server": server,
                "service": "execution-service",
                "execution_mode": "docker-container" if should_use_docker() else "local-process",
                "monitoring": metrics,
                "server_loads": get_server_snapshot(),
                "suggestion": "The program may be stuck in a loop or waiting for additional input.",
            }
        except Exception as exc:
            metrics = build_metrics(start_time)
            return {
                "output": "",
                "error": f"Server error: {exc}",
                "execution_time": metrics["execution_time"],
                "assigned_server": server,
                "service": "execution-service",
                "execution_mode": "docker-container" if should_use_docker() else "local-process",
                "monitoring": metrics,
                "server_loads": get_server_snapshot(),
                "suggestion": "Review the code structure and retry with a smaller sample case.",
            }
        finally:
            release_server(server, time.perf_counter() - start_time)


def fetch_user(connection, username: str):
    return connection.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def fetch_folder_row(connection, folder_id: int):
    return connection.execute(
        """
        SELECT wf.*, u.username AS owner_username
        FROM workspace_folders wf
        JOIN users u ON u.id = wf.user_id
        WHERE wf.id = ? AND wf.is_deleted = 0
        """,
        (folder_id,),
    ).fetchone()


def fetch_file_row(connection, file_id: int):
    return connection.execute(
        """
        SELECT cf.*, u.username AS owner_username
        FROM code_files cf
        JOIN users u ON u.id = cf.user_id
        WHERE cf.id = ? AND cf.is_deleted = 0
        """,
        (file_id,),
    ).fetchone()


def folder_ancestor_ids(connection, folder_id: int | None):
    ancestors = []
    current = folder_id
    while current:
        ancestors.append(current)
        row = connection.execute(
            "SELECT parent_id FROM workspace_folders WHERE id = ? AND is_deleted = 0",
            (current,),
        ).fetchone()
        current = row["parent_id"] if row else None
    return ancestors


def folder_descendant_ids(connection, root_folder_id: int):
    descendants = []
    pending = [root_folder_id]
    while pending:
        folder_id = pending.pop()
        descendants.append(folder_id)
        child_rows = connection.execute(
            "SELECT id FROM workspace_folders WHERE parent_id = ? AND is_deleted = 0",
            (folder_id,),
        ).fetchall()
        pending.extend(row["id"] for row in child_rows)
    return descendants


def get_folder_permission(connection, user_id: int, folder_row):
    if not folder_row:
        return None
    if folder_row["user_id"] == user_id:
        return "write"
    ancestor_ids = folder_ancestor_ids(connection, folder_row["id"])
    placeholders = ",".join("?" * len(ancestor_ids))
    share_row = connection.execute(
        f"""
        SELECT permission
        FROM workspace_shares
        WHERE target_user_id = ?
          AND resource_type = 'folder'
          AND resource_id IN ({placeholders})
        ORDER BY CASE permission WHEN 'write' THEN 1 WHEN 'both' THEN 1 ELSE 2 END
        LIMIT 1
        """,
        [user_id, *ancestor_ids],
    ).fetchone()
    return share_row["permission"] if share_row else None


def get_file_permission(connection, user_id: int, file_row):
    if not file_row:
        return None
    if file_row["user_id"] == user_id:
        return "write"
    direct_share = connection.execute(
        """
        SELECT permission
        FROM workspace_shares
        WHERE target_user_id = ? AND resource_type = 'file' AND resource_id = ?
        ORDER BY CASE permission WHEN 'write' THEN 1 WHEN 'both' THEN 1 ELSE 2 END
        LIMIT 1
        """,
        (user_id, file_row["id"]),
    ).fetchone()
    if direct_share:
        return direct_share["permission"]
    if file_row["folder_id"]:
        folder_row = fetch_folder_row(connection, file_row["folder_id"])
        return get_folder_permission(connection, user_id, folder_row)
    return None


def ensure_folder_access(connection, folder_id: int | None, user_id: int, require_write: bool = False):
    if folder_id is None:
        return None, "write"
    folder_row = fetch_folder_row(connection, folder_id)
    permission = get_folder_permission(connection, user_id, folder_row)
    if not folder_row or not permission:
        return None, None
    if require_write and not permission_allows_write(permission):
        return folder_row, None
    return folder_row, permission


def ensure_file_access(connection, file_id: int, user_id: int, require_write: bool = False):
    file_row = fetch_file_row(connection, file_id)
    permission = get_file_permission(connection, user_id, file_row)
    if not file_row or not permission:
        return None, None
    if require_write and not permission_allows_write(permission):
        return file_row, None
    return file_row, permission


def serialize_folder(folder_row, permission="write"):
    return {
        "id": folder_row["id"],
        "name": folder_row["name"],
        "parent_id": folder_row["parent_id"],
        "owner_id": folder_row["user_id"],
        "owner_username": folder_row["owner_username"],
        "permission": permission,
        "created_at": folder_row["created_at"],
        "updated_at": folder_row["updated_at"],
        "type": "folder",
    }


def serialize_file(file_row, permission="write"):
    return {
        "id": file_row["id"],
        "filename": file_row["filename"],
        "language": file_row["language"],
        "folder_id": file_row["folder_id"],
        "owner_id": file_row["user_id"],
        "owner_username": file_row["owner_username"],
        "permission": permission,
        "storage_key": file_row["storage_key"],
        "created_at": file_row["created_at"],
        "updated_at": file_row["updated_at"],
        "type": "file",
    }


def list_workspace_tree(user_id: int):
    connection = get_db_connection()
    folder_rows = connection.execute(
        """
        SELECT wf.*, u.username AS owner_username
        FROM workspace_folders wf
        JOIN users u ON u.id = wf.user_id
        WHERE wf.is_deleted = 0
        ORDER BY wf.name COLLATE NOCASE
        """
    ).fetchall()
    visible_folders = []
    for folder_row in folder_rows:
        permission = get_folder_permission(connection, user_id, folder_row)
        if permission:
            visible_folders.append(serialize_folder(folder_row, permission))
    visible_folders = dedupe_records(
        visible_folders,
        lambda item: (normalized_parent_key(item["parent_id"]), item["name"].lower()),
    )
    visible_folders.sort(key=lambda item: (item["name"].lower(), item["id"]))

    file_rows = connection.execute(
        """
        SELECT cf.*, u.username AS owner_username
        FROM code_files cf
        JOIN users u ON u.id = cf.user_id
        WHERE cf.is_deleted = 0
        ORDER BY cf.filename COLLATE NOCASE
        """
    ).fetchall()
    visible_files = []
    for file_row in file_rows:
        permission = get_file_permission(connection, user_id, file_row)
        if permission:
            visible_files.append(serialize_file(file_row, permission))
    visible_files = dedupe_records(
        visible_files,
        lambda item: (normalized_parent_key(item["folder_id"]), item["filename"].lower()),
    )
    visible_files.sort(key=lambda item: (item["filename"].lower(), item["id"]))

    shares = connection.execute(
        """
        SELECT ws.id, ws.resource_type, ws.resource_id, ws.permission, ws.created_at,
               owner.username AS owner_username, target.username AS target_username
        FROM workspace_shares ws
        JOIN users owner ON owner.id = ws.owner_user_id
        JOIN users target ON target.id = ws.target_user_id
        WHERE ws.owner_user_id = ? OR ws.target_user_id = ?
        ORDER BY ws.id DESC
        """,
        (user_id, user_id),
    ).fetchall()
    connection.close()
    return {
        "folders": visible_folders,
        "files": visible_files,
        "recent_files": visible_files[:12],
        "shares": [dict(row) for row in shares],
    }


def list_history_for_user(user_id: int, limit: int = 12):
    connection = get_db_connection()
    rows = connection.execute(
        """
        SELECT eh.id, eh.language, eh.execution_time, eh.assigned_server, eh.status,
               eh.error, eh.suggestion, eh.execution_mode, eh.created_at, cf.filename
        FROM execution_history eh
        LEFT JOIN code_files cf ON cf.id = eh.file_id
        WHERE eh.user_id = ?
        ORDER BY eh.id DESC
        LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    connection.close()
    return [dict(row) for row in rows]


def get_storage_usage(user_id: int):
    connection = get_db_connection()
    rows = connection.execute(
        """
        SELECT
            COALESCE(SUM(LENGTH(code)), 0) AS files_bytes,
            COALESCE((SELECT SUM(LENGTH(cv.code))
                      FROM code_versions cv
                      JOIN code_files cf ON cf.id = cv.file_id
                      WHERE cf.user_id = ? AND cf.is_deleted = 0), 0) AS versions_bytes,
            COALESCE((SELECT SUM(LENGTH(eh.code))
                      FROM execution_history eh
                      WHERE eh.user_id = ?), 0) AS history_bytes
        FROM code_files
        WHERE user_id = ? AND is_deleted = 0
        """,
        (user_id, user_id, user_id),
    ).fetchone()
    connection.close()
    used_bytes = int(rows["files_bytes"] or 0) + int(rows["versions_bytes"] or 0) + int(rows["history_bytes"] or 0)
    quota_bytes = STORAGE_QUOTA_BYTES
    percent = round(min((used_bytes / quota_bytes) * 100 if quota_bytes else 0, 100), 2)
    return {
        "used_bytes": used_bytes,
        "quota_bytes": quota_bytes,
        "used_mb": round(used_bytes / (1024 * 1024), 2),
        "quota_mb": round(quota_bytes / (1024 * 1024), 2),
        "used_percent": percent,
    }


def default_workspace_state():
    return {
        "selected_file_id": None,
        "language": "python",
        "editor_code": "",
        "syntax_code": "",
        "stdin_text": "",
        "theme": "dark",
    }


def get_workspace_state(user_id: int):
    connection = get_db_connection()
    row = connection.execute("SELECT * FROM workspace_state WHERE user_id = ?", (user_id,)).fetchone()
    connection.close()
    if not row:
        return default_workspace_state()
    return {
        "selected_file_id": row["selected_file_id"],
        "language": row["language"] or "python",
        "editor_code": row["editor_code"] or "",
        "syntax_code": row["syntax_code"] or "",
        "stdin_text": row["stdin_text"] or "",
        "theme": row["theme"] or "dark",
    }


def upsert_workspace_state(user_id: int, payload: dict):
    current = default_workspace_state()
    current.update({key: payload.get(key, current[key]) for key in current})
    connection = get_db_connection()
    connection.execute(
        """
        INSERT INTO workspace_state (user_id, selected_file_id, language, editor_code, syntax_code, stdin_text, theme, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            selected_file_id = excluded.selected_file_id,
            language = excluded.language,
            editor_code = excluded.editor_code,
            syntax_code = excluded.syntax_code,
            stdin_text = excluded.stdin_text,
            theme = excluded.theme,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            user_id,
            current["selected_file_id"],
            current["language"],
            current["editor_code"],
            current["syntax_code"],
            current["stdin_text"],
            current["theme"],
        ),
    )
    connection.commit()
    connection.close()
    return current


def normalize_workspace_link_row(row):
    return {
        "id": row["id"],
        "token": row["token"],
        "file_id": row["file_id"],
        "title": row["title"],
        "created_at": row["created_at"],
    }


def normalize_deployment_row(row):
    return {
        "id": row["id"],
        "token": row["token"],
        "file_id": row["file_id"],
        "language": row["language"],
        "title": row["title"],
        "created_at": row["created_at"],
    }


def build_public_workspace_payload(user_id: int, file_id: int | None):
    tree = list_workspace_tree(user_id)
    payload = {
        "tree": tree,
        "selected_file": None,
        "workspace_title": "Shared Cloud Workspace",
    }
    if file_id:
        connection = get_db_connection()
        file_row = connection.execute(
            """
            SELECT cf.*, u.username AS owner_username
            FROM code_files cf
            JOIN users u ON u.id = cf.user_id
            WHERE cf.id = ? AND cf.is_deleted = 0 AND cf.user_id = ?
            """,
            (file_id, user_id),
        ).fetchone()
        if file_row:
            payload["selected_file"] = file_record_with_body(connection, file_id)
            payload["selected_file"]["permission"] = "read"
        connection.close()
    return payload


def file_record_with_body(connection, file_id: int):
    file_row = fetch_file_row(connection, file_id)
    if not file_row:
        return None
    code = file_row["code"]
    if file_row["storage_key"]:
        stored_code = storage_client.read_text(file_row["storage_key"])
        if stored_code:
            code = stored_code
    payload = serialize_file(file_row)
    payload["code"] = code
    return payload


def create_version(connection, file_row, code: str):
    version_row = connection.execute(
        "SELECT COALESCE(MAX(version_number), 0) AS max_version FROM code_versions WHERE file_id = ?",
        (file_row["id"],),
    ).fetchone()
    next_version = int(version_row["max_version"]) + 1
    storage_key = file_storage_key(file_row["user_id"], file_row["id"], next_version)
    storage_client.write_text(storage_key, code)
    connection.execute(
        "INSERT INTO code_versions (file_id, version_number, code, storage_key) VALUES (?, ?, ?, ?)",
        (file_row["id"], next_version, code, storage_key),
    )
    connection.execute(
        "UPDATE code_files SET code = ?, storage_key = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (code, storage_key, file_row["id"]),
    )
    return next_version, storage_key


def delete_folder_cascade(connection, folder_id: int):
    descendant_ids = folder_descendant_ids(connection, folder_id)
    placeholder = ",".join("?" * len(descendant_ids))
    connection.execute(
        f"UPDATE workspace_folders SET is_deleted = 1, updated_at = CURRENT_TIMESTAMP WHERE id IN ({placeholder})",
        descendant_ids,
    )
    connection.execute(
        f"UPDATE code_files SET is_deleted = 1, updated_at = CURRENT_TIMESTAMP WHERE folder_id IN ({placeholder})",
        descendant_ids,
    )


def record_execution(user_id: int, file_id: int | None, language: str, code: str, user_input: str, result: dict):
    connection = get_db_connection()
    connection.execute(
        """
        INSERT INTO execution_history (
            user_id, file_id, language, code, program_input, output, error,
            suggestion, execution_time, assigned_server, status, execution_mode
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            file_id,
            language,
            code,
            user_input,
            result.get("output", ""),
            result.get("error", ""),
            result.get("suggestion", ""),
            result.get("execution_time", 0),
            result.get("assigned_server", "unassigned"),
            "failed" if result.get("error") else "success",
            result.get("execution_mode", "local-process"),
        ),
    )
    connection.commit()
    connection.close()


@app.route("/")
def serve_index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/api/signup", methods=["POST"])
def signup():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    confirm_password = data.get("confirm_password") or ""
    username_error = validate_username(username)
    if username_error:
        return jsonify({"error": username_error}), 400
    password_error = validate_password(password)
    if password_error:
        return jsonify({"error": password_error}), 400
    if password != confirm_password:
        return jsonify({"error": "Password and confirm password do not match."}), 400

    connection = get_db_connection()
    try:
        cursor = connection.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, generate_password_hash(password)),
        )
        connection.commit()
        user_id = cursor.lastrowid
        session["user_id"] = user_id
        user_row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return jsonify({"message": "Account created successfully.", "user": user_response_row(user_row)})
    except sqlite3.IntegrityError:
        return jsonify({"error": "Username already exists."}), 409
    finally:
        connection.close()


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    connection = get_db_connection()
    user_row = fetch_user(connection, username)
    connection.close()
    if not user_row or not check_password_hash(user_row["password_hash"], password):
        return jsonify({"error": "Invalid username or password."}), 401
    session["user_id"] = user_row["id"]
    return jsonify({"message": "Login successful.", "user": user_response_row(user_row)})


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"message": "Logged out successfully."})


@app.route("/api/session", methods=["GET"])
def get_session_data():
    user_id = current_user_id()
    if not user_id:
        return jsonify({"authenticated": False, "user": None})
    connection = get_db_connection()
    user_row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    connection.close()
    if not user_row:
        session.clear()
        return jsonify({"authenticated": False, "user": None})
    return jsonify({"authenticated": True, "user": user_response_row(user_row)})


@app.route("/api/dashboard", methods=["GET"])
@login_required
def dashboard():
    user_id = current_user_id()
    connection = get_db_connection()
    stats = {
        "files": connection.execute(
            "SELECT COUNT(*) AS total FROM code_files WHERE user_id = ? AND is_deleted = 0",
            (user_id,),
        ).fetchone()["total"],
        "folders": connection.execute(
            "SELECT COUNT(*) AS total FROM workspace_folders WHERE user_id = ? AND is_deleted = 0",
            (user_id,),
        ).fetchone()["total"],
        "shared_with_me": connection.execute(
            "SELECT COUNT(*) AS total FROM workspace_shares WHERE target_user_id = ?",
            (user_id,),
        ).fetchone()["total"],
        "runs": connection.execute(
            "SELECT COUNT(*) AS total FROM execution_history WHERE user_id = ?",
            (user_id,),
        ).fetchone()["total"],
    }
    connection.close()
    tree = list_workspace_tree(user_id)
    storage_usage = get_storage_usage(user_id)
    return jsonify(
        {
            "stats": stats,
            "recent_files": tree["recent_files"],
            "recent_history": list_history_for_user(user_id, limit=8),
            "servers": get_server_snapshot(),
            "storage_backend": storage_client.descriptor(),
            "docker_enabled": should_use_docker(),
            "storage_usage": storage_usage,
        }
    )


@app.route("/api/workspace/tree", methods=["GET"])
@login_required
def workspace_tree():
    return jsonify(list_workspace_tree(current_user_id()))


@app.route("/api/folders", methods=["POST"])
@login_required
def create_folder():
    data = request.get_json(silent=True) or {}
    name = sanitize_name(data.get("name") or "")
    parent_id = data.get("parent_id")
    if not name:
        return jsonify({"error": "Folder name is required."}), 400

    connection = get_db_connection()
    parent_folder = None
    if parent_id not in (None, ""):
        parent_folder, permission = ensure_folder_access(connection, int(parent_id), current_user_id(), require_write=True)
        if not parent_folder or not permission_allows_write(permission):
            connection.close()
            return jsonify({"error": "You do not have write access to the selected parent folder."}), 403
        parent_id = parent_folder["id"]

    existing_folder = connection.execute(
        """
        SELECT id
        FROM workspace_folders
        WHERE user_id = ?
          AND is_deleted = 0
          AND COALESCE(parent_id, -1) = ?
          AND LOWER(name) = LOWER(?)
        ORDER BY id DESC
        LIMIT 1
        """,
        (current_user_id(), normalized_parent_key(parent_id), name),
    ).fetchone()
    if existing_folder:
        folder_row = fetch_folder_row(connection, existing_folder["id"])
        connection.close()
        return jsonify({"message": "Folder already exists.", "folder": serialize_folder(folder_row)}), 200

    cursor = connection.execute(
        "INSERT INTO workspace_folders (user_id, parent_id, name) VALUES (?, ?, ?)",
        (current_user_id(), parent_id if parent_id not in (None, "") else None, name),
    )
    folder_id = cursor.lastrowid
    connection.commit()
    folder_row = fetch_folder_row(connection, folder_id)
    connection.close()
    return jsonify({"message": "Folder created.", "folder": serialize_folder(folder_row)}), 201


@app.route("/api/folders/<int:folder_id>", methods=["PUT"])
@login_required
def update_folder(folder_id):
    data = request.get_json(silent=True) or {}
    connection = get_db_connection()
    folder_row, permission = ensure_folder_access(connection, folder_id, current_user_id(), require_write=True)
    if not folder_row or not permission_allows_write(permission):
        connection.close()
        return jsonify({"error": "Folder not found or write access denied."}), 404

    name = sanitize_name(data.get("name") or folder_row["name"])
    parent_id = data.get("parent_id", folder_row["parent_id"])
    normalized_parent_id = None if parent_id in ("", None) else int(parent_id)
    if normalized_parent_id == folder_id:
        connection.close()
        return jsonify({"error": "A folder cannot be its own parent."}), 400
    if normalized_parent_id:
        parent_folder, parent_permission = ensure_folder_access(connection, normalized_parent_id, current_user_id(), require_write=True)
        if not parent_folder or not permission_allows_write(parent_permission):
            connection.close()
            return jsonify({"error": "You do not have write access to the target parent folder."}), 403
        if folder_id in folder_descendant_ids(connection, normalized_parent_id):
            connection.close()
            return jsonify({"error": "Cannot move a folder into one of its own descendants."}), 400

    duplicate_folder = connection.execute(
        """
        SELECT id
        FROM workspace_folders
        WHERE user_id = ?
          AND is_deleted = 0
          AND id != ?
          AND COALESCE(parent_id, -1) = ?
          AND LOWER(name) = LOWER(?)
        LIMIT 1
        """,
        (current_user_id(), folder_id, normalized_parent_key(normalized_parent_id), name),
    ).fetchone()
    if duplicate_folder:
        connection.close()
        return jsonify({"error": "A folder with that name already exists in the selected location."}), 409

    connection.execute(
        "UPDATE workspace_folders SET name = ?, parent_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (name, normalized_parent_id, folder_id),
    )
    connection.commit()
    updated_row = fetch_folder_row(connection, folder_id)
    connection.close()
    return jsonify({"message": "Folder updated.", "folder": serialize_folder(updated_row)})


@app.route("/api/folders/<int:folder_id>", methods=["DELETE"])
@login_required
def delete_folder(folder_id):
    connection = get_db_connection()
    folder_row, permission = ensure_folder_access(connection, folder_id, current_user_id(), require_write=True)
    if not folder_row or not permission_allows_write(permission):
        connection.close()
        return jsonify({"error": "Folder not found or write access denied."}), 404
    delete_folder_cascade(connection, folder_id)
    connection.commit()
    connection.close()
    return jsonify({"message": "Folder deleted."})


def _update_file_from_payload(file_id: int, data: dict, autosave: bool = False):
    connection = get_db_connection()
    file_row, permission = ensure_file_access(connection, file_id, current_user_id(), require_write=True)
    if not file_row or not permission_allows_write(permission):
        connection.close()
        return jsonify({"error": "File not found or write access denied."}), 404

    filename = sanitize_name(data.get("filename") or file_row["filename"])
    language = (data.get("language") or file_row["language"]).strip().lower()
    code = data.get("code") if data.get("code") is not None else file_row["code"]
    folder_id = data.get("folder_id", file_row["folder_id"])
    if language not in WORKSPACE_LANGUAGES:
        connection.close()
        return jsonify({"error": "Unsupported file type selected."}), 400

    normalized_folder_id = None if folder_id in ("", None) else int(folder_id)
    if normalized_folder_id:
        folder_row, folder_permission = ensure_folder_access(connection, normalized_folder_id, current_user_id(), require_write=True)
        if not folder_row or not permission_allows_write(folder_permission):
            connection.close()
            return jsonify({"error": "Target folder does not allow write access."}), 403

    duplicate_file = connection.execute(
        """
        SELECT id
        FROM code_files
        WHERE user_id = ?
          AND is_deleted = 0
          AND id != ?
          AND COALESCE(folder_id, -1) = ?
          AND LOWER(filename) = LOWER(?)
        LIMIT 1
        """,
        (current_user_id(), file_id, normalized_parent_key(normalized_folder_id), filename),
    ).fetchone()
    if duplicate_file:
        connection.close()
        return jsonify({"error": "A file with that name already exists in the selected folder."}), 409

    connection.execute(
        "UPDATE code_files SET filename = ?, language = ?, folder_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (filename, language, normalized_folder_id, file_id),
    )
    latest_version = connection.execute(
        "SELECT code FROM code_versions WHERE file_id = ? ORDER BY version_number DESC LIMIT 1",
        (file_id,),
    ).fetchone()
    if latest_version is None or latest_version["code"] != code or not autosave:
        current_row = fetch_file_row(connection, file_id)
        create_version(connection, current_row, code)
    connection.commit()
    payload = file_record_with_body(connection, file_id)
    connection.close()
    return jsonify({"message": "File updated.", "file": payload, "autosave": autosave})


@app.route("/api/files", methods=["POST"])
@login_required
def create_file():
    data = request.get_json(silent=True) or {}
    filename = sanitize_name(data.get("filename") or "")
    folder_id = data.get("folder_id")
    language = (data.get("language") or guess_language_from_filename(filename)).strip().lower()
    code = data.get("code") if data.get("code") is not None else ""
    if not filename:
        return jsonify({"error": "Filename is required."}), 400
    if language not in WORKSPACE_LANGUAGES:
        return jsonify({"error": "Unsupported file type selected."}), 400

    connection = get_db_connection()
    normalized_folder_id = None
    if folder_id not in (None, ""):
        folder_row, permission = ensure_folder_access(connection, int(folder_id), current_user_id(), require_write=True)
        if not folder_row or not permission_allows_write(permission):
            connection.close()
            return jsonify({"error": "Selected folder does not allow write access."}), 403
        normalized_folder_id = int(folder_id)

    existing_file = connection.execute(
        """
        SELECT id
        FROM code_files
        WHERE user_id = ?
          AND is_deleted = 0
          AND COALESCE(folder_id, -1) = ?
          AND LOWER(filename) = LOWER(?)
        ORDER BY id DESC
        LIMIT 1
        """,
        (current_user_id(), normalized_parent_key(normalized_folder_id), filename),
    ).fetchone()
    if existing_file:
        response_file = file_record_with_body(connection, existing_file["id"])
        connection.close()
        return jsonify({"message": "File already exists.", "file": response_file, "version_number": None, "storage_key": None}), 200

    cursor = connection.execute(
        "INSERT INTO code_files (user_id, folder_id, filename, language, code) VALUES (?, ?, ?, ?, ?)",
        (current_user_id(), normalized_folder_id, filename, language, code),
    )
    file_id = cursor.lastrowid
    file_row = fetch_file_row(connection, file_id)
    version_number, storage_key = create_version(connection, file_row, code)
    connection.commit()
    response_file = file_record_with_body(connection, file_id)
    connection.close()
    return jsonify({"message": "File created.", "file": response_file, "version_number": version_number, "storage_key": storage_key}), 201


@app.route("/api/files/<int:file_id>", methods=["GET"])
@login_required
def get_file(file_id):
    connection = get_db_connection()
    file_row, permission = ensure_file_access(connection, file_id, current_user_id())
    if not file_row or not permission:
        connection.close()
        return jsonify({"error": "File not found."}), 404
    payload = file_record_with_body(connection, file_id)
    payload["permission"] = permission
    connection.close()
    return jsonify({"file": payload})


@app.route("/api/files/<int:file_id>", methods=["PUT"])
@login_required
def update_file(file_id):
    data = request.get_json(silent=True) or {}
    return _update_file_from_payload(file_id, data, autosave=bool(data.get("autosave")))


@app.route("/api/files/<int:file_id>/autosave", methods=["POST"])
@login_required
def autosave_file(file_id):
    data = request.get_json(silent=True) or {}
    return _update_file_from_payload(file_id, data, autosave=True)


@app.route("/api/files/<int:file_id>", methods=["DELETE"])
@login_required
def delete_file(file_id):
    connection = get_db_connection()
    file_row, permission = ensure_file_access(connection, file_id, current_user_id(), require_write=True)
    if not file_row or permission != "write":
        connection.close()
        return jsonify({"error": "File not found or write access denied."}), 404
    connection.execute("UPDATE code_files SET is_deleted = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (file_id,))
    connection.commit()
    connection.close()
    return jsonify({"message": "File deleted."})


@app.route("/api/files/search", methods=["GET"])
@login_required
def search_files():
    query = (request.args.get("q") or "").strip().lower()
    if not query:
        return jsonify({"results": []})
    tree = list_workspace_tree(current_user_id())
    results = [item for item in tree["files"] if query in item["filename"].lower()]
    return jsonify({"results": results[:25]})


@app.route("/api/files/<int:file_id>/versions", methods=["GET"])
@login_required
def get_versions(file_id):
    connection = get_db_connection()
    file_row, permission = ensure_file_access(connection, file_id, current_user_id())
    if not file_row or not permission:
        connection.close()
        return jsonify({"error": "File not found."}), 404
    rows = connection.execute(
        "SELECT id, version_number, storage_key, created_at FROM code_versions WHERE file_id = ? ORDER BY version_number DESC",
        (file_id,),
    ).fetchall()
    connection.close()
    return jsonify({"versions": [dict(row) for row in rows]})


@app.route("/api/versions/<int:version_id>", methods=["GET"])
@login_required
def get_version(version_id):
    connection = get_db_connection()
    version_row = connection.execute(
        """
        SELECT cv.id, cv.file_id, cv.version_number, cv.code, cv.storage_key, cv.created_at
        FROM code_versions cv
        JOIN code_files cf ON cf.id = cv.file_id
        WHERE cv.id = ?
        """,
        (version_id,),
    ).fetchone()
    if not version_row:
        connection.close()
        return jsonify({"error": "Version not found."}), 404
    file_row, permission = ensure_file_access(connection, version_row["file_id"], current_user_id())
    if not file_row or not permission:
        connection.close()
        return jsonify({"error": "Version not found."}), 404

    code = version_row["code"]
    if version_row["storage_key"]:
        stored_code = storage_client.read_text(version_row["storage_key"])
        if stored_code:
            code = stored_code
    connection.close()
    return jsonify({"version": {"id": version_row["id"], "file_id": version_row["file_id"], "version_number": version_row["version_number"], "code": code, "created_at": version_row["created_at"]}})


@app.route("/api/shares", methods=["GET"])
@login_required
def list_shares():
    tree = list_workspace_tree(current_user_id())
    return jsonify({"shares": tree["shares"]})


@app.route("/api/shares", methods=["POST"])
@login_required
def create_share():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    resource_type = (data.get("resource_type") or "").strip().lower()
    resource_id = int(data.get("resource_id") or 0)
    permission = (data.get("permission") or "").strip().lower()
    if resource_type not in {"file", "folder"}:
        return jsonify({"error": "Share type must be `file` or `folder`."}), 400
    if permission not in PERMISSIONS:
        return jsonify({"error": "Permission must be `read`, `write`, or `both`."}), 400

    connection = get_db_connection()
    target_user = fetch_user(connection, username)
    if not target_user:
        connection.close()
        return jsonify({"error": "Target user not found."}), 404
    if target_user["id"] == current_user_id():
        connection.close()
        return jsonify({"error": "You already own this workspace item."}), 400

    if resource_type == "file":
        resource_row, resource_permission = ensure_file_access(connection, resource_id, current_user_id(), require_write=True)
    else:
        resource_row, resource_permission = ensure_folder_access(connection, resource_id, current_user_id(), require_write=True)
    if not resource_row or not permission_allows_write(resource_permission):
        connection.close()
        return jsonify({"error": "Only owners or writers can share this item."}), 403

    existing = connection.execute(
        """
        SELECT id FROM workspace_shares
        WHERE owner_user_id = ? AND target_user_id = ? AND resource_type = ? AND resource_id = ?
        """,
        (current_user_id(), target_user["id"], resource_type, resource_id),
    ).fetchone()
    if existing:
        connection.execute("UPDATE workspace_shares SET permission = ? WHERE id = ?", (permission, existing["id"]))
        share_id = existing["id"]
    else:
        cursor = connection.execute(
            """
            INSERT INTO workspace_shares (owner_user_id, target_user_id, resource_type, resource_id, permission)
            VALUES (?, ?, ?, ?, ?)
            """,
            (current_user_id(), target_user["id"], resource_type, resource_id, permission),
        )
        share_id = cursor.lastrowid
    connection.commit()
    share_row = connection.execute(
        """
        SELECT ws.id, ws.resource_type, ws.resource_id, ws.permission, ws.created_at,
               owner.username AS owner_username, target.username AS target_username
        FROM workspace_shares ws
        JOIN users owner ON owner.id = ws.owner_user_id
        JOIN users target ON target.id = ws.target_user_id
        WHERE ws.id = ?
        """,
        (share_id,),
    ).fetchone()
    connection.close()
    return jsonify({"message": "Share saved.", "share": dict(share_row)}), 201


@app.route("/api/shares/<int:share_id>", methods=["DELETE"])
@login_required
def delete_share(share_id):
    connection = get_db_connection()
    share_row = connection.execute("SELECT * FROM workspace_shares WHERE id = ?", (share_id,)).fetchone()
    if not share_row or share_row["owner_user_id"] != current_user_id():
        connection.close()
        return jsonify({"error": "Share not found."}), 404
    connection.execute("DELETE FROM workspace_shares WHERE id = ?", (share_id,))
    connection.commit()
    connection.close()
    return jsonify({"message": "Share removed."})


@app.route("/api/history", methods=["GET"])
@login_required
def history():
    return jsonify({"history": list_history_for_user(current_user_id(), limit=20)})


@app.route("/api/sync/state", methods=["GET", "PUT"])
@login_required
def sync_state():
    user_id = current_user_id()
    if request.method == "GET":
        return jsonify({"state": get_workspace_state(user_id)})
    data = request.get_json(silent=True) or {}
    payload = upsert_workspace_state(
        user_id,
        {
            "selected_file_id": int(data["selected_file_id"]) if data.get("selected_file_id") not in (None, "", "null") else None,
            "language": (data.get("language") or "python").strip().lower(),
            "editor_code": data.get("editor_code") or "",
            "syntax_code": data.get("syntax_code") or "",
            "stdin_text": data.get("stdin_text") or "",
            "theme": (data.get("theme") or "dark").strip().lower(),
        },
    )
    return jsonify({"message": "Workspace state synced.", "state": payload})


@app.route("/api/workspace-links", methods=["GET", "POST"])
@login_required
def workspace_links():
    user_id = current_user_id()
    connection = get_db_connection()
    if request.method == "GET":
        rows = connection.execute(
            """
            SELECT *
            FROM workspace_links
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 10
            """,
            (user_id,),
        ).fetchall()
        connection.close()
        return jsonify({"links": [normalize_workspace_link_row(row) for row in rows]})

    data = request.get_json(silent=True) or {}
    file_id = data.get("file_id")
    if file_id not in (None, "", "null"):
        file_row, permission = ensure_file_access(connection, int(file_id), user_id)
        if not file_row or not permission:
            connection.close()
            return jsonify({"error": "Selected file is unavailable."}), 403
        title = f"{file_row['filename']} workspace"
        normalized_file_id = int(file_id)
    else:
        owner_row = connection.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
        title = f"{owner_row['username'] if owner_row else 'Workspace'}'s workspace"
        normalized_file_id = None

    token = secrets.token_urlsafe(16)
    cursor = connection.execute(
        """
        INSERT INTO workspace_links (user_id, token, file_id, title)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, token, normalized_file_id, title),
    )
    link_id = cursor.lastrowid
    connection.commit()
    row = connection.execute("SELECT * FROM workspace_links WHERE id = ?", (link_id,)).fetchone()
    connection.close()
    return jsonify({"message": "Workspace link created.", "link": normalize_workspace_link_row(row), "url": f"/workspace-link/{token}"}), 201


@app.route("/api/deployments", methods=["GET", "POST"])
@login_required
def deployments():
    user_id = current_user_id()
    connection = get_db_connection()
    if request.method == "GET":
        rows = connection.execute(
            """
            SELECT *
            FROM deployments
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 10
            """,
            (user_id,),
        ).fetchall()
        connection.close()
        return jsonify({"deployments": [normalize_deployment_row(row) for row in rows]})

    data = request.get_json(silent=True) or {}
    language = (data.get("language") or "").strip().lower()
    code = data.get("content") or data.get("code") or ""
    file_id = data.get("file_id")
    if file_id not in (None, "", "null"):
        file_row, permission = ensure_file_access(connection, int(file_id), user_id)
        if not file_row or not permission:
            connection.close()
            return jsonify({"error": "Selected file is unavailable."}), 403
        code = file_record_with_body(connection, int(file_id))["code"]
        language = file_row["language"]
        normalized_file_id = int(file_id)
        title = file_row["filename"]
    else:
        normalized_file_id = None
        title = (data.get("title") or "Deployed app").strip() or "Deployed app"
    if language not in {"html", "javascript"}:
        connection.close()
        return jsonify({"error": "Only HTML and JavaScript apps can be deployed."}), 400
    token = secrets.token_urlsafe(16)
    cursor = connection.execute(
        """
        INSERT INTO deployments (user_id, token, file_id, language, title, content)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, token, normalized_file_id, language, title, code),
    )
    deployment_id = cursor.lastrowid
    connection.commit()
    row = connection.execute("SELECT * FROM deployments WHERE id = ?", (deployment_id,)).fetchone()
    connection.close()
    return jsonify({"message": "Deployment created.", "deployment": normalize_deployment_row(row), "url": f"/deploy/{token}"}), 201


@app.route("/workspace-link/<token>", methods=["GET"])
def public_workspace_link(token):
    connection = get_db_connection()
    link_row = connection.execute("SELECT * FROM workspace_links WHERE token = ?", (token,)).fetchone()
    if not link_row:
        connection.close()
        return "Workspace link not found.", 404
    payload = build_public_workspace_payload(link_row["user_id"], link_row["file_id"])
    connection.close()
    title = html_lib.escape(link_row["title"])
    selected = payload["selected_file"]
    files_markup = "".join(
        f"<li><span>{html_lib.escape(file['filename'])}</span><small>{html_lib.escape(file['language'])}</small></li>"
        for file in payload["tree"]["files"]
    ) or "<li>No files found.</li>"
    selected_markup = (
        f"<pre>{html_lib.escape(selected['code'])}</pre>" if selected else "<p>No file selected in this shared workspace.</p>"
    )
    page = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>{title}</title>
        <style>
            body {{ margin: 0; font-family: system-ui, sans-serif; background: #08111f; color: #e5eefc; padding: 24px; }}
            .card {{ max-width: 1100px; margin: 0 auto; background: #111827; border: 1px solid rgba(148,163,184,.18); border-radius: 20px; padding: 24px; }}
            ul {{ list-style: none; padding: 0; display: grid; gap: 10px; }}
            li {{ padding: 12px 14px; border: 1px solid rgba(148,163,184,.18); border-radius: 14px; background: rgba(25,34,56,.92); display:flex; justify-content:space-between; gap:12px; }}
            pre {{ white-space: pre-wrap; word-break: break-word; padding: 16px; border-radius: 14px; background: #020617; border: 1px solid rgba(148,163,184,.18); }}
            small {{ color: #90a1c2; }}
            a {{ color: #36cfc9; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h1>{title}</h1>
            <p>Shared workspace link</p>
            <h2>Files</h2>
            <ul>{files_markup}</ul>
            <h2>Selected File</h2>
            {selected_markup}
            <p><a href="/">Open Cloud IDE</a></p>
        </div>
    </body>
    </html>
    """
    return page


def build_deployment_document(language: str, content: str, title: str):
    if language == "html":
        return content
    escaped = html_lib.escape(content)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{html_lib.escape(title)}</title>
</head>
<body>
    <script>
{content}
    </script>
</body>
</html>"""


@app.route("/deploy/<token>", methods=["GET"])
def public_deployment(token):
    connection = get_db_connection()
    row = connection.execute("SELECT * FROM deployments WHERE token = ?", (token,)).fetchone()
    connection.close()
    if not row:
        return "Deployment not found.", 404
    document = build_deployment_document(row["language"], row["content"], row["title"])
    return document


@app.route("/api/monitor", methods=["GET"])
@login_required
def monitor():
    memory = psutil.virtual_memory()
    return jsonify(
        {
            "cpu_percent": round(psutil.cpu_percent(interval=0.1), 2),
            "memory_percent": round(memory.percent, 2),
            "memory_used_mb": round(memory.used / (1024 * 1024), 2),
            "memory_total_mb": round(memory.total / (1024 * 1024), 2),
            "servers": get_server_snapshot(),
            "storage_backend": storage_client.descriptor(),
            "docker_enabled": should_use_docker(),
        }
    )


@app.route("/run", methods=["POST"])
@login_required
def run_code():
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    language = (data.get("language") or "").strip().lower()
    user_input = data.get("input") or ""
    file_id = data.get("file_id")
    if not code:
        return jsonify({"output": "", "error": "Code cannot be empty.", "execution_time": 0, "suggestion": "Write or load some code before running."}), 400
    if language not in EXECUTABLE_LANGUAGES:
        return jsonify({"output": "", "error": "This file type is not executable.", "execution_time": 0, "suggestion": "Run Python, C, C++, Java, JavaScript, or HTML files."}), 400

    connection = get_db_connection()
    if file_id is not None:
        file_row, permission = ensure_file_access(connection, int(file_id), current_user_id())
        if not file_row or not permission:
            connection.close()
            return jsonify({"error": "Selected workspace file is unavailable."}), 403
    connection.close()

    result = execute_on_remote_service(code, language, user_input)
    record_execution(current_user_id(), int(file_id) if file_id else None, language, code, user_input, result)
    return jsonify(result), 200


@app.route("/api/bootstrap", methods=["POST"])
@login_required
def bootstrap_workspace():
    connection = get_db_connection()
    existing = connection.execute(
        "SELECT id FROM code_files WHERE user_id = ? AND is_deleted = 0 LIMIT 1",
        (current_user_id(),),
    ).fetchone()
    if existing:
        connection.close()
        return jsonify({"message": "Workspace already initialized."})

    cursor = connection.execute(
        "INSERT INTO code_files (user_id, folder_id, filename, language, code) VALUES (?, ?, ?, ?, ?)",
        (current_user_id(), None, DEFAULT_EDITOR_FILE["filename"], DEFAULT_EDITOR_FILE["language"], DEFAULT_EDITOR_FILE["code"]),
    )
    file_id = cursor.lastrowid
    file_row = fetch_file_row(connection, file_id)
    create_version(connection, file_row, DEFAULT_EDITOR_FILE["code"])
    connection.commit()
    connection.close()
    return jsonify({"message": "Workspace initialized."}), 201


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
