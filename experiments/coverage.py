"""Decompile every Grid preset, rebuild it, and compare the two."""

import collections
import sys

from bwpreset import model as m
from bwpreset.codec import parse, serialize, walk
from bwpreset.decompile import decompile


def node_values(nodes):
    return {(m.get(o, m.F_NAME), m.get(p, m.F_NAME)): m.param_value(p) for o in nodes for p in m.params(o) if p.cls in m.VALUE_FIELD}


def graph(preset) -> dict:
    """Comparable summary of a Grid preset's structure."""
    device = m.get(preset.body, m.F_DEVICE)
    grid = next(o for o in walk(device) if o.cls == m.GRID)
    mods = m.get(grid, m.F_LIST)
    modulators = m.get(m.get(device, m.F_MODULATORS), m.F_LIST)
    routes = set()
    for owner in [*mods, *modulators, *[o for o in walk(device) if o.cls == m.POLY]]:
        sources = [m.get(owner, m.F_POLY_SPREAD)] if owner.cls == m.POLY else [p for p in m.params(owner) if p.cls == m.MOD_SOURCE]
        for src in sources:
            for r in m.get(src, m.F_ROUTINGS):
                routes.add(
                    (
                        owner.cls,
                        m.get(owner, m.F_NAME),
                        m.get(src, m.F_NAME),
                        m.get(r, m.F_ROUTE_TARGET),
                        m.get(r, m.F_ROUTE_AMOUNT),
                        m.get(r, m.F_ROUTE_SCALE_SOURCE),
                        m.get(r, m.F_ROUTE_MODE),
                    )
                )
    pages = m.get(m.get(device, m.F_REMOTE_CONTROLS), m.F_REMOTE_PAGES)
    return {
        "modules": len(mods),
        "cables": {
            (m.get(o, m.F_NAME), m.get(p, m.F_NAME), m.get(p, m.F_SOURCE))
            for o in mods
            for p in m.params(o)
            if p.cls == m.P_INPUT and m.get(p, m.F_SOURCE)
        },
        "routes": routes,
        "remotes": {
            (i, m.get(page, m.F_PAGE_NAME), m.get(rc, m.F_RC_INDEX), m.get(rc, m.F_RC_TARGET), m.get(rc, m.F_RC_NAME))
            for i, page in enumerate(pages)
            for rc in m.get(page, m.F_PAGE_CONTROLS)
        },
        "values": node_values(mods) | {("mod",) + k: v for k, v in node_values(modulators).items()},
    }


def compare(original, rebuilt, unsupported: list[str]) -> list[str]:
    """Names of summary parts that differ, ignoring parts the decompiler reported as unsupported."""
    a, b = graph(original), graph(rebuilt)
    stale = {u.split(" ")[-1] for u in unsupported if u.startswith("stale")}
    a["routes"] = {r for r in a["routes"] if r[3] not in stale}
    a["remotes"] = {r for r in a["remotes"] if r[3] not in stale}
    skip = set()
    if any(u.startswith("routing") for u in unsupported):
        skip.add("routes")
    if any(u.startswith("remote") for u in unsupported):
        skip.add("remotes")
    if any("." in u.split(" ")[0] for u in unsupported):
        skip.add("values")
    return [k for k in a if k not in skip and a[k] != b[k]]


def main(list_file: str):
    stats = collections.Counter()
    mismatches = []
    reasons = collections.Counter()
    for path in open(list_file).read().splitlines():
        original = parse(open(path, "rb").read())
        if m.get(original.body, m.F_DEVICE) is None or not any(o.cls == m.GRID for o in walk(original.body)):
            continue
        ns = {}
        try:
            code, unsupported = decompile(original)
            for u in set(unsupported):
                reasons[u if "." in u.split(" ")[0] else u.split(" ")[0]] += 1
            exec(code, ns)
            rebuilt = parse(serialize(ns["p"].to_preset()))
        except Exception as e:
            stats["error"] += 1
            reasons[f"ERROR {type(e).__name__}: {str(e)[:90]}"] += 1
            continue
        stats["rebuilt"] += 1
        stats["complete" if not [u for u in unsupported if not u.startswith("stale")] else "partial"] += 1
        if diff := compare(original, rebuilt, unsupported):
            stats["mismatch"] += 1
            mismatches.append((path.split("/")[-1], diff))
    print(dict(stats))
    for k, v in sorted(reasons.items(), key=lambda kv: (not kv[0].startswith("ERROR"), -kv[1]))[:40]:
        print(v, k)
    for name, diff in mismatches:
        print("MISMATCH", name, diff)


if __name__ == "__main__":
    main(sys.argv[1])
