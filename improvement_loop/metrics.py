"""Configurable metrics and the complexity gate.

A metric is a dict:
    {"key": "product_loc", "kind": "loc", "include": ["**/*.py"], "exclude": [...], "gated": true,
     "higher_is_worse": true, "target": "<= 5000", "pattern": "<regex, for kind=regex>", "file": "<for kind=lines>"}
kinds: files (count of matching files), loc (non-blank lines), regex (matches of `pattern`), lines (lines in `file`),
bench (median of `repeat` runs of `cmd`, the number captured by `parse`; worse only beyond `tolerance`, default 5%),
surface (the set of lines `cmd` prints, e.g. CLI commands or API paths; any removed line is worse).
bench and surface run their command in a temporary checkout of each ref (or the working tree for WORKTREE).

Sources: a git ref (read with `git ls-tree` / `git cat-file`, never the working tree) or WORKTREE (tracked plus
untracked-but-not-ignored files). Tests and docs are excluded from product metrics by default.
"""
from __future__ import annotations

import contextlib
import fnmatch
import os
import shutil
import statistics
import tempfile
import re
import subprocess
from typing import Any, Dict, Iterator, List, Optional, Tuple

WORKTREE = "WORKTREE"
NON_PRODUCT = ["tests/**", "test/**", "**/tests/**", "**/test/**", "**/test_*.py", "**/*_test.py",
               "**/*.test.*", "**/*.spec.*", "docs/**", "**/*.md", "**/*.rst", "**/*.txt", "examples/**",
               ".github/**", ".improvement-loop/**", "LICENSE*"]
CODE = ["**/*.py", "**/*.js", "**/*.mjs", "**/*.ts", "**/*.tsx", "**/*.jsx", "**/*.go", "**/*.rs", "**/*.java",
        "**/*.kt", "**/*.swift", "**/*.rb", "**/*.php", "**/*.c", "**/*.h", "**/*.cc", "**/*.cpp", "**/*.cs",
        "**/*.sh", "**/*.lua", "**/*.scala"]
DEFAULT_METRICS: List[Dict[str, Any]] = [
    {"key": "product_files", "kind": "files", "include": CODE, "exclude": NON_PRODUCT, "gated": True},
    {"key": "product_loc", "kind": "loc", "include": CODE, "exclude": NON_PRODUCT, "gated": True,
     "target": "goes down per cycle"},
    {"key": "background_actors", "kind": "regex", "include": CODE, "exclude": NON_PRODUCT, "gated": True,
     "pattern": r"threading\.(?:Thread|Timer)\(|setInterval\(|\bgo func\(|asyncio\.create_task\(|"
                r"multiprocessing\.Process\(|spawn\("},
    {"key": "listeners", "kind": "regex", "include": CODE, "exclude": NON_PRODUCT, "gated": True,
     "pattern": r"\.listen\(|HTTPServer\(|create_server\(|serve_forever\(|uvicorn\.run\(|\.bind\(\("},
    {"key": "readme_lines", "kind": "lines", "file": "README.md", "gated": False},
    {"key": "test_files", "kind": "files", "include": ["tests/**", "test/**", "**/test_*.py", "**/*_test.*",
                                                      "**/*.test.*", "**/*.spec.*"], "gated": False,
     "higher_is_worse": False},
]
KINDS = ("files", "loc", "regex", "lines", "bench", "surface")
RUN_KINDS = ("bench", "surface")


def match(path: str, globs: List[str]) -> bool:
    """fnmatch where a leading `**/` also matches the top level."""
    for g in globs:
        if fnmatch.fnmatch(path, g) or (g.startswith("**/") and fnmatch.fnmatch(path, g[3:])):
            return True
    return False


