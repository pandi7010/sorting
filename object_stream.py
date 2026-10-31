"""
object_stream.py - find every object in a CONTINUOUS line-scan stream, exactly once.

A line-scan camera delivers the product as a never-ending strip, cut into frames
(e.g. 200 lines each). A cashew that falls across the cut between frame 7 and 8 is
half in each frame. If you analysed frames one by one you would get two half-cashews.

How this works (simple picture):
  1. New frame rows are added under the rows we kept from before ("carry" rows).
  2. Pixels that differ from the background are marked as "object".
  3. Connected object pixels are grouped (cv2.connectedComponents).
  4. A group that touches the BOTTOM edge is not finished yet (it continues in the next
     frame) -> keep its rows for next time.
  5. A group that does not touch the bottom is finished -> emit it ONCE, then erase it
     from the kept mask so it can never be found again.
So every object comes out exactly once, whole, no matter where the frame cuts are.

Used by: tools/ball_speed.py (Phase 2) and capture_engine.py (Phase 4).
"""

from dataclasses import dataclass, field

import cv2
import numpy as np


@dataclass
class StreamObject:
    obj_id: int
    line_top: int            # global line number of the first object line (0 = first line of the stream)
    line_bottom: int         # global line number of the last object line
    x_left: int
    x_right: int
    area_px: int
    cx: float                # centroid x (pixels)
    cy_line: float           # centroid y (global line number, may be fractional)
    crop: np.ndarray = field(repr=False, default=None)   # BGR crop incl. padding
    mask: np.ndarray = field(repr=False, default=None)   # uint8 0/255 mask of this object in the crop
    flags: tuple = ()        # "touches_left", "touches_right", "partial_start", "too_long"

    @property
    def height_lines(self):
        return self.line_bottom - self.line_top + 1

    @property
    def width_px(self):
        return self.x_right - self.x_left + 1


