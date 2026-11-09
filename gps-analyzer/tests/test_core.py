import json
import math
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from gps_analyzer.core import Point,Detector,distance_m
from gps_analyzer.sources import classify,parcel

C=json.loads((Path(__file__).resolve().parents[1]/'config.json').read_text(encoding='utf-8'))
T=datetime(2026,6,11)


def point(seconds,lat=36.78,lon=127.0,speed=0,vehicle=29):
    return Point(vehicle,T+timedelta(seconds=seconds),lat,lon,speed)


class DetectorTest(unittest.TestCase):
    def detect(self,rows,**kw): return list(Detector(dict(C,**kw)).detect(rows))

    def test_180_seconds_included(self):
        s=self.detect([point(i) for i in [0,60,120,180]])
        self.assertEqual(len(s),1);self.assertTrue(s[0].eligible);self.assertEqual(s[0].duration_s,180)

    def test_179_seconds_excluded(self):
        self.assertFalse(self.detect([point(i) for i in [0,60,120,179]])[0].eligible)

    def test_gap_does_not_count_as_dwell(self):
        s=self.detect([point(i) for i in [0,60,120,1000,1060,1120]])
        self.assertEqual([x.duration_s for x in s],[120,120]);self.assertTrue(all(not x.eligible for x in s))

    def test_exact_120_second_gap_allowed(self):
        s=self.detect([point(i) for i in [0,120,240]])
        self.assertTrue(s[0].eligible);self.assertEqual(s[0].max_gap_s,120)

    def test_fixed_anchor_prevents_creeping_chain(self):
        s=self.detect([point(i*60,lon=127+i*.0003) for i in range(5)])
        self.assertTrue(all(x.max_radius_m<=40 for x in s));self.assertFalse(any(x.eligible for x in s))

    def test_high_speed_excluded_even_identical_coords(self):
        self.assertEqual(self.detect([point(i,speed=30) for i in range(0,301,60)]),[])

    def test_single_point_not_a_visit(self):
        s=self.detect([point(0)])[0];self.assertFalse(s.eligible);self.assertEqual(s.duration_s,0)

    def test_vehicle_boundary(self):
        s=self.detect([point(0),point(60),point(120,vehicle=3),point(180,vehicle=3)])
        self.assertEqual([x.duration_s for x in s],[60,60]);self.assertEqual([x.vehicle for x in s],[29,3])

    def test_excess_path_is_reviewed_not_split_into_false_visits(self):
        # Jitter in a fixed 27m envelope must not create multiple visit counts.
        s=self.detect([point(i*60,lat=36.78+(i%2)*.00025) for i in range(30)])
        self.assertEqual(len(s),1);self.assertFalse(s[0].eligible)
        self.assertIn('과도한',s[0].quality)

    def test_jump_flag_and_exclusion(self):
        s=self.detect([point(0),point(60),point(61,lat=37),point(121,lat=37)])
        self.assertFalse(any(x.eligible for x in s));self.assertTrue(any('GPS 튐' in x.quality for x in s))

    def test_parameter_changes(self):
        self.assertTrue(self.detect([point(0),point(60)],min_stop_seconds=60)[0].eligible)
        self.assertFalse(self.detect([point(0),point(60)],max_gap_seconds=30)[0].eligible)

    def test_real_distance(self):
        self.assertAlmostEqual(distance_m((0,0),(0,1)),111195.08,places=1)
        self.assertEqual(distance_m((36,127),(36,127)),0)

    def test_classification_specificity(self):
        self.assertEqual(classify('요양병원'),('의료시설','요양병원'))
        self.assertEqual(classify('다가구주택'),('주거시설','다가구주택'))
        self.assertEqual(classify('제1종근린생활시설'),('미확인','미확인'))
        self.assertEqual(classify('공동주택'),('미확인','미확인'))

    def test_parcel(self):
        self.assertEqual(parcel({'pnu':'4420010100101230004'}),dict(sigunguCd='44200',bjdongCd='10100',platGbCd='0',bun='0123',ji='0004'))
        self.assertIsNone(parcel({}))

class BoundedMemoryTest(unittest.TestCase):
    def test_long_stop_coordinates_spill_to_disk(self):
        detector=Detector(C)
        try:
            for i in range(66000):detector.push(point(i))
            self.assertTrue(detector.spool._rolled)
            stay=detector.finish('test')
            self.assertEqual(stay.point_count,66000);self.assertEqual(stay.duration_s,65999)
            self.assertEqual((stay.lat,stay.lon),(36.78,127.0));self.assertTrue(stay.eligible)
        finally:detector.spool.close()
