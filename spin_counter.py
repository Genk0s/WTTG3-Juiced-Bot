"""Persistent visual estimate of distinct presses of the active Merramun button.

Observation never issues extra clicks. A successful click on a visually ready
button counts immediately, without waiting for two later animation frames.
The latch is released by a non-ready frame, or a substantially changed reel
board if an entire busy phase was missed between samples.
"""
from pathlib import Path
import json
import logging
import time
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
SIZE = (1650, 927)
log = logging.getLogger(__name__)


class SpinStats:
    def __init__(self, path=ROOT / 'spin_stats.json'):
        self.path = Path(path)
        self.total = 0
        self.enabled = True
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding='utf-8'))
                n = data['total_spins']
                if type(n) is not int or n < 0:
                    raise ValueError('invalid total_spins')
                self.total = n
        except Exception as exc:
            self.enabled = False
            log.warning('Spin counter disabled; existing stats file preserved: %s', exc)

    def add(self):
        if not self.enabled:
            return False
        n = self.total + 1
        tmp = self.path.with_suffix('.tmp')
        try:
            tmp.write_text(json.dumps({'total_spins': n}, indent=2) + '\n', encoding='utf-8')
            tmp.replace(self.path)
        except Exception as exc:
            self.enabled = False
            log.warning('Spin counter could not save; farming continues: %s', exc)
            return False
        self.total = n
        return True


class SpinTracker:
    def __init__(self, stats):
        self.stats = stats
        self.session = 0
        self.latched = False
        self.last_board = None
        self.last_target = None
        self.last_at = None
        self.last_count_at = -1000.
        self.observed_ready = 0
        self.observed_busy = 0
        self.observed_uncertain = 0

    @staticmethod
    def board_changed(a, b):
        if a is None or b is None or a.shape != b.shape:
            return False
        return float(np.mean(np.abs(a.astype(np.float32)-b.astype(np.float32)))) > 13.

    def observe(self, state, now, target, board=None):
        # One-pixel template jitter must not reset the latch. A new position
        # or long pause is not, on its own, evidence of a new spin.
        self.last_at = now
        self.last_target = target
        if state == 'busy':
            self.observed_busy += 1
            self.latched = False
            self.last_board = None
        elif state == 'ready':
            self.observed_ready += 1
            if self.latched and now-self.last_count_at >= .12 and self.board_changed(self.last_board, board):
                self.latched = False
        else:
            self.observed_uncertain += 1
        # A small debounce also protects against a single flickering frame.
        return state == 'ready' and not self.latched and now-self.last_count_at >= .12

    def clicked(self, eligible, now, board):
        # Called only after the existing mouse click completed successfully.
        if eligible and self.stats.add():
            self.latched = True
            self.last_count_at = now
            self.last_board = None if board is None else board.copy()
            self.session += 1
            log.info('Spin +1 | SpinsTotal=%s | SpinsSession=%s', self.stats.total, self.session)


