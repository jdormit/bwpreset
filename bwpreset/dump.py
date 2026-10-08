import sys
import uuid

from .codec import Obj, parse


def fmt_scalar(v):
    if isinstance(v, bytes):
        return f"bytes[{len(v)}]"
    if isinstance(v, uuid.UUID):
        return f"uuid:{v}"
    if isinstance(v, list) and len(v) > 8 and all(isinstance(x, float) for x in v):
        return f"floats[{len(v)}]"
    return repr(v)


class Dumper:
    def __init__(self, out=None):
        self.out = out or sys.stdout
        self.index: dict[int, int] = {}

    def dump(self, v, indent=0):
        pad = "  " * indent
        if isinstance(v, Obj):
            if id(v) in self.index:
                self.out.write(f"&{self.index[id(v)]} <{v.cls:#x}>\n")
                return
            self.index[id(v)] = len(self.index) + 1
            self.out.write(f"#{self.index[id(v)]} <{v.cls:#x}>\n")
            for fid, t, fv in v.fields:
                self.out.write(f"{pad}  {fid:#x}/{t:#x} = ")
                self.dump(fv, indent + 1)
        elif isinstance(v, list) and v and all(isinstance(x, Obj) for x in v):
            self.out.write("[\n")
            for x in v:
                self.out.write(f"{pad}  - ")
                self.dump(x, indent + 2)
            self.out.write(f"{pad}]\n")
        else:
            self.out.write(fmt_scalar(v) + "\n")


def dump(v, out=None):
    Dumper(out).dump(v)


if __name__ == "__main__":
    p = parse(open(sys.argv[1], "rb").read())
    for k, t, v in p.meta:
        print(f"# {k} = {fmt_scalar(v)}")
    dump(p.body)
