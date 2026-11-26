"""Bounded, independently timed click worker. No screen capture in this thread."""
import threading
import time

class FastClicker:
    def __init__(self, click, on_close=None):
        self.click = click
        self.on_close = on_close
        self.cv = threading.Condition()
        self.target = None
        self.deadline = 0.0
        self.interval = .05
        self.next_click = 0.0
        self.count = 0
        self.error = None
        self.closed = False
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

    def enable(self, x, y, interval=.05, lease=1.5):
        with self.cv:
            self._check_locked()
            if self.closed:
                raise RuntimeError('Fast clicker closed')
            if self.target is None:
                self.next_click = time.monotonic()
            self.target = (x, y)
            self.interval = max(.025, min(.5, float(interval)))
            self.deadline = time.monotonic() + max(.1, min(2.0, float(lease)))
            self.cv.notify_all()
            return self.count

    def _check_locked(self):
        if self.error is not None:
            raise self.error

    def check(self):
        with self.cv:
            self._check_locked()

    def pause(self):
        # Lock also surrounds each actual click: no in-flight click remains
        # when pause returns. Do not hold this lock during vision processing.
        with self.cv:
            self.target = None
            self.cv.notify_all()
            return self.count

    def close(self):
        with self.cv:
            self.target = None
            self.closed = True
            self.cv.notify_all()
        self.thread.join()
        return self.count

    def _worker(self):
        with self.cv:
            while not self.closed:
                now = time.monotonic()
                if self.target is not None and now >= self.deadline:
                    self.target = None
                if self.target is None:
                    self.cv.wait()
                    continue
                remaining = min(self.deadline, self.next_click) - now
                if remaining > 0:
                    self.cv.wait(remaining)
                    continue
                try:
                    self.click(*self.target)
                except Exception as exc:
                    self.error = exc
                    self.target = None
                    self.cv.notify_all()
                    continue
                self.count += 1
                self.next_click = max(now + self.interval, time.monotonic())
                # Release lock even if the click itself was slower than interval.
                self.cv.wait(max(.001, min(self.next_click, self.deadline) - time.monotonic()))

        if self.on_close is not None:
            self.on_close()
