"""
Geometry check for SYSTEM_MAP.html.

Nothing here looks at the drawing. It re-runs the generator and asks four
questions of the numbers: does any connector cut through a card it does not
belong to, does every connector land on its target, is anything off the canvas,
does anything overlap. Those are the failures that are hard to see and easy to
ship - the first build of this map had 12 of 32 lines tunnelling through cards
and it took a script to notice.

    python scratchpad/check_system_map.py
"""

import importlib.util, io, contextlib, os

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
spec = importlib.util.spec_from_file_location("m", "scratchpad/build_system_map.py")
m = importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(m)
A, L, W, H = m.anchors, m.LINKS, m.W, m.H

def seg_rect(p, q, r):
    rx, ry, rw, rh = r
    x0, x1 = sorted([p[0], q[0]]); y0, y1 = sorted([p[1], q[1]])
    return not (x1 <= rx + 2 or x0 >= rx + rw - 2 or y1 <= ry + 2 or y0 >= ry + rh - 2)

bad = 0
for a, b, pts in L:
    hit = set()
    for i in range(len(pts) - 1):
        for cid, rect in A.items():
            if cid in (a, b, 'job') or cid.startswith('sec_'):
                continue
            if seg_rect(pts[i], pts[i + 1], rect):
                hit.add(cid)
    if hit:
        bad += 1
        print('  %-9s -> %-8s crosses %s' % (a, b, sorted(hit)))
print('connectors: %d   crossing an unrelated card: %d' % (len(L), bad))

# every connector must actually land on its target edge
off = [(a, b) for a, b, pts in L
       if not (A[b][0] - 3 <= pts[-1][0] <= A[b][0] + A[b][2] + 3
               and A[b][1] - 3 <= pts[-1][1] <= A[b][1] + A[b][3] + 3)]
print('connectors missing their target:', off or 'none')

out = [(k, v) for k, v in A.items()
       if v[0] < 0 or v[1] < 0 or v[0] + v[2] > W or v[1] + v[3] > H]
print('cards outside canvas:', out or 'none')

def ov(x, y):
    return not (x[0] + x[2] <= y[0] or y[0] + y[2] <= x[0]
                or x[1] + x[3] <= y[1] or y[1] + y[3] <= x[1])
ks = [k for k in A if k != 'job']
lap = [(ks[i], ks[j]) for i in range(len(ks)) for j in range(i + 1, len(ks))
       if ov(A[ks[i]], A[ks[j]])]
print('overlapping cards:', lap or 'none')
