"""Merge raw extraction output into a graph: projects, methods, and what connects them.

The extraction runs units in parallel, so the same project shows up under several names
and the same method under many phrasings. One Claude call ("consolidate") maps all raw
project names to ~20-40 real projects and all raw "how" items to a small vocabulary of
methods. The mapping is cached in state/consolidated.json by a hash of its input, so it
is only redone when new activities arrive. The graph is then built locally:
  project nodes  (size = activities, colour = life area, kind = private/work)
  method nodes   (size = number of projects using it)
  edges          project -- method
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from .config import Config
from .extract import LIFE_AREAS, ActivityStore

SYSTEM = """You organise the extracted activity log of one person into a clean map.

1. PROJECTS: group the raw project names into real projects or themes (aim for 20-40).
   Merge sub-parts ("Recipe app login", "Recipe app search" -> "Recipe app"). Group
   small one-off work tasks into themes ("Reporting", "Customer emails",
   "Office tools"); group small private questions into themes ("Household",
   "Health & food"). kind = "job" for work done for an employer, "private" otherwise.
   Every raw name must appear in exactly one project.
2. METHODS: turn the numbered raw "how" items into a vocabulary of 15-30 reusable
   methods that show HOW this person works (e.g. "Plan before building",
   "Small reversible steps", "Test with real data", "Parallel AI sessions").
   Name the method, not the tool, unless the tool is the point. Assign each raw item
   index to at most one method; leave out items that are trivial ("asked Claude").
3. HEADLINES: 4-7 broad headlines that sum up this person's life and work from the
   content of the projects (the outermost zoom level of their map), e.g. "Day job",
   "Building tools with AI agents". Every project gets exactly one headline.
   If PREVIOUS_HEADLINES are given, reuse them where they still fit so the map stays
   stable; add, merge or rename only when the content clearly calls for it.
Write names and summaries in English, summaries one sentence."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "projects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "area": {"type": "string", "enum": LIFE_AREAS},
                    "kind": {"type": "string", "enum": ["private", "job"]},
                    "summary": {"type": "string"},
                    "headline": {"type": "string"},
                    "raw": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "area", "kind", "summary", "headline", "raw"],
                "additionalProperties": False,
            },
        },
        "methods": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "summary": {"type": "string"},
                    "items": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["name", "summary", "items"],
                "additionalProperties": False,
            },
        },
        "headlines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "summary": {"type": "string"}},
                "required": ["name", "summary"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["projects", "methods", "headlines"],
    "additionalProperties": False,
}


