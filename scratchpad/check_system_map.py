"""
Geometry check for SYSTEM_MAP.html / SYSTEM_MAP_DARK.html.

Nothing here looks at the drawing. It re-runs the generator and asks five
questions of the numbers - the failures that are hard to see and easy to ship:

  1. does any connector cut through a card it does not belong to
  2. does every connector actually land on its target
  3. is anything off the canvas
  4. does anything overlap
  5. do two long lines of the SAME colour run parallel and close

The first build had 12 of 32 lines tunnelling through cards. The one after that
had none - and was still hard to read, because three long red lines ran side by
side down one corridor. Check 5 exists because of that.

    python scratchpad/check_system_map.py
"""

import contextlib
import importlib.util
import io
import os

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

spec = importlib.util.spec_from_file_location("m", "scratchpad/build_system_map.py")
m = importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(m)
    A, L = m.build('light')

FLOW = m.THEMES['light']['FLOW']
W, H = m.W, m.H
fail = 0


def seg_rect(p, q, r):
    rx, ry, rw, rh = r
    x0, x1 = sorted([p[0], q[0]])
    y0, y1 = sorted([p[1], q[1]])
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
fail += bad
print('1. connectors: %d, crossing an unrelated card: %d' % (len(L), bad))

off = [(a, b) for a, b, pts in L if b and not (
    A[b][0] - 3 <= pts[-1][0] <= A[b][0] + A[b][2] + 3
    and A[b][1] - 3 <= pts[-1][1] <= A[b][1] + A[b][3] + 3)]
fail += len(off)
print('2. connectors missing their target:', off or 'none')

out = [k for k, v in A.items()
       if v[0] < 0 or v[1] < 0 or v[0] + v[2] > W or v[1] + v[3] > H]
fail += len(out)
print('3. cards outside the canvas:', out or 'none')


def ov(x, y):
    return not (x[0] + x[2] <= y[0] or y[0] + y[2] <= x[0]
                or x[1] + x[3] <= y[1] or y[1] + y[3] <= x[1])


ks = [k for k in A if k != 'job']
lap = [(ks[i], ks[j]) for i in range(len(ks)) for j in range(i + 1, len(ks))
       if ov(A[ks[i]], A[ks[j]])]
fail += len(lap)
print('4. overlapping cards:', lap or 'none')

# 5. Two long same-colour runs closer than 12px, overlapping by more than 120px,
#    are the thing a reader cannot follow.
runs = []
for a, b, pts in L:
    for i in range(len(pts) - 1):
        p, q = pts[i], pts[i + 1]
        if abs(p[1] - q[1]) < 1 and abs(p[0] - q[0]) > 120:
            runs.append(('h', round(p[1]), sorted([p[0], q[0]]), a, b))
        elif abs(p[0] - q[0]) < 1 and abs(p[1] - q[1]) > 120:
            runs.append(('v', round(p[0]), sorted([p[1], q[1]]), a, b))

pairs = 0
for i in range(len(runs)):
    for j in range(i + 1, len(runs)):
        ax, ay, arng, a1, a2 = runs[i]
        bx, by, brng, b1, b2 = runs[j]
        if ax != bx or abs(ay - by) >= 12:
            continue
        share = min(arng[1], brng[1]) - max(arng[0], brng[0])
        if share > 120:
            pairs += 1
            print('  %s/%s and %s/%s run %dpx apart for %dpx'
                  % (a1, a2, b1, b2, abs(ay - by), share))
fail += pairs
print('5. confusable parallel runs:', pairs or 'none')

print('\nALL CLEAR' if not fail else '\n%d PROBLEM(S)' % fail)
