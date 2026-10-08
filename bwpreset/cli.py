import argparse
import runpy
import sys
from pathlib import Path

from . import model as m
from .build import Patch, PatchError
from .codec import parse
from .decompile import decompile
from .dump import dump
from .schema import load, refresh


def cmd_list(args):
    schema = load()[0]
    for section in ("modules", "modulators"):
        if args.modulators and section == "modules" or args.modules_only and section == "modulators":
            continue
        print(f"# {section}")
        for kind, e in sorted(schema[section].items()):
            text = f"{kind} {e['description']}".lower()
            if args.query and args.query.lower() not in text:
                continue
            ports = f"in: {', '.join(e['inputs']) or '-'}; out: {', '.join(e['outputs']) or '-'}"
            if e["mod_sources"]:
                ports += f"; mod: {', '.join(e['mod_sources'])}"
            print(f"{kind:32} {ports}")
            if e["description"]:
                print(f"{'':32} {e['description']}")


def describe(d: dict | None) -> str:
    if not d:
        return ""
    if "options" in d:
        return "options " + ", ".join(f"{k}={v}" for k, v in d["options"].items())
    text = ", ".join(f"{k} {d[k]:g}" for k in ("min", "max", "default", "unit", "scaling") if k in d)
    hint = m.DISPLAY_HINTS.get((d.get("unit"), d.get("scaling")))
    return f"{text} [{hint}]" if hint else text


def cmd_info(args):
    schema = load()[0]
    sections = ["modulators", "modules"] if args.modulator else ["modules", "modulators"]
    section = next((s for s in sections if args.kind in schema[s]), None)
    if section is None:
        sys.exit(f"unknown module {args.kind!r}; try `bwgrid list {args.kind.split('/')[-1]}`")
    e = schema[section][args.kind]
    print(f"{args.kind} ({section[:-1]}, seen {e['count']}x): {e['description']}")
    print(f"outputs: {', '.join(e['outputs']) or '-'}")
    for name, p in e["params"].items():
        line = f"  {name:24} {p['kind']:10} {describe(p.get('descriptor'))}"
        if p.get("common_values") and p["kind"] not in ("input", "mod_source"):
            line += f"  common: {p['common_values']}"
        print(line.rstrip())
        if p.get("description"):
            print(f"  {'':24} {p['description']}")


def cmd_build(args):
    ns = runpy.run_path(args.script)
    patches = list({id(v): v for v in ns.values() if isinstance(v, Patch)}.values())
    if not patches:
        sys.exit(f"{args.script} defines no Patch")
    names = [p.filename for p in patches]
    if len(set(names)) != len(names):
        raise PatchError("patch names produce duplicate output filenames")
    for p in patches:
        print(p.save(args.out / p.filename if args.out else None))


def cmd_decompile(args):
    code, issues = decompile(parse(Path(args.file).read_bytes()), lossless=not args.readable)
    for issue in issues:
        print(issue, file=sys.stderr)
    print(code, end="")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="bwgrid", description="Read and write Bitwig Grid presets")
    sub = ap.add_subparsers(required=True)
    s = sub.add_parser("list", help="list modules and modulators")
    s.add_argument("query", nargs="?")
    s.add_argument("--modulators", action="store_true", help="only modulators")
    s.add_argument("--modules", dest="modules_only", action="store_true", help="only grid modules")
    s.set_defaults(func=cmd_list)
    s = sub.add_parser("info", help="show a module's parameters")
    s.add_argument("kind", help='e.g. "Envelope/ADSR"')
    s.add_argument("--modulator", action="store_true", help="prefer the modulator when a name is both")
    s.set_defaults(func=cmd_info)
    s = sub.add_parser("build", help="run a patch script and save every Patch it defines")
    s.add_argument("script")
    s.add_argument("--out", type=Path, help="output directory (default: user library Presets/bwpreset)")
    s.set_defaults(func=cmd_build)
    s = sub.add_parser("decompile", help="print builder code for a preset")
    s.add_argument("file")
    mode = s.add_mutually_exclusive_group()
    mode.add_argument("--readable", action="store_true", help="emit sparse builder calls, reporting state they cannot reproduce")
    mode.add_argument("--lossless", action="store_false", dest="readable", help="preserve the complete imported graph (default)")
    s.set_defaults(readable=False)
    s.set_defaults(func=cmd_decompile)
    s = sub.add_parser("dump", help="print a preset's raw object tree")
    s.add_argument("file")
    s.set_defaults(func=lambda a: dump(parse(open(a.file, "rb").read()).body))
    s = sub.add_parser("refresh", help="rescan installed presets and rebuild the schema")
    s.set_defaults(func=lambda a: print({k: len(v) for k, v in refresh().items()}))
    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