def validate_metric(m: Any) -> List[str]:
    if not isinstance(m, dict) or not isinstance(m.get("key"), str):
        return ["metric needs a string 'key'"]
    errs = []
    if m.get("kind") not in KINDS:
        errs.append(f"{m['key']}: kind must be one of {', '.join(KINDS)}")
    if m.get("kind") == "regex":
        try:
            re.compile(m.get("pattern", ""))
        except re.error as e:
            errs.append(f"{m['key']}: bad pattern: {e}")
        if not m.get("pattern"):
            errs.append(f"{m['key']}: regex metric needs 'pattern'")
    if m.get("kind") == "lines" and not m.get("file"):
        errs.append(f"{m['key']}: lines metric needs 'file'")
    if m.get("kind") in RUN_KINDS and not (isinstance(m.get("cmd"), list) and m["cmd"]):
        errs.append(f"{m['key']}: {m.get('kind')} metric needs 'cmd' (a list, no shell)")
    if m.get("kind") == "bench":
        try:
            if re.compile(m.get("parse", "")).groups < 1:
                errs.append(f"{m['key']}: bench 'parse' needs one capture group for the number")
        except re.error as e:
            errs.append(f"{m['key']}: bad parse: {e}")
    if m.get("kind") in ("files", "loc", "regex") and not m.get("include"):
        errs.append(f"{m['key']}: needs 'include' globs")
    return errs


class Source:
    def __init__(self, repo: str, ref: str):
        self.repo, self.ref = repo, ref

    def _git(self, *args: str, data: Optional[bytes] = None) -> bytes:
        p = subprocess.run(["git", "-C", self.repo, *args], input=data, capture_output=True)
        if p.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)}: {p.stderr.decode(errors='replace').strip()}")
        return p.stdout

    def files(self) -> List[str]:
        if self.ref == WORKTREE:
            out = self._git("ls-files", "-co", "--exclude-standard", "-z")
        else:
            out = self._git("ls-tree", "-r", "--name-only", "-z", self.ref)
        return [f for f in out.decode().split("\0") if f]

    def read(self, paths: List[str]) -> Dict[str, str]:
        if not paths:
            return {}
        if self.ref == WORKTREE:
            out = {}
            for p in paths:
                try:
                    with open(f"{self.repo}/{p}", encoding="utf-8", errors="replace") as fh:
                        out[p] = fh.read()
                except (OSError, IsADirectoryError):
                    pass
            return out
        buf = self._git("cat-file", "--batch", data="".join(f"{self.ref}:{p}\n" for p in paths).encode())
        out, i = {}, 0
        for p in paths:
            nl = buf.index(b"\n", i)
            header = buf[i:nl].decode()
            i = nl + 1
            if header.endswith("missing"):
                continue
            size = int(header.split()[2])
            out[p] = buf[i:i + size].decode("utf-8", errors="replace")
            i += size + 1
        return out