class SpinObserver:
    """Read a localized button/board region before the existing fast click."""
    def __init__(self, window, vision, stats):
        self.window = window
        self.stats = stats
        self.tracker = SpinTracker(stats)
        self.templates = {k: vision.templates[k] for k in ('spin', 'spin_hover', 'disabled', 'merr_title')}
        self.sct = None
        self.enabled = stats.enabled
        self.samples = 0
        self.next_debug = time.monotonic()+5
        self.last_detail = ''
        self.animation_offset = 110

    @staticmethod
    def score(crop, template):
        _, score, _, loc = cv2.minMaxLoc(cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED))
        return float(score), loc

    @classmethod
    def classify(cls, crop, templates):
        scores = {k: cls.score(crop, templates[k]) for k in ('spin', 'spin_hover', 'disabled')}
        k = max(scores, key=lambda name: scores[name][0])
        score, (x, y) = scores[k]
        h, w = templates[k].shape[:2]
        hsv = cv2.cvtColor(crop[y:y+h, x:x+w], cv2.COLOR_BGR2HSV)
        hue, sat, val = cv2.split(hsv)
        yellow = float(np.mean((hue>=8)&(hue<=42)&(sat>100)&(val>120)))
        saturated = float(np.mean((sat>120)&(val>140)))
        # Both ready variants share the bright gold rim, even when the blue
        # hover effect changes. Disabled is geometrically similar but dull.
        if max(scores['spin'][0],scores['spin_hover'][0],scores['disabled'][0]) >= .75 and yellow >= .20 and saturated >= .16:
            state = 'ready'
        elif scores['disabled'][0] >= .78 and yellow < .15 and saturated < .12:
            state = 'busy'
        elif score < .60:
            # The button has disappeared entirely (result/rolling animation).
            state = 'busy'
        else:
            state = 'uncertain'
        return state, f'{k}={score:.2f} yellow={yellow:.2f} bright={saturated:.2f}'

    @classmethod
    def is_ready(cls, crop, templates):
        return cls.classify(crop, templates)[0]=='ready'

    def capture(self, rect, x, y, w, h):
        left, top, cw, ch = rect
        if x < 0 or y < 0 or x+w > SIZE[0] or y+h > SIZE[1]:
            raise ValueError('counter region outside game window')
        x0, y0 = round(x*cw/SIZE[0]), round(y*ch/SIZE[1])
        x1, y1 = round((x+w)*cw/SIZE[0]), round((y+h)*ch/SIZE[1])
        if self.sct is None:
            import mss
            self.sct = mss.mss()
        a = np.asarray(self.sct.grab({'left':left+x0, 'top':top+y0, 'width':x1-x0, 'height':y1-y0}))[:, :, :3]
        return cv2.resize(a, (w, h), interpolation=cv2.INTER_AREA)

    def before(self, x, y):
        if not self.enabled or not self.stats.enabled:
            return False, False, None
        try:
            rect = self.window.rect()
            button = self.capture(rect, x-55, y-55, 110, 110)
            state, detail = self.classify(button, self.templates)
            self.last_detail = f'{state} {detail}'
            board = None
            if state == 'ready':
                # Check the window title before counting, not after two later
                # frames. Do not count on the wrong window or on the menu.
                title = self.capture(rect, x-327, y-671, 114, 33)
                if self.score(title, self.templates['merr_title'])[0] < .72:
                    state = 'uncertain'
                    self.last_detail += ' title mismatch'
                else:
                    # Fingerprint a stable section of reels. Handles a very
                    # fast complete animation between two ready observations.
                    panel = self.capture(rect, x-230, y-510, 460, 160)
                    board = cv2.resize(cv2.cvtColor(panel,cv2.COLOR_BGR2GRAY),(46,16),interpolation=cv2.INTER_AREA)
            now=time.monotonic()
            eligible=self.tracker.observe(state,now,(x,y),board)
            self.samples += 1
            if now>=self.next_debug:
                log.info('Spin monitor | ready=%s busy=%s uncertain=%s | last=%s | SpinsTotal=%s', self.tracker.observed_ready,self.tracker.observed_busy,self.tracker.observed_uncertain,self.last_detail,self.stats.total)
                self.next_debug=now+5
            return state == 'ready', eligible, board
        except Exception as exc:
            self.enabled = False
            log.warning('Spin observation disabled for this session; farming continues: %s', exc)
            return False, False, None

    def click(self, x, y):
        ready, eligible, board = self.before(x, y)
        # Animation clicks stay inside Merramun, but outside the actual spin
        # button. Otherwise a click on a button appearing between captures
        # can start an entirely unobserved spin. The main thread selects a
        # clear side of the slot before it enables this worker.
        # If observation fails, preserve ordinary farming rather than stall.
        offset = self.animation_offset
        self.window.click_fast(x if ready or not self.enabled or not self.stats.enabled else x+offset, y)
        if self.enabled:
            self.tracker.clicked(eligible,time.monotonic(),board)

    def close(self):
        if self.sct is not None:
            try:
                self.sct.close()
            except Exception as exc:
                log.warning('Spin observer cleanup: %s', exc)
            self.sct = None
