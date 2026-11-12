"""The loop's own explicit state machine.

States (one cycle):  critique -> verify -> rank -> plan -> issues -> build -> gate -> audit
then either the next cycle's `critique` or `done` (stop rule + owner approval).

Every transition is a named, guarded function of the files in the cycle directory and the recorded owner
approvals. `advance` refuses (with the list of unmet guards) instead of moving on. State lives in
`<project>/.improvement-loop/state.json`; nothing else is implicit.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any, Dict, List, Optional

from . import decisions as D
from . import findings as F
from . import lenses as L

STEPS = ("critique", "verify", "rank", "plan", "issues", "build", "gate", "audit")
TERMINAL = "done"
# The owner approval needed to LEAVE a step.
APPROVAL_TO_LEAVE = {"rank": "ranking", "plan": "plan", "gate": "release", "audit": "next"}
APPROVALS = tuple(APPROVAL_TO_LEAVE.values()) + ("stop",)

DEFAULT_CONFIG: Dict[str, Any] = {
    "mode": "full",                  # full | lite
    "max_cycles": 3,
    "stop_severity": 3,              # stop when no verified finding at or above this severity remains
    "lite_top_n": 3,
    "budget": {"minutes": 0, "cost": 0.0, "max_lanes": 4},   # 0 = unlimited
    "critics": None,                 # explicit critic list; overrides lens selection
    "lenses": None,                  # None = core + detected lenses (see lenses.py); or an explicit list
    "retired": [],                   # lenses never to run (e.g. after `loop lenses --scores`)
    "checks": [],                    # project commands whose failure becomes a finding (`loop check`)
    "metrics": None,                 # None = metrics.DEFAULT_METRICS
    "denylist": None,                # path to a denylist file OUTSIDE the repo; or $IMPROVEMENT_LOOP_DENYLIST
}


class LoopError(Exception):
    pass


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


class Loop:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.dir = os.path.join(self.root, ".improvement-loop")
        self.state_path = os.path.join(self.dir, "state.json")
        self.config_path = os.path.join(self.dir, "config.json")
        self.decisions_path = os.path.join(self.dir, "DECISIONS.md")
        self.fixed_path = os.path.join(self.dir, "fixed.json")

    # -------------------------------------------------------------- persistence

    @property
    def exists(self) -> bool:
        return os.path.exists(self.state_path)

    def init(self, mode: str = "full") -> Dict[str, Any]:
        if self.exists:
            raise LoopError(f"already initialised: {self.state_path}")
        if mode not in ("full", "lite"):
            raise LoopError("mode must be full or lite")
        os.makedirs(self.dir, exist_ok=True)
        cfg = dict(DEFAULT_CONFIG, mode=mode)
        _write_json(self.config_path, cfg)
        st = {"cycle": 1, "step": STEPS[0], "approvals": [], "budget_used": {"minutes": 0, "cost": 0.0},
              "history": [{"at": _now(), "event": "init", "cycle": 1, "step": STEPS[0]}], "lenses": {}}
        self._select_lenses(st)
        self._save(st)
        os.makedirs(os.path.join(self.cycle_dir(st), "findings"), exist_ok=True)
        return st

    def load(self) -> Dict[str, Any]:
        if not self.exists:
            raise LoopError("not initialised here; run `loop init` first")
        with open(self.state_path, encoding="utf-8") as fh:
            return json.load(fh)

    def config(self) -> Dict[str, Any]:
        cfg = dict(DEFAULT_CONFIG)
        if os.path.exists(self.config_path):
            with open(self.config_path, encoding="utf-8") as fh:
                cfg.update(json.load(fh))
        return cfg

    def _save(self, st: Dict[str, Any]) -> None:
        _write_json(self.state_path, st)

    def cycle_dir(self, st: Optional[Dict[str, Any]] = None) -> str:
        st = st or self.load()
        return os.path.join(self.dir, f"cycle-{st['cycle']}")

    def path(self, name: str, st: Optional[Dict[str, Any]] = None) -> str:
        return os.path.join(self.cycle_dir(st), name)

    # -------------------------------------------------------------- approvals and budget

    def approve(self, gate: str, by: str, note: str = "") -> Dict[str, Any]:
        st = self.load()
        if gate not in APPROVALS:
            raise LoopError(f"unknown approval '{gate}'; one of {', '.join(APPROVALS)}")
        needed = APPROVAL_TO_LEAVE.get(st["step"])
        if gate == "stop":
            if st["step"] != "audit":
                raise LoopError("'stop' can only be approved at the audit step")
        elif gate != needed:
            raise LoopError(f"step '{st['step']}' takes approval '{needed}', not '{gate}'"
                            if needed else f"step '{st['step']}' needs no approval")
        if not by.strip():
            raise LoopError("an approval needs the approver's name (--by)")
        st["approvals"].append({"cycle": st["cycle"], "gate": gate, "by": by.strip(), "at": _now(), "note": note})
        st["history"].append({"at": _now(), "event": f"approve:{gate}", "by": by.strip(), "cycle": st["cycle"]})
        self._save(st)
        return st

    def approved(self, st: Dict[str, Any], gate: str) -> bool:
        return any(a["gate"] == gate and a["cycle"] == st["cycle"] for a in st["approvals"])

    def spend(self, minutes: float = 0, cost: float = 0.0) -> Dict[str, Any]:
        if minutes < 0 or cost < 0:
            raise LoopError("budget spend cannot be negative")
        st = self.load()
        st["budget_used"]["minutes"] += minutes
        st["budget_used"]["cost"] = round(st["budget_used"]["cost"] + cost, 4)
        self._save(st)
        return st

    def budget_spent(self, st: Dict[str, Any]) -> List[str]:
        b, used = self.config()["budget"], st["budget_used"]
        out = []
        if b.get("minutes") and used["minutes"] >= b["minutes"]:
            out.append(f"time {used['minutes']}/{b['minutes']} min")
        if b.get("cost") and used["cost"] >= b["cost"]:
            out.append(f"cost {used['cost']}/{b['cost']}")
        return out

    def _select_lenses(self, st: Dict[str, Any]) -> None:
        """Fix this cycle's lenses once, when the cycle starts, so the critique guard cannot shift."""
        st.setdefault("lenses", {})[str(st["cycle"])] = [
            {"lens": n, "why": why} for n, why in L.select(self.root, self.config(), st["cycle"])]

    def lens_selection(self, st: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
        st = st or self.load()
        sel = st.get("lenses", {}).get(str(st["cycle"]))
        if sel is None:  # state written before lenses existed
            sel = [{"lens": n, "why": w} for n, w in L.select(self.root, self.config(), st["cycle"])]
        return sel

    def critics(self) -> List[str]:
        return [x["lens"] for x in self.lens_selection()]

    # -------------------------------------------------------------- guards

    def guards(self, st: Optional[Dict[str, Any]] = None) -> List[str]:
        """Unmet conditions for leaving the current step. Empty list = may advance."""
        st = st or self.load()
        step, unmet = st["step"], []
        if step == TERMINAL:
            return ["the loop is done; `loop init` a new one to start over"]
        fdir = self.path("findings", st)
        if step == "critique":
            have = {os.path.splitext(n)[0] for n in _ls(fdir, ".json")}
            for role in self.critics():
                if role not in have:
                    unmet.append(f"missing findings/{role}.json")
            for n in _ls(fdir, ".json"):
                try:
                    errs = F.validate_all(F.load(os.path.join(fdir, n)))
                except (ValueError, json.JSONDecodeError) as e:
                    errs = [str(e)]
                unmet += [f"findings/{n}: {e}" for e in errs[:5]]
        elif step == "verify":
            vp = self.path("verified.json", st)
            if not os.path.exists(vp):
                unmet.append("missing verified.json (run `loop verify`, then the verifier sets each verdict)")
            else:
                fs = F.load(vp)
                unmet += [f"verified.json: {e}" for e in F.validate_all(fs)[:5]]
                pending = [f["id"] for f in fs if f.get("verdict", "unverified") == "unverified"]
                if pending:
                    unmet.append(f"{len(pending)} finding(s) still unverified: {', '.join(pending[:8])}")
                if any("evidence_ok" not in f for f in fs):
                    unmet.append("evidence not checked for every finding (run `loop verify`)")
        elif step == "rank":
            if not os.path.exists(self.path("RANKED.md", st)):
                unmet.append("missing RANKED.md (run `loop rank`)")
        elif step == "plan":
            if not os.path.exists(self.path("plan.json", st)):
                unmet.append("missing plan.json (the planner writes it; see `loop prompt planner`)")
            else:
                from . import issues as I
                unmet += [f"plan.json: {e}" for e in I.validate_plan(I.load_plan(self.path("plan.json", st)))[:5]]
        elif step == "issues":
            if not _ls(self.path("issues", st), ".md"):
                unmet.append("no drafted issues (run `loop issues draft`)")
        elif step == "build":
            if not os.path.exists(self.path("BUILD.md", st)):
                unmet.append("missing BUILD.md (integrator's merge log: one line per merged lane)")
        elif step == "gate":
            for name, what in (("gate.json", "complexity gate (`loop gate`)"),
                               ("privacy.json", "privacy scan (`loop privacy`)")):
                p = self.path(name, st)
                if not os.path.exists(p):
                    unmet.append(f"{what} not run")
                elif not _read_json(p).get("pass"):
                    unmet.append(f"{what} did not pass")
        elif step == "audit":
            if not os.path.exists(self.path("AUDIT.md", st)):
                unmet.append("missing AUDIT.md (fresh critics; see `loop prompt auditor`)")
        gate = APPROVAL_TO_LEAVE.get(step)
        if gate and not self.approved(st, gate) and not (step == "audit" and self.approved(st, "stop")):
            unmet.append(f"owner approval '{gate}' not recorded (`loop approve {gate} --by NAME`)")
        return unmet

    def stop_reasons(self, st: Optional[Dict[str, Any]] = None) -> List[str]:
        """The stop rule: no verified finding >= stop_severity left, max cycles reached, or budget spent."""
        st = st or self.load()
        cfg, out = self.config(), []
        vp = self.path("verified.json", st)
        if os.path.exists(vp):
            ranked, _ = F.rank(F.load(vp), D.closed(self.decisions_path))
            if not any(f["severity"] >= cfg["stop_severity"] for f in ranked):
                out.append(f"no open verified finding with severity >= {cfg['stop_severity']}")
        if st["cycle"] >= cfg["max_cycles"]:
            out.append(f"cycle {st['cycle']} of max {cfg['max_cycles']}")
        out += [f"budget spent: {b}" for b in self.budget_spent(st)]
        return out

    # -------------------------------------------------------------- transitions

    def advance(self) -> Dict[str, Any]:
        st = self.load()
        unmet = self.guards(st)
        if unmet:
            raise LoopError("cannot leave '" + st["step"] + "':\n  - " + "\n  - ".join(unmet))
        i = STEPS.index(st["step"])
        if st["step"] == "audit":
            if self.approved(st, "stop"):
                st["step"] = TERMINAL
            else:
                st["cycle"] += 1
                st["step"] = STEPS[0]
                self._select_lenses(st)
                os.makedirs(os.path.join(self.cycle_dir(st), "findings"), exist_ok=True)
        else:
            st["step"] = STEPS[i + 1]
        st["history"].append({"at": _now(), "event": "advance", "cycle": st["cycle"], "step": st["step"]})
        self._save(st)
        return st


def _ls(d: str, ext: str) -> List[str]:
    return sorted(n for n in os.listdir(d) if n.endswith(ext)) if os.path.isdir(d) else []


def _read_json(p: str) -> Dict[str, Any]:
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def _write_json(p: str, data: Any) -> None:
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, p)