@contextlib.contextmanager
def checkout(repo: str, ref: str) -> Iterator[str]:
    """A directory holding `ref` (a temporary detached worktree), or the repo itself for WORKTREE."""
    if ref == WORKTREE:
        yield repo
        return
    root = os.path.join(os.path.dirname(os.path.abspath(repo)), ".improvement-loop.noindex")  # same drive; no Spotlight
    os.makedirs(root, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="ref-", dir=root)
    wt = os.path.join(tmp, "wt")
    p = subprocess.run(["git", "-C", repo, "worktree", "add", "--detach", wt, ref], capture_output=True)
    if p.returncode:
        raise RuntimeError(f"cannot check out {ref}: {p.stderr.decode(errors='replace').strip()}")
    try:
        yield wt
    finally:
        subprocess.run(["git", "-C", repo, "worktree", "remove", "--force", wt], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


def run_metric(cwd: str, m: Dict[str, Any]) -> Any:
    """bench: median of the parsed number over `repeat` runs; surface: sorted unique output lines."""
    limit = int(m.get("timeout", 600))
    if m["kind"] == "surface":
        p = subprocess.run(m["cmd"], cwd=cwd, capture_output=True, timeout=limit)
        if p.returncode:
            raise RuntimeError(f"{m['key']}: `{' '.join(m['cmd'])}` exit {p.returncode}")
        return sorted({ln.strip() for ln in p.stdout.decode("utf-8", errors="replace").splitlines() if ln.strip()})
    rx, vals = re.compile(m["parse"]), []
    for _ in range(max(1, int(m.get("repeat", 5)))):
        p = subprocess.run(m["cmd"], cwd=cwd, capture_output=True, timeout=limit)
        hit = rx.search((p.stdout + p.stderr).decode("utf-8", errors="replace"))
        if p.returncode or not hit:
            raise RuntimeError(f"{m['key']}: `{' '.join(m['cmd'])}` exit {p.returncode}, number found: {bool(hit)}")
        vals.append(float(hit.group(1)))
    return statistics.median(vals)


def collect(source: Source, metrics: List[Dict[str, Any]]) -> Dict[str, Any]:
    runs = [m for m in metrics if m["kind"] in RUN_KINDS]
    metrics = [m for m in metrics if m["kind"] not in RUN_KINDS]
    vals: Dict[str, Any] = {}
    if runs:
        with checkout(source.repo, source.ref) as cwd:
            for m in runs:
                vals[m["key"]] = run_metric(cwd, m)
    files = source.files()
    sel = {}
    need = set()
    for m in metrics:
        if m["kind"] == "lines":
            chosen = [m["file"]] if m["file"] in files else []
        else:
            chosen = [f for f in files if match(f, m["include"]) and not match(f, m.get("exclude", []))]
        sel[m["key"]] = chosen
        if m["kind"] != "files":
            need.update(chosen)
    text = source.read(sorted(need))
    for m in metrics:
        chosen = sel[m["key"]]
        if m["kind"] == "files":
            vals[m["key"]] = len(chosen)
        elif m["kind"] == "loc":
            vals[m["key"]] = sum(1 for p in chosen for ln in text.get(p, "").splitlines() if ln.strip())
        elif m["kind"] == "lines":
            vals[m["key"]] = sum(len(text.get(p, "").splitlines()) for p in chosen)
        else:
            rx = re.compile(m["pattern"])
            vals[m["key"]] = sum(len(rx.findall(text.get(p, ""))) for p in chosen)
    return vals


def read_justifications(text: str) -> Dict[str, str]:
    """`metric_key: why` per line (leading '- ' allowed). Empty reasons do not count."""
    out = {}
    for line in text.splitlines():
        s = line.strip().lstrip("-*").strip()
        m = re.match(r"^`?([A-Za-z0-9_.-]+)`?\s*:\s*(\S.*)$", s)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def gate(metrics: List[Dict[str, Any]], base: Dict[str, int], head: Dict[str, int],
         just: Dict[str, str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    rows, fails = [], []
    for m in metrics:
        k, a, b = m["key"], base.get(m["key"], 0), head.get(m["key"], 0)
        hiw = m.get("higher_is_worse", True)
        note = ""
        if m.get("kind") == "surface":
            removed, added = sorted(set(a or []) - set(b or [])), sorted(set(b or []) - set(a or []))
            worse, better = bool(removed), False
            note = (" removed: " + ", ".join(removed[:8]) if removed else "") + (f" (+{len(added)} added)" if added else "")
            a, b = len(a or []), len(b or [])
        elif m.get("kind") == "bench":
            tol = float(m.get("tolerance", 0.05)) * abs(a)
            worse, better = (b > a + tol, b < a - tol) if hiw else (b < a - tol, b > a + tol)
        else:
            worse, better = (b > a, b < a) if hiw else (b < a, b > a)
        if not worse:
            v = "better" if better else "same"
        elif not m.get("gated"):
            v = "worse (info)"
        elif k in just:
            v = "worse, justified"
        else:
            v = "WORSE: FAIL"
            fails.append(k)
        rows.append({"key": k, "gated": bool(m.get("gated")), "base": a, "head": b, "delta": round(b - a, 4),
                     "target": m.get("target", ""), "verdict": v + note, "justification": just.get(k, "")})
    return rows, fails


def markdown(rows: List[Dict[str, Any]], base: str, head: str) -> str:
    out = [f"| Metric | Gated | {base} | {head} | Delta | Target | Verdict |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        d = ("+" if r["delta"] > 0 else "") + str(r["delta"])
        verdict = r["verdict"] + (f" ({r['justification']})" if r["justification"] else "")
        out.append(f"| {r['key']} | {'yes' if r['gated'] else 'no'} | {r['base']} | {r['head']} | {d} | "
                   f"{r['target'] or '-'} | {verdict} |")
    return "\n".join(out) + "\n"
