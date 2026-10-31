"""
Exactly-once test of object_stream.StreamSegmenter (no hardware).

    python tools\\test_object_stream.py

500 simulated objects, about half crossing a frame boundary, side by side, random sizes.
Pass = 500 objects in, 500 out, 0 duplicates, 0 missed, every crop whole (exact extents).
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from object_stream import StreamSegmenter, make_synthetic_stream  # noqa: E402

failures = 0


def check(name, cond, detail=""):
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        failures += 1


for frame_h in (200, 32, 16):
    frames, truth = make_synthetic_stream(n_objects=500, frame_h=frame_h, seed=frame_h)
    crossing = sum(1 for t, b, l, r in truth if t // frame_h != b // frame_h)
    seg = StreamSegmenter(min_area_px=20, pad=6)
    seg.bgm.learn(np.full((32, 4096, 3), (160, 90, 30), np.uint8))   # 'feeder off' background, as on the machine
    t0 = time.perf_counter()
    found = []
    for i, f in enumerate(frames):
        found += seg.push(f, first_line=i * frame_h)
    found += seg.flush()
    dt = time.perf_counter() - t0
    got = sorted((o.line_top, o.line_bottom, o.x_left, o.x_right) for o in found)
    want = sorted(truth)
    dup = len(got) - len(set(got))
    missing = len(set(want) - set(got))
    extra = len(set(got) - set(want))
    print(f"--- frame height {frame_h}: {len(frames)} frames, {crossing} objects cross a frame boundary, "
          f"{dt * 1000 / len(frames):.2f} ms/frame")
    check(f"[H={frame_h}] 500 in -> 500 out", len(found) == 500, len(found))
    check(f"[H={frame_h}] 0 duplicates", dup == 0, dup)
    check(f"[H={frame_h}] 0 missed, 0 wrong extents", missing == 0 and extra == 0, (missing, extra))
    check(f"[H={frame_h}] crops present and padded", all(o.crop is not None and o.crop.shape[0] >= o.height_lines
                                                         for o in found))

# Gap in the stream (lost frames): objects before the gap are finished, none duplicated
frames, truth = make_synthetic_stream(n_objects=100, frame_h=200, seed=7)
seg = StreamSegmenter()
seg.bgm.learn(np.full((32, 4096, 3), (160, 90, 30), np.uint8))
found = []
for i, f in enumerate(frames):
    if i == 5:
        continue                               # simulate a dropped frame
    found += seg.push(f, first_line=i * 200)
found += seg.flush()
keys = [(o.line_top, o.line_bottom, o.x_left, o.x_right) for o in found]
check("dropped frame: no duplicates", len(keys) == len(set(keys)), len(keys) - len(set(keys)))

print("\nALL TESTS PASSED" if failures == 0 else f"\n{failures} TEST(S) FAILED")
sys.exit(1 if failures else 0)