def _activities(cfg: Config) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(unit row, activity) pairs in a stable order."""
    rows = sorted(ActivityStore(cfg).rows.values(), key=lambda r: r["unit_id"])
    return [(r, a) for r in rows for a in r["activities"]]


class MergeInput:
    """What the merge step needs: the payload for the model and what to map back."""

    def __init__(self, cfg: Config):
        acts = _activities(cfg)
        self.raw_projects: dict[str, str] = {}
        for _, a in acts:
            self.raw_projects.setdefault(a["project"], a["life_area"])
        self.hows = sorted({h.strip() for _, a in acts for h in a["how"] if h.strip()})
        self.payload: dict[str, Any] = {
            "raw_projects": [
                {"name": n, "area": self.raw_projects[n]} for n in sorted(self.raw_projects)
            ],
            "how_items": [f"{i}: {h}" for i, h in enumerate(self.hows)],
        }
        self.digest = hashlib.sha256(
            json.dumps(self.payload, ensure_ascii=False).encode()
        ).hexdigest()[:16]
        self.path = cfg.state_dir / "consolidated.json"
        self.cached = (
            json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {}
        )
        if self.cached.get("headlines"):
            self.payload["previous_headlines"] = [h["name"] for h in self.cached["headlines"]]

    @property
    def fresh(self) -> bool:
        return self.cached.get("hash") == self.digest and bool(self.cached.get("headlines"))

    @property
    def text(self) -> str:
        return json.dumps(self.payload, ensure_ascii=False)


def validate_merge(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return ["expected an object"]
    errs = [f"missing '{k}'" for k in ("projects", "methods", "headlines") if k not in data]
    for p in data.get("projects", []):
        for k in ("name", "area", "kind", "summary", "headline", "raw"):
            if k not in p:
                errs.append(f"project {p.get('name', '?')}: missing '{k}'")
        if p.get("area") not in LIFE_AREAS:
            errs.append(f"project {p.get('name', '?')}: bad area")
        if p.get("kind") not in ("private", "job"):
            errs.append(f"project {p.get('name', '?')}: kind must be private or job")
    for m in data.get("methods", []):
        if not isinstance(m.get("items"), list) or not all(isinstance(i, int) for i in m["items"]):
            errs.append(f"method {m.get('name', '?')}: 'items' must be a list of integers")
    return errs


def finalize_merge(mi: MergeInput, data: dict[str, Any]) -> dict[str, Any]:
    """Map the model's answer back onto every raw name and method item, then save."""
    covered = {r for p in data["projects"] for r in p["raw"]}
    for name, area in mi.raw_projects.items():
        if name not in covered:  # nothing silently vanishes
            data["projects"].append(
                {
                    "name": name,
                    "area": area,
                    "kind": "job" if area == "work" else "private",
                    "summary": "",
                    "headline": "",
                    "raw": [name],
                }  # fmt: skip
            )
    method_of = {}
    for m in data["methods"]:
        for i in m.pop("items"):
            if 0 <= i < len(mi.hows):
                method_of[mi.hows[i]] = m["name"]
    out = {
        "hash": mi.digest,
        "projects": data["projects"],
        "methods": data["methods"],
        "headlines": data.get("headlines", []),
        "method_of": method_of,
    }
    mi.path.parent.mkdir(parents=True, exist_ok=True)
    mi.path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def consolidate(
    cfg: Config, client: Any = None, force: bool = False, engine: str = "api"
) -> dict[str, Any]:
    """Merge via the Claude API or a local model (Claude Code uses handoff.py)."""
    mi = MergeInput(cfg)
    if mi.fresh and not force:
        return mi.cached
    if engine == "local":
        from .llm import LocalLLM

        data = LocalLLM(cfg).chat_json(
            SYSTEM + "\n\nAnswer with JSON matching this schema:\n" + json.dumps(SCHEMA), mi.text
        )
    else:
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        with client.beta.messages.stream(
            model=cfg.get("claude.model", "claude-opus-5-5"),
            max_tokens=32000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=SYSTEM,
            messages=[{"role": "user", "content": mi.text}],
        ) as stream:
            resp = stream.get_final_message()
        if resp.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"consolidation stopped: {resp.stop_reason}")
        data = json.loads("".join(b.text for b in resp.content if b.type == "text"))
    if errs := validate_merge(data):
        raise RuntimeError("merge answer invalid: " + "; ".join(errs[:3]))
    return finalize_merge(mi, data)


def build_graph(cfg: Config, merged: dict[str, Any] | None = None) -> dict[str, Any]:
    """Nodes and edges for the UI, plus per-project activity lists for drafting."""
    if merged is None:
        path = cfg.state_dir / "consolidated.json"
        merged = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    if not merged:
        raise RuntimeError("Nothing merged yet. Run: tessera merge")
    proj_of = {r: p["name"] for p in merged["projects"] for r in p["raw"]}
    meta = {p["name"]: p for p in merged["projects"]}
    method_meta = {m["name"]: m for m in merged["methods"]}

    acts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    edges: dict[tuple[str, str], int] = defaultdict(int)
    idx: dict[str, int] = defaultdict(int)
    for row, a in _activities(cfg):
        p = proj_of.get(a["project"], a["project"])
        i = idx[row["unit_id"]]
        idx[row["unit_id"]] += 1
        methods = sorted({m for h in a["how"] if (m := merged["method_of"].get(h.strip()))})
        item = {
            "id": f"{row['unit_id']}#{i}",
            "methods": methods,
            "unit": row["unit_id"],
            "source": row["source"],
            "first": row["first"][:10],
            "last": row["last"][:10],
            "raw_project": a["project"],
            **{k: a[k] for k in ("what", "how", "outcome", "lesson", "public_ok", "story")},
        }
        acts[p].append(item)
        for h in a["how"]:
            if m := merged["method_of"].get(h.strip()):
                edges[(p, m)] += 1

    nodes = []
    for name, items in acts.items():
        m = meta.get(name, {"area": "other", "kind": "private", "summary": ""})
        nodes.append({
            "id": f"p:{name}", "label": name, "type": "project", "area": m["area"],
            "kind": m["kind"], "summary": m["summary"], "size": len(items),
            "headline": m.get("headline", ""),
            "story": max(i["story"] for i in items),
            "public": sum(1 for i in items if i["public_ok"]),
            "first": min(i["first"] for i in items), "last": max(i["last"] for i in items),
        })  # fmt: skip
    used_methods: dict[str, set[str]] = defaultdict(set)
    for p, m in edges:
        used_methods[m].add(p)
    for m, ps in used_methods.items():
        nodes.append({
            "id": f"m:{m}", "label": m, "type": "method", "size": len(ps),
            "summary": method_meta.get(m, {}).get("summary", ""),
        })  # fmt: skip
    return {
        "tree": build_tree(nodes, acts, merged.get("headlines", [])),
        "methods": [
            {
                "name": m,
                "summary": method_meta.get(m, {}).get("summary", ""),
                "projects": len(ps),
                "activities": sum(1 for v in acts.values() for a in v if m in a["methods"]),
            }
            for m, ps in sorted(used_methods.items(), key=lambda kv: -len(kv[1]))
        ],  # fmt: skip
        "nodes": nodes,
        "edges": [{"from": f"p:{p}", "to": f"m:{m}", "weight": w} for (p, m), w in edges.items()],
        "activities": acts,
        "stats": {
            "projects": len(acts),
            "methods": len(used_methods),
            "activities": sum(len(v) for v in acts.values()),
            "areas": sorted({n["area"] for n in nodes if n["type"] == "project"}),
        },
    }