class BackgroundModel:
    """Per-column background colour, learned from rows that contain no object.
    Object pixel = any colour channel differs from the background by more than `threshold`."""

    def __init__(self, threshold=30, alpha=0.02):
        self.threshold = threshold
        self.alpha = alpha
        self.bg = None            # (W, 3) float32

    def learn(self, frames):
        """Learn the background from EMPTY-chute frames (feeder off). Best method on the machine."""
        stack = np.concatenate(list(frames), axis=0) if isinstance(frames, (list, tuple)) else frames
        self.bg = np.median(stack, axis=0).astype(np.float32)
        self._bg_u8 = None

    update_every = 8              # background update cost is spread out: every Nth frame

    def mask(self, frame):
        if self.bg is None:
            # fallback: per-column median of the first frame (correct if < 50 % of it is product)
            self.bg = np.median(frame, axis=0).astype(np.float32)
        h = frame.shape[0]
        bg_u8 = getattr(self, "_bg_u8", None)
        if bg_u8 is None or bg_u8.shape[0] != h:
            bg_u8 = np.ascontiguousarray(np.broadcast_to(self.bg.astype(np.uint8)[None], frame.shape))
            self._bg_u8 = bg_u8
        diff = cv2.absdiff(frame, bg_u8)                       # fast, uint8
        d = cv2.max(cv2.max(diff[:, :, 0], diff[:, :, 1]), diff[:, :, 2])
        m = (d > self.threshold).astype(np.uint8)
        self._n = getattr(self, "_n", 0) + 1
        if self._n % self.update_every:
            return m
        self._bg_u8 = None                                     # background changes below -> rebuild cache
        # slow per-column update from pixels that are background in this frame
        free = (m == 0)
        n_free = free.sum(axis=0)
        cols = n_free >= max(1, frame.shape[0] // 2)
        if cols.any():
            sums = (frame.astype(np.float32) * free[:, :, None]).sum(axis=0)
            mean = sums[cols] / n_free[cols][:, None]
            self.bg[cols] = (1 - self.alpha) * self.bg[cols] + self.alpha * mean
        # a column that is 'object' in EVERY row of many frames is probably a wrong background:
        # re-learn it from this frame's median so a bad start can recover
        stuck = n_free == 0
        if stuck.any():
            self._stuck = getattr(self, "_stuck", np.zeros(frame.shape[1], np.int32))
            self._stuck[stuck] += 1
            self._stuck[~stuck] = 0
            relearn = self._stuck > 50
            if relearn.any():
                self.bg[relearn] = np.median(frame[:, relearn], axis=0)
                self._stuck[relearn] = 0
        return m


class StreamSegmenter:
    def __init__(self, min_area_px=20, max_object_lines=4000, pad=8, open_kernel=3,
                 x_range=None, background=None, keep_crops=True):
        """
        min_area_px      : smaller blobs are ignored as noise/dust
        max_object_lines : an 'object' longer than this is force-cut (stuck debris, product stream)
        pad              : crop padding in pixels/lines around each object
        open_kernel      : morphological opening size to remove 1-pixel noise (0 = off)
        x_range          : (x0, x1) only look at these columns (e.g. start/end pixel of the chute)
        """
        self.min_area = min_area_px
        self.max_lines = max_object_lines
        self.pad = pad
        self.kernel = np.ones((open_kernel, open_kernel), np.uint8) if open_kernel and open_kernel > 1 else None
        self.x_range = x_range
        self.bgm = background or BackgroundModel()
        self.keep_crops = keep_crops

        self.img_buf = None       # (rows, W, 3)
        self.mask_buf = None      # (rows, W) uint8 0/1, emitted objects already erased
        self.buf_line0 = 0        # global line number of img_buf row 0
        self.next_line = 0        # global line number of the next pushed row
        self.next_id = 1
        self.first_push = True
        self.stats = {"frames": 0, "objects": 0, "too_long": 0, "noise_blobs": 0}

    # ------------------------------------------------------------------
    def push(self, frame, first_line=None):
        """Add one frame (H x W x 3, BGR). first_line = global line number of its first row
        (pass it when known, e.g. from the line counter; a jump means lines were lost)."""
        if first_line is None:
            first_line = self.next_line
        h = frame.shape[0]
        m = self.bgm.mask(frame)
        if self.x_range:
            x0, x1 = self.x_range
            m[:, :x0] = 0
            m[:, x1:] = 0
        # NOTE: noise filtering (opening) is done on the JOINED buffer in _extract, never per frame:
        # per-frame filtering would erase thin object slivers at the frame cut.

        if self.img_buf is None or first_line != self.next_line:
            # start, or a gap in the stream: finish everything open, then restart the buffer
            out = self.flush() if self.img_buf is not None else []
            self.img_buf = frame.copy()
            self.mask_buf = m
            self.buf_line0 = first_line
        else:
            out = []
            self.img_buf = np.concatenate([self.img_buf, frame], axis=0)
            self.mask_buf = np.concatenate([self.mask_buf, m], axis=0)
        self.next_line = first_line + h
        self.stats["frames"] += 1
        out += self._extract(final=False)
        self.first_push = False
        return out

    def flush(self):
        """End of stream: emit all remaining objects (those touching the bottom are cut there)."""
        if self.img_buf is None:
            return []
        out = self._extract(final=True)
        self.img_buf = None
        self.mask_buf = None
        return out

    # ------------------------------------------------------------------
    def _extract(self, final):
        rows = self.mask_buf.shape[0]
        work = self.mask_buf
        if self.kernel is not None:
            work = cv2.morphologyEx(self.mask_buf, cv2.MORPH_OPEN, self.kernel)   # idempotent: safe to repeat
        n, labels, st, cent = cv2.connectedComponentsWithStats(work, connectivity=8)
        out = []
        open_tops = []
        erase = []
        W = self.mask_buf.shape[1]
        for i in range(1, n):
            x, y, w, h, area = st[i]
            bottom = y + h - 1
            touches_bottom = bottom == rows - 1
            if area < self.min_area:
                if not touches_bottom or final:
                    erase.append(i)
                    self.stats["noise_blobs"] += 1
                else:
                    open_tops.append(y)
                continue
            too_long = h >= self.max_lines
            if touches_bottom and not final and not too_long:
                open_tops.append(y)
                continue
            flags = []
            if x == 0:
                flags.append("touches_left")
            if x + w >= W:
                flags.append("touches_right")
            if y == 0 and self.buf_line0 == 0 and self.first_push:
                flags.append("partial_start")
            if too_long:
                flags.append("too_long")
                self.stats["too_long"] += 1
            obj = StreamObject(
                obj_id=self.next_id,
                line_top=self.buf_line0 + int(y), line_bottom=self.buf_line0 + int(bottom),
                x_left=int(x), x_right=int(x + w - 1), area_px=int(area),
                cx=float(cent[i][0]), cy_line=float(self.buf_line0 + cent[i][1]), flags=tuple(flags))
            if self.keep_crops:
                p = self.pad
                y0, y1 = max(0, y - p), min(rows, y + h + p)
                x0, x1 = max(0, x - p), min(W, x + w + p)
                obj.crop = self.img_buf[y0:y1, x0:x1].copy()
                obj.mask = ((labels[y0:y1, x0:x1] == i) * 255).astype(np.uint8)
            self.next_id += 1
            self.stats["objects"] += 1
            out.append(obj)
            erase.append(i)

        if final:
            return out

        # erase emitted objects + noise from the mask so they can never be found again
        if erase:
            self.mask_buf[np.isin(labels, erase)] = 0

        # keep rows needed for unfinished objects (+ padding above them), and always the last `pad` rows
        keep_from = rows - self.pad
        if open_tops:
            keep_from = min(keep_from, min(open_tops) - self.pad)
        keep_from = max(0, keep_from)
        if keep_from > 0:
            self.img_buf = self.img_buf[keep_from:]
            self.mask_buf = self.mask_buf[keep_from:]
            self.buf_line0 += keep_from
        return out


def make_synthetic_stream(n_objects=500, frame_h=200, width=4096, seed=1, bg=(160, 90, 30),
                          sizes=(20, 60), lines_per_obj_gap=(5, 80), crossing_fraction=0.5):
    """Generate frames with n non-touching elliptical objects; about `crossing_fraction`
    of them deliberately cross a frame boundary. Returns (frames, truth) where truth is a
    list of (line_top, line_bottom, x_left, x_right) for every object."""
    rng = np.random.default_rng(seed)
    objs = []
    line = 10
    lane_free = {}  # simple placement: several objects side by side per "row band"
    while len(objs) < n_objects:
        n_side = int(rng.integers(1, 6))
        band_h = 0
        xs = sorted(rng.choice(np.arange(100, width - 200, 160), size=n_side, replace=False))
        for x in xs:
            if len(objs) >= n_objects:
                break
            h = int(rng.integers(*sizes))
            w = int(rng.integers(*sizes))
            top = line
            if rng.random() < crossing_fraction:
                # put it across the next frame boundary
                b = ((line // frame_h) + 1) * frame_h
                top = max(line, b - h // 2)
            objs.append((top, top + h - 1, int(x), int(x) + w - 1))
            band_h = max(band_h, top + h - line)
        line += band_h + int(rng.integers(*lines_per_obj_gap))
    total = ((line // frame_h) + 2) * frame_h
    strip = np.empty((total, width, 3), np.uint8)
    strip[:] = bg
    strip += rng.integers(0, 6, size=(total, 1, 1), dtype=np.uint8)   # small line-to-line noise
    truth = []
    bg_arr = np.array(bg)
    for (t, b, l, r) in objs:
        cy, cx = (t + b) / 2, (l + r) / 2
        ry, rx = (b - t + 1) / 2, (r - l + 1) / 2
        yy, xx = np.ogrid[t:b + 1, l:r + 1]
        inside = ((yy - cy) / ry) ** 2 + ((xx - cx) / rx) ** 2 <= 1.0
        while True:                                    # colour clearly different from background
            col = rng.integers(0, 256, size=3)
            if np.abs(col - bg_arr).max() >= 80:
                break
        region = strip[t:b + 1, l:r + 1]
        region[inside] = col
        ys, xs = np.nonzero(inside)                    # true extent after rasterising
        truth.append((t + ys.min(), t + ys.max(), l + xs.min(), l + xs.max()))
    frames = [strip[i:i + frame_h] for i in range(0, total, frame_h)]
    return frames, truth
