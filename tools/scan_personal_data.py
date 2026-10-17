"""Scan tracked/staged files for personal data before committing.

Checks for e-mail addresses, user home paths, the current OS user name and any
extra words listed (one per line) in the untracked file `.personal-words`.
Exits 1 if anything is found.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED_EMAILS = {"noreply@anthropic.com"}
ALLOWED_EMAIL_SUFFIXES = ("@users.noreply.github.com", "@example.com")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
HOME_PATH_RE = re.compile(r"(?i)([A-Z]:[\/]+Users[\/]+|/Users/|/home/)(?!<|\{|\$|%)[^\/\s\"'`]+")
SKIP_SUFFIXES = {".png", ".ico", ".jpg", ".jpeg", ".onnx", ".zip"}


def list_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [ROOT / p for p in out.splitlines() if p]


def forbidden_words() -> list[str]:
    words = []
    user = os.environ.get("USERNAME") or os.environ.get("USER")
    if user and len(user) >= 3:
        words.append(user)
    extra = ROOT / ".personal-words"
    if extra.exists():
        words += [w.strip() for w in extra.read_text(encoding="utf-8").splitlines() if w.strip()]
    return words


def scan_text(text: str, words: list[str]) -> list[str]:
    hits = []
    for m in EMAIL_RE.finditer(text):
        email = m.group(0)
        if email in ALLOWED_EMAILS or email.endswith(ALLOWED_EMAIL_SUFFIXES):
            continue
        hits.append(f"e-mail: {email}")
    for m in HOME_PATH_RE.finditer(text):
        hits.append(f"home path: {m.group(0)}")
    for w in words:
        if re.search(rf"(?i)\b{re.escape(w)}\b", text):
            hits.append(f"personal word: {w}")
    return hits


def main() -> int:
    words = forbidden_words()
    found = False
    for path in list_files():
        if path.suffix.lower() in SKIP_SUFFIXES or not path.is_file() or path == Path(__file__).resolve():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for hit in scan_text(text, words):
            found = True
            print(f"{path.relative_to(ROOT)}: {hit}")
    if found:
        print("Personal data found - remove it before committing.")
        return 1
    print("No personal data found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
