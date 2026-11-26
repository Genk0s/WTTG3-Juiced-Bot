"""Read-only Steam cache monitoring and bounded screenshot history.

No Steam file is created, modified, locked for writing, or uploaded.
"""
from pathlib import Path
from collections import deque
import datetime
import json
import logging
import math
import os
import struct
import time

APP_ID = '3869850'
ACH_NAME = 'ACH_JUICED'
LOG = logging.getLogger(__name__)


def binary_kv(raw):
    """Read the subset of Steam binary KeyValues used by the supplied schema/cache."""
    if not raw or len(raw) > 4 * 1024 * 1024:
        raise ValueError('unexpected cache/schema size')
    pos = 0
    def string():
        nonlocal pos
        end = raw.index(b'\0', pos)
        value = raw[pos:end].decode('utf-8', errors='strict')
        pos = end + 1
        return value
    def obj(depth=0):
        nonlocal pos
        if depth > 20:
            raise ValueError('nested KeyValues too deep')
        values = {}
        while pos < len(raw):
            typ = raw[pos]
            pos += 1
            if typ == 8:
                return values
            name = string()
            if name in values:
                raise ValueError('duplicate KeyValues entry')
            if typ == 0:
                value = obj(depth + 1)
            elif typ == 1:
                value = string()
            elif typ in (2, 3, 4, 6):
                value = struct.unpack_from('<I', raw, pos)[0]
                pos += 4
            elif typ == 7:
                value = struct.unpack_from('<Q', raw, pos)[0]
                pos += 8
            else:
                raise ValueError('unsupported KeyValues type: %d' % typ)
            values[name] = value
        raise ValueError('incomplete KeyValues object')
    result = obj()
    if pos != len(raw):
        raise ValueError('trailing data')
    return result


def achievement_location(schema):
    root = binary_kv(schema)[APP_ID]
    hits = []
    for group, stat in root['stats'].items():
        if stat.get('type') != 'ACHIEVEMENTS':
            continue
        for bit, entry in stat.get('bits', {}).items():
            if entry.get('name') == ACH_NAME:
                idx = int(bit)
                if not 0 <= idx < 32:
                    raise ValueError('unsupported achievement bit')
                hits.append((group, idx))
    if len(hits) != 1:
        raise ValueError('achievement schema not unique')
    return hits[0]


def achievement_state(raw, location):
    group, bit = location
    record = binary_kv(raw)['cache'][group]
    mask = record['data']
    if not isinstance(mask, int) or not 0 <= mask <= 0xffffffff:
        raise ValueError('invalid achievement mask')
    timestamp = record.get('AchievementTimes', {}).get(str(bit), 0)
    if not isinstance(timestamp, int):
        raise ValueError('invalid unlock timestamp')
    # The actual cache bit is authoritative; a timestamp may be absent.
    return bool(mask & (1 << bit)), timestamp or None


def registry_value(path, name):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except (ImportError, OSError):
        return None


def locate_cache(root):
    # Optional explicit override for multiple Steam installations/accounts.
    config = Path(root) / 'steam_achievement.json'
    if config.exists():
        val = json.loads(config.read_text(encoding='utf-8'))['stats_file']
        p = Path(os.path.expandvars(str(val))).expanduser()
        if not p.is_absolute():
            p = Path(root) / p
        if not p.is_file() or not p.name.startswith('UserGameStats_') or not p.name.endswith('_' + APP_ID + '.bin'):
            raise ValueError('configured stats_file is not a WTTG3 cache')
        return p, p.parent / ('UserGameStatsSchema_' + APP_ID + '.bin')
    steam_path = registry_value(r'Software\Valve\Steam', 'SteamPath')
    active = registry_value(r'Software\Valve\Steam\ActiveProcess', 'ActiveUser')
    active = (int(active) & 0xffffffff) if isinstance(active, int) else 0
    roots = []
    if steam_path:
        roots.append(Path(steam_path))
    for env in ('ProgramFiles(x86)', 'ProgramFiles'):
        if os.environ.get(env):
            roots.append(Path(os.environ[env]) / 'Steam')
    candidates = []
    seen = set()
    for base in roots:
        d = base / 'appcache' / 'stats'
        if d in seen:
            continue
        seen.add(d)
        schema = d / ('UserGameStatsSchema_' + APP_ID + '.bin')
        if not schema.is_file():
            continue
        if active:
            p = d / ('UserGameStats_%s_%s.bin' % (active, APP_ID))
            if p.is_file():
                return p, schema
        else:
            candidates.extend((p, schema) for p in d.glob('UserGameStats_*_' + APP_ID + '.bin') if p.is_file())
    if active:
        raise FileNotFoundError('active Steam account cache not found')
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        raise ValueError('multiple account caches; configure steam_achievement.json')
    raise FileNotFoundError('WTTG3 local Steam cache not found')


