from __future__ import annotations
import hashlib
import math
import struct
import tempfile
from dataclasses import dataclass
from datetime import datetime


def stable_id(prefix, *parts):
    return prefix + hashlib.sha256('|'.join(map(str, parts)).encode()).hexdigest()[:24]


def distance_m(a, b):
    """Great circle distance; coordinates are (lat, lon), metres."""
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2-p1, math.radians(b[1]-a[1])
    h = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 6371008.8 * 2 * math.asin(min(1, math.sqrt(h)))


@dataclass(frozen=True)
class Point:
    vehicle: int
    time: datetime
    lat: float
    lon: float
    speed: float | None

    @property
    def xy(self):
        return self.lat, self.lon


@dataclass
class Stay:
    visit_id: str
    vehicle: int
    start: datetime
    end: datetime
    duration_s: float
    lat: float
    lon: float
    point_count: int
    max_gap_s: float
    path_m: float
    max_radius_m: float
    reason: str
    quality: str
    eligible: bool


class Detector:
    """Streaming segments, fixed anchor, bounded path and speed gate.

    A gap/movement closes at the previous point, never at the new point.
    Representatives are actual observations nearest the segment centroid.
    GPS alone establishes a candidate, not a passenger visit or parking event.
    """
    def __init__(self, config):
        self.c = config
        self.count = 0
        self.first = self.last = None
        self.sum_lat = self.sum_lon = 0.0
        self.spool = tempfile.SpooledTemporaryFile(max_size=1024*1024, mode="w+b")
        self.path = 0.0
        self.max_gap = 0.0
        self.max_radius = 0.0
        self.quality = set()
        self.previous = None

    def finish(self, reason):
        if not self.count:
            return None
        p, q = self.first, self.last
        lat, lon = self.sum_lat/self.count, self.sum_lon/self.count
        self.spool.seek(0)
        representative = p.xy
        best = float('inf')
        while True:
            chunk = self.spool.read(16*4096)
            if not chunk: break
            for xy in struct.iter_unpack('dd',chunk):
                d = distance_m(xy,(lat,lon))
                if d < best: best, representative = d,xy
        duration = (q.time-p.time).total_seconds()
        eligible = duration >= self.c['min_stop_seconds'] and self.count >= 2 and self.path <= self.c['max_path_m']
        if self.path > self.c['max_path_m']:
            self.quality.add('과도한 누적 이동·정체 또는 GPS 흔들림 검토')
        out = Stay(stable_id('V_', p.vehicle, p.time.isoformat(), q.time.isoformat()),
                   p.vehicle, p.time, q.time, duration, representative[0], representative[1],
                   self.count, self.max_gap, self.path, self.max_radius, reason,
                   '; '.join(sorted(self.quality | {'GPS 체류 후보·실제 방문 미확인'})),
                   eligible)
        self.count = 0
        self.first = self.last = None
        self.sum_lat = self.sum_lon = 0.0
        self.spool.seek(0);self.spool.truncate(0)
        self.path = self.max_gap = self.max_radius = 0.0
        self.quality = set()
        return out

    def push(self, p):
        result = []
        prior = self.previous
        self.previous = p
        jump = False
        if prior and prior.vehicle == p.vehicle:
            dt = (p.time-prior.time).total_seconds()
            if dt > 0:
                jump = distance_m(prior.xy, p.xy)/dt*3.6 > self.c['jump_speed_kmh']
        if self.count:
            last, first = self.last, self.first
            dt = (p.time-last.time).total_seconds()
            step = distance_m(last.xy, p.xy)
            radius = distance_m(first.xy, p.xy)
            why = None
            if p.vehicle != first.vehicle: why = 'vehicle_change'
            elif dt <= 0: why = 'timestamp_conflict'
            elif dt > self.c['max_gap_seconds']: why = 'record_gap'
            elif jump: why = 'gps_jump'
            elif p.speed is not None and p.speed > self.c['max_stop_speed_kmh']: why = 'reported_motion'
            elif radius > self.c['radius_m']: why = 'radius_exceeded'
            if why:
                if jump: self.quality.add('GPS 튐 인접')
                done = self.finish(why)
                if done: result.append(done)
            else:
                self.path += step
                self.max_gap = max(self.max_gap, dt)
                self.max_radius = max(self.max_radius, radius)
        if p.speed is None or p.speed <= self.c['max_stop_speed_kmh']:
            if jump: self.quality.add('GPS 튐 인접')
            if p.speed is None: self.quality.add('속도 결측')
            if not self.count: self.first = p
            self.last = p
            self.count += 1
            self.sum_lat += p.lat;self.sum_lon += p.lon
            self.spool.write(struct.pack("dd",p.lat,p.lon))
        return result

    def detect(self, points):
        try:
            for p in points:
                yield from self.push(p)
            last = self.finish('end_of_data')
            if last: yield last
        finally:
            self.spool.close()
