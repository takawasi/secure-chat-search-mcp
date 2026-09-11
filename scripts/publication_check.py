"""公開対象の簡易点検。

Library保存版で有効だった公開前スキャンを、mainの配置と証跡形式へ
合わせたもの。秘密・実データ・壊れた相対リンクを完全に判定するもの
ではないため、目視確認とCIを置き換えない。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / ".verification" / "publication-check.json"
IGNORED_DIRS = {
    ".git", ".venv", ".venv311", ".data", ".secrets", "__pycache__",
    ".pytest_cache", ".verification", "build", "dist", "htmlcov",
}
TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".html", ".css", ".js", ".json", ".yaml",
    ".yml", ".toml", ".sh", ".ps1", ".csv", ".example",
}
SECRET_PATTERNS = {
    "private_key": re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\s+[A-Za-z0-9+/=]{40,}"
    ),
    "github_token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"),
    "openai_project_key": re.compile(r"\bsk-proj-[A-Za-z0-9_-]{30,}\b"),
}
MARKDOWN_LINK = re.compile(r"!?\[[^\]\n]+\]\(([^)\n]+)\)")


def ignored(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    return any(part in IGNORED_DIRS or part.endswith(".egg-info") for part in relative.parts)


def main() -> None:
    issues: list[dict[str, str]] = []
    scanned = 0
    links = 0
    excluded = 0

    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        if ignored(path):
            excluded += 1
            continue
        relative = str(path.relative_to(ROOT))
        if path.is_symlink():
            issues.append({"path": relative, "type": "symlink"})
            continue
        if path.name == ".env" or path.suffix in {".pem", ".key", ".db", ".sqlite", ".sqlite3"}:
            issues.append({"path": relative, "type": "forbidden_file"})
            continue
        if path.suffix not in TEXT_SUFFIXES and path.name not in {"Dockerfile", ".gitignore", ".dockerignore"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            issues.append({"path": relative, "type": "non_utf8_text"})
            continue
        scanned += 1
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                issues.append({"path": relative, "type": name})
        if path.suffix == ".md":
            for target in MARKDOWN_LINK.findall(text):
                target = target.split(' "', 1)[0].strip().strip("<>")
                parsed = urlsplit(target)
                if parsed.scheme or parsed.netloc or target.startswith("#"):
                    continue
                links += 1
                destination = (path.parent / unquote(parsed.path)).resolve()
                if not destination.exists():
                    issues.append({"path": relative, "type": "missing_link", "target": target})

    result = {
        "status": "passed" if not issues else "failed",
        "scanned_text_files": scanned,
        "checked_relative_links": links,
        "excluded_runtime_files": excluded,
        "issues": issues,
        "notice": "既知形式の簡易検査です。秘密・個人情報の目視確認、依存脆弱性、ライセンス確認は別途必要です。",
    }
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if issues:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