class AchievementWatch:
    def __init__(self, root, interval=.5):
        self.root = Path(root)
        self.interval = interval
        self.next_poll = 0.
        self.next_locate = 0.
        self.cache_path = None
        self.location = None
        self.schema_path = None
        self.status = None
        self.timestamp = None
        self.last_warning = None
        self.last_signature = None

    def warn(self, message):
        if message != self.last_warning:
            LOG.warning('Juiced monitor: %s; farming continues, evidence buffer stays active', message)
            self.last_warning = message

    def poll(self, now=None, force=False):
        now = time.monotonic() if now is None else now
        if not force and now < self.next_poll:
            return self.status
        self.next_poll = now + self.interval
        if self.cache_path is None:
            if not force and now < self.next_locate:
                return self.status
            self.next_locate = now + 10.
            try:
                self.cache_path, self.schema_path = locate_cache(self.root)
                self.location = achievement_location(self.schema_path.read_bytes())
                LOG.info('Juiced monitor: Steam cache found, achievement group=%s bit=%s (read only)', *self.location)
            except (OSError, ValueError, KeyError, TypeError, struct.error) as exc:
                self.cache_path = None
                self.warn(type(exc).__name__ + ': ' + str(exc))
                return self.status
        try:
            stat = self.cache_path.stat()
            sig = (stat.st_size, stat.st_mtime_ns)
            if not force and sig == self.last_signature and self.status is not None:
                return self.status
            raw = self.cache_path.read_bytes()
            after = self.cache_path.stat()
            if sig != (after.st_size, after.st_mtime_ns):
                return self.status  # Client changed it during the read.
            state, stamp = achievement_state(raw, self.location)
            self.last_signature = sig
            if state != self.status:
                LOG.info('Juiced monitor: %s', 'UNLOCKED' if state else 'locked')
            self.status, self.timestamp = state, stamp
            self.last_warning = None
        except (OSError, ValueError, KeyError, TypeError, struct.error) as exc:
            # Steam may be replacing the cache. A failed read is never an unlock.
            self.warn('temporary unreadable cache (' + type(exc).__name__ + ')')
        return self.status


class JuicedUnlocked(Exception):
    pass


def write_bytes(path, data):
    path = Path(path)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_bytes(data)
    tmp.replace(path)