# Fallback headlines (merges made before AI headlines existed). Employer work -> "Work".
HEADLINES = {
    "work": "Work",
    "business": "Business",
    "software": "Building things",
    "ai": "Building things",
    "making": "Building things",
    "science": "Learning and curiosity",
    "learning": "Learning and curiosity",
    "creative": "Learning and curiosity",
}


def headline(project: dict[str, Any]) -> str:
    """AI-chosen headline; static fallback by life area for older merges."""
    if project.get("headline"):
        return project["headline"]
    return "Work" if project.get("kind") == "job" else HEADLINES.get(project["area"], "Life")


def build_tree(
    nodes: list[dict[str, Any]],
    acts: dict[str, list[dict[str, Any]]],
    headlines: list[dict[str, str]] | None = None,
) -> dict:
    """root -> headline -> project -> activity, for the zoomable map."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for n in nodes:
        if n["type"] != "project":
            continue
        leaves = [
            {"name": a["what"], "id": a["id"], "type": "activity", "value": 1 + a["story"],
             "public": a["public_ok"], "story": a["story"], "methods": a["methods"],
             "how": a["how"], "outcome": a["outcome"], "lesson": a["lesson"],
             "first": a["first"], "last": a["last"], "source": a["source"]}
            for a in acts[n["label"]]
        ]  # fmt: skip
        groups[headline(n)].append({
            "name": n["label"], "type": "project", "kind": n["kind"], "area": n["area"],
            "summary": n["summary"], "first": n["first"], "last": n["last"],
            "children": leaves,
        })  # fmt: skip
    summaries = {h["name"]: h["summary"] for h in headlines or []}
    return {
        "name": "Everything",
        "type": "root",
        "children": [
            {
                "name": g,
                "type": "headline",
                "summary": summaries.get(g, ""),
                "children": sorted(ps, key=lambda p: p["name"]),
            }  # fmt: skip
            for g, ps in sorted(groups.items())
        ],
    }


def share_view(g: dict[str, Any]) -> dict[str, Any]:
    """The map as it may be shown to others: no work projects, only activities marked
    shareable (public_ok), no AI-written summaries. Hidden parts never reach the page."""
    tree = {"name": g["tree"]["name"], "type": "root", "children": []}
    keep_projects: set[str] = set()
    for h in g["tree"]["children"]:
        projects = []
        for p in h["children"]:
            if p.get("kind") == "job":
                continue
            leaves = [a for a in p["children"] if a.get("public")]
            if leaves:
                projects.append({**p, "summary": "", "children": leaves})
                keep_projects.add(p["name"])
        if projects:
            tree["children"].append({**h, "summary": "", "children": projects})
    acts = {p: [a for a in items if a["public_ok"]]
            for p, items in g["activities"].items() if p in keep_projects}  # fmt: skip
    used: dict[str, int] = defaultdict(int)
    for items in acts.values():
        for a in items:
            for m in a["methods"]:
                used[m] += 1
    methods = [{**m, "summary": "", "activities": used[m["name"]],
                "projects": sum(1 for items in acts.values()
                                if any(m["name"] in a["methods"] for a in items))}
               for m in g["methods"] if used.get(m["name"])]  # fmt: skip
    names = {f"p:{p}" for p in keep_projects} | {f"m:{m['name']}" for m in methods}
    return {
        "tree": tree,
        "methods": methods,
        "nodes": [{**n, "summary": ""} for n in g["nodes"] if n["id"] in names],
        "edges": [e for e in g["edges"] if e["from"] in names and e["to"] in names],
        "activities": acts,
        "stats": {
            "projects": len(keep_projects),
            "methods": len(methods),
            "activities": sum(len(v) for v in acts.values()),
            "areas": sorted({p["area"] for h in tree["children"] for p in h["children"]}),
        },
        "shared": True,
    }
