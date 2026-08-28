"""Print every card's box from the built map, in reading order.

Routing work needs real numbers: a `via` corridor is only correct relative to
where the cards actually landed, and after any re-layout the old coordinates
are guesses. Run this, then route against the output.

    py scratchpad/_anchors.py
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
    A, _ = m.build('light')

for k in sorted(A, key=lambda k: (round(A[k][1]), round(A[k][0]))):
    x, y, w, h = A[k]
    print('%-9s x=%7.1f y=%7.1f w=%6.1f h=%5.1f  right=%7.1f bot=%7.1f'
          % (k, x, y, w, h, x + w, y + h))