class Evidence:
    """120 seconds of existing game captures, bounded by both time and bytes."""
    def __init__(self, root, seconds=120., interval=.33, max_bytes=96*1024*1024):
        self.root = Path(root)
        self.seconds = seconds
        self.interval = interval
        self.max_bytes = max_bytes
        self.frames = deque()
        self.bytes = 0
        self.next_frame = 0.
        self.next_balance = 0.
        self.peak = None
        self.peak_jpeg = None
        self.peak_gain = None
        self.gain_jpeg = None
        self.previous_balance = None
        self.peak_warning = False
        self.record_warning = False

    @staticmethod
    def jpeg(img):
        import cv2
        success, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not success:
            raise ValueError('cannot encode frame')
        return buf.tobytes()

    @staticmethod
    def png(img):
        import cv2
        success, buf = cv2.imencode('.png', img)
        if not success:
            raise ValueError('cannot encode PNG')
        return buf.tobytes()

    def record(self, img, now=None):
        now = time.monotonic() if now is None else now
        if now < self.next_frame:
            return
        self.next_frame = now + self.interval
        try:
            data = self.jpeg(img)
            self.frames.append((now, time.time(), data))
            self.bytes += len(data)
            while self.frames and (now - self.frames[0][0] > self.seconds or self.bytes > self.max_bytes):
                self.bytes -= len(self.frames.popleft()[2])
        except Exception as exc:
            if not self.record_warning:
                LOG.warning('Evidence capture failed; farming continues: %s', exc)
                self.record_warning = True

    def observe_balance(self, v, img, now=None):
        # This is an additional safety net, not a jackpot condition or a
        # definitive payout measurement. No guessed OCR can stop the bot.
        now = time.monotonic() if now is None else now
        if now < self.next_balance:
            return
        self.next_balance = now + .5
        try:
            if not v.has('merr_title'):
                self.previous_balance = None
                return
            _, amount = v.taskbar_balances()
            if amount is None or not math.isfinite(amount) or amount < 0:
                return
            peak = self.peak is None or amount > self.peak
            gain = None
            if self.previous_balance is not None:
                last, at = self.previous_balance
                if now - at <= 5:
                    gain = amount - last
            self.previous_balance = amount, now
            gain_record = gain is not None and gain >= 5000 and (self.peak_gain is None or gain > self.peak_gain)
            if not peak and not gain_record:
                return
            data = self.jpeg(img)
            self.root.mkdir(parents=True, exist_ok=True)
            if peak:
                self.peak, self.peak_jpeg = amount, data
                write_bytes(self.root / 'highest_observed_balance.jpg', data)
            if gain_record:
                self.peak_gain, self.gain_jpeg = gain, data
                write_bytes(self.root / 'largest_observed_balance_increase.jpg', data)
                # Preserve the preceding award frames even if Steam writes
                # its local cache more than two minutes after the event.
                folder = self.root / 'largest_increase_previous_frames'
                folder.mkdir(exist_ok=True)
                for old in folder.glob('frame_*.jpg'):
                    old.unlink()
                for i, (t, _, blob) in enumerate(self.frames):
                    if now - t <= 15:
                        write_bytes(folder / ('frame_%03d.jpg' % i), blob)
            write_bytes(self.root / 'observed_values.json', json.dumps({
                'highest_observed_balance': self.peak,
                'largest_observed_balance_increase': self.peak_gain,
                'note': 'Visual estimates; not confirmed win amount or achievement evidence.'
            }, indent=2).encode('utf-8'))
        except Exception as exc:
            if not self.peak_warning:
                LOG.warning('Supplementary balance evidence unavailable: %s', exc)
                self.peak_warning = True

    def save_unlock(self, img, timestamp, spins):
        stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        folder = self.root / ('JUICED_' + stamp)
        folder.mkdir(parents=True, exist_ok=False)
        write_bytes(folder / 'unlock_detected.png', self.png(img))
        hist = folder / 'previous_frames'
        hist.mkdir()
        frames = list(self.frames)
        for i, (_, wall, blob) in enumerate(frames):
            name = 'frame_%04d_%s.jpg' % (i, datetime.datetime.fromtimestamp(wall).strftime('%H%M%S_%f'))
            write_bytes(hist / name, blob)
        if self.peak_jpeg:
            write_bytes(folder / 'highest_observed_balance.jpg', self.peak_jpeg)
        if self.gain_jpeg:
            write_bytes(folder / 'largest_observed_balance_increase.jpg', self.gain_jpeg)
            saved = self.root / 'largest_increase_previous_frames'
            if saved.is_dir():
                gain_history = folder / 'largest_increase_previous_frames'
                gain_history.mkdir()
                for frame in sorted(saved.glob('frame_*.jpg')):
                    write_bytes(gain_history / frame.name, frame.read_bytes())
        report = {'achievement': ACH_NAME, 'source': 'read-only local Steam cache',
                  'steam_unlock_unix': timestamp, 'detected_local_time': datetime.datetime.now().isoformat(),
                  'spins_total_estimated': spins, 'previous_frames': len(frames),
                  'history_seconds_actual': round(frames[-1][0]-frames[0][0], 2) if frames else 0,
                  'highest_observed_balance': self.peak,
                  'largest_observed_balance_increase': self.peak_gain,
                  'pause_verified': False, 'payout_completion_verified': False,
                  'note': 'Balance observations are visual estimates, not guaranteed jackpot amount.'}
        write_bytes(folder / 'report.json', json.dumps(report, indent=2).encode('utf-8'))
        return folder, report

    def save_result(self, folder, img, name):
        if folder is not None and img is not None:
            write_bytes(folder / (name + '.png'), self.png(img))
