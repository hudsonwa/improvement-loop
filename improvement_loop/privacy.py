"""Privacy / secret scan with a user-supplied denylist.

The denylist is a plain text file kept OUTSIDE the repository (never commit it: it names the private things
you are protecting). One term per line; `#` starts a comment; a line `re:<regex>` is a regular expression,
anything else is a case-insensitive substring. A line in a scanned file containing `privacy:allow` is skipped
(use sparingly; reviewers see it).
"""
from __future__ import annotations

import ipaddress
import os
import re
import subprocess
from typing import Dict, Iterable, List, Optional, Tuple

SECRET_PATTERNS: List[Tuple[str, str]] = [
    ("private-key", r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY"),
    ("aws-access-key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("github-token", r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    ("slack-token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    ("anthropic-key", r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    ("openai-key", r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}"),
    ("google-api-key", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ("assigned-secret", r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)\b"
                        r"\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"),
    ("url-credentials", r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@"),
]
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
EMAIL_OK = re.compile(r"(?i)(?:noreply|no-reply)@|@(?:example\.(?:com|org|net)|users\.noreply\.github\.com)$")
IPV4 = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?![\w.])")
HOME_PATH = re.compile(r"(?:/Users/|/home/|C:\\Users\\)(?!<|\$|\{|USER|you|me\b|name\b)[A-Za-z0-9._-]+")
ALLOW = "privacy:allow"


def load_denylist(path: Optional[str]) -> List[Tuple[str, "re.Pattern[str]"]]:
    if not path:
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            rx = s[3:] if s.startswith("re:") else re.escape(s)
            out.append((f"denylist line {n}", re.compile(rx, re.I)))  # the label never echoes the term
    return out


def _ip_hit(text: str) -> bool:
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False  # version numbers like 1.2.3.999
    return not (ip.is_loopback or ip.is_unspecified or str(ip).startswith(("0.", "255.")) or
                ip in ipaddress.ip_network("192.0.2.0/24") or ip in ipaddress.ip_network("198.51.100.0/24") or
                ip in ipaddress.ip_network("203.0.113.0/24"))


def scan_text(name: str, text: str, deny) -> List[Dict[str, str]]:
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        if ALLOW in line:
            continue
        found: List[Tuple[str, str]] = []
        for kind, rx in SECRET_PATTERNS:
            for m in re.finditer(rx, line):
                found.append(("secret:" + kind, m.group(0)))
        for m in EMAIL.finditer(line):
            if not EMAIL_OK.search(m.group(0)):
                found.append(("email", m.group(0)))
        for m in IPV4.finditer(line):
            if _ip_hit(m.group(1)):
                found.append(("ip-address", m.group(1)))
        for m in HOME_PATH.finditer(line):
            found.append(("home-path", m.group(0)))
        for term, rx in deny:
            if rx.search(line):
                found.append(("denylist", term))
        for kind, what in found:
            hits.append({"file": name, "line": n, "kind": kind, "match": _redact(kind, what)})
    return hits


def _redact(kind: str, s: str) -> str:
    if kind.startswith("secret:"):
        return s[:4] + "…" + f"({len(s)} chars)"
    if kind == "denylist":
        return "<" + s + ">"
    return s


def git_files(repo: str) -> List[str]:
    """What a push could publish: tracked plus untracked-not-ignored files."""
    p = subprocess.run(["git", "-C", repo, "ls-files", "-co", "--exclude-standard", "-z"], capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode(errors="replace").strip())
    return [f for f in p.stdout.decode().split("\0") if f]


def git_history(repo: str, rev_range: str) -> str:
    """Full patch text plus commit metadata (author names/emails, messages) for a range."""
    p = subprocess.run(["git", "-C", repo, "log", "-p", "--format=commit %H%nAuthor: %an <%ae>%nCommitter: %cn <%ce>"
                        "%n%B", rev_range], capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode(errors="replace").strip())
    return p.stdout.decode("utf-8", errors="replace")


def new_hits(head: List[Dict[str, str]], base: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Hits in `head` that `base` did not already have: same file, kind and (redacted) match, counted, so moved
    lines are not new and an added copy of an old secret is."""
    from collections import Counter
    key = lambda h: (h["file"], h["kind"], h["match"])
    left = Counter(key(h) for h in base)
    out = []
    for h in head:
        if left[key(h)]:
            left[key(h)] -= 1
        else:
            out.append(h)
    return out


def scan_repo(repo: str, deny, files: Optional[Iterable[str]] = None, history: Optional[str] = None,
              skip: Iterable[str] = ()) -> List[Dict[str, str]]:
    hits = []
    for f in files if files is not None else git_files(repo):
        if f in skip:
            continue
        p = os.path.join(repo, f)
        if not os.path.isfile(p):
            continue
        with open(p, "rb") as fh:
            raw = fh.read()
        if b"\0" in raw[:4096]:
            hits += scan_text(f + " (file name)", f, deny)
            continue
        hits += scan_text(f + " (file name)", f, deny)
        hits += scan_text(f, raw.decode("utf-8", errors="replace"), deny)
    if history:
        hits += scan_text(f"git-log:{history}", git_history(repo, history), deny)
    return hits
