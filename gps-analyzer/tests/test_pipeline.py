import hashlib
import json
import os
import tempfile
import unittest
import zipfile
from copy import deepcopy
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch
import duckdb
from openpyxl import Workbook,load_workbook
from gps_analyzer.pipeline import run,REPORT_NAME
from gps_analyzer.report import Excel,SHEETS
from gps_analyzer.core import distance_m
from test_core import C


def write_input(path,vehicle=29,rows=None):
    book=Workbook();s=book.active;s.append(['GPS수집시간','위도','경도','속도','삭제여부'])
    if rows is None:
        t=datetime(2026,6,11)
        rows=[[(t+timedelta(seconds=i)).isoformat(' '),36.78,127.0,0,'N'] for i in [180,120,60,0]]
    for row in rows:s.append(row)
    book.save(path)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.input=self.root/'input';self.input.mkdir();self.c=deepcopy(C)
    def tearDown(self): self.temp.cleanup()
    def run_it(self):return run(self.input,self.root/'output',self.root/'cache',self.c,self.root)

    def test_cross_vehicle_place_and_all_totals(self):
        write_input(self.input/'ASAN_29_2026_06_11.xlsx')
        write_input(self.input/'ASAN_3_2026_06_11.xlsx',3)
        r=self.run_it();self.assertEqual(r['visits'],2);self.assertEqual(r['sources']['places'],1)
        w=load_workbook(self.root/'output'/REPORT_NAME,read_only=True,data_only=True)
        try:
            self.assertEqual(w.sheetnames,SHEETS)
            rows=list(w[SHEETS[0]].values);self.assertEqual(rows[1][6:8],(2,2))
            month=list(w[SHEETS[1]].values)[1];self.assertEqual(month[2:8],(0,0,2,0,0,2))
        finally:w.close()

    def test_invalid_duplicates_and_conflicting_times_reconcile(self):
        a=['2026-06-11 00:00:00',36.78,127,0,'N']
        b=['2026-06-11 00:01:00',36.78,127,0,'N']
        conflict=['2026-06-11 00:01:00',36.79,127,0,'N']
        bad=['2026-06-11 00:02:00',1000,1000,0,'N']
        deleted=['2026-06-11 00:03:00',36.78,127,0,'Y']
        write_input(self.input/'ASAN_29_2026_06_11.xlsx',rows=[a,a,b,conflict,bad,deleted])
        r=self.run_it();n=r['input']
        self.assertEqual((n['raw'],n['invalid'],n['duplicates'],n['conflicting_points'],n['clean']),(6,2,1,2,1))
        self.assertEqual(r['visits'],0);self.assertTrue(all(r['checks'].values()))

    def test_only_invalid_input_still_exports_empty_report(self):
        write_input(self.input/'ASAN_29_2026_06_11.xlsx',rows=[['2026-06-11 00:00:00',1000,1000,0,'N']])
        r=self.run_it();self.assertEqual(r['visits'],0);self.assertEqual(r['input']['clean'],0)
        self.assertTrue((self.root/'output'/REPORT_NAME).exists())

    def test_resume_keeps_completed_cache_and_ids(self):
        write_input(self.input/'ASAN_29_2026_06_11.xlsx')
        first=self.run_it()
        files=list((self.root/'cache'/'parquet').glob('*.parquet'))+list((self.root/'cache'/'segments').glob('*.parquet'))
        before={p:p.stat().st_mtime_ns for p in files}
        evidence=(self.root/'output'/'source_evidence.jsonl').read_bytes()
        second=self.run_it()
        self.assertEqual(first,second);self.assertEqual(before,{p:p.stat().st_mtime_ns for p in files})
        self.assertEqual(evidence,(self.root/'output'/'source_evidence.jsonl').read_bytes())

    def test_partial_file_failure_keeps_completed_ingestion(self):
        write_input(self.input/'ASAN_3_2026_06_11.xlsx',3)
        bad=self.input/'ASAN_29_2026_06_11.xlsx';bad.write_bytes(b'not an xlsx')
        # File 29 sorts before 3: use a later name to exercise prior checkpoint.
        bad.rename(self.input/'ZZ_ASAN_29_2026_06_11.xlsx')
        with self.assertRaises(zipfile.BadZipFile):self.run_it()
        files=list((self.root/'cache'/'parquet').glob('*.parquet'));self.assertEqual(len(files),1)
        before=files[0].stat().st_mtime_ns
        (self.input/'ZZ_ASAN_29_2026_06_11.xlsx').unlink()
        self.assertEqual(self.run_it()['visits'],1);self.assertEqual(files[0].stat().st_mtime_ns,before)

    def test_api_failure_keeps_every_visit_in_report(self):
        self.c['api']['enabled']=True
        write_input(self.input/'ASAN_29_2026_06_11.xlsx')
        with patch('gps_analyzer.places.APIClient.kakao',return_value={'status':'http_503'}):r=self.run_it()
        self.assertEqual(r['visits'],1);self.assertEqual(r['sources']['review_places'],1)

    def test_output_inside_input_rejected(self):
        with self.assertRaises(ValueError):run(self.input,self.input/'output',self.root/'cache',self.c,self.root)


class ExcelTest(unittest.TestCase):
    def test_split_and_formula_injection_protection(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'split.xlsx';e=Excel(p,max_rows=4)
            try:e.table('분할',['장소명','방문 횟수'],[(f'=HYPERLINK("bad","{i}")',i) for i in range(10)])
            finally:e.close()
            w=load_workbook(p,read_only=True,data_only=False)
            try:
                self.assertEqual(len(w.sheetnames),4)
                self.assertEqual(sum(len(list(s.values))-1 for s in w),10)
                self.assertTrue(all(row[0].data_type=='s' for s in w for row in list(s.rows)[1:]))
                self.assertTrue(all(s.max_row is None or s.max_row<=4 for s in w))
            finally:w.close()


SAMPLE=os.getenv('GPS_SAMPLE_FILE')

@unittest.skipUnless(SAMPLE and Path(SAMPLE).is_file(),'GPS_SAMPLE_FILE 환경변수로 실제 샘플 경로 지정')
class RealSampleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        cls.source=Path(SAMPLE).resolve();cls.before=hashlib.sha256(cls.source.read_bytes()).hexdigest()
        cls.result=run(cls.source.parent,cls.root/'output',cls.root/'cache',deepcopy(C),Path(__file__).resolve().parents[1])
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_raw_file_unchanged(self):
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),self.before)

    def test_real_cleaning_and_interval_baseline(self):
        self.assertEqual(self.result['input'],dict(raw=2610,valid=2588,invalid=22,deduplicated=2578,duplicates=10,conflicting_points=0,clean=2578))
        self.assertEqual(self.result['visits'],12);self.assertEqual(self.result['sources']['api_requests'],0)
        self.assertEqual(self.result['short_segments'],122);self.assertTrue(all(self.result['checks'].values()))

    def test_each_reported_interval_against_original_rows(self):
        # Independent read and interval checks; does not reuse pipeline point tables.
        w=load_workbook(self.source,read_only=True,data_only=True)
        try:
            values=list(w.active.values);head={v:i for i,v in enumerate(values[0])}
            raw=set()
            for r in values[1:]:
                lat,lon=float(r[head['위도']]),float(r[head['경도']])
                if -90<=lat<=90 and -180<=lon<=180:
                    raw.add((datetime.fromisoformat(r[head['GPS수집시간']]),lat,lon,float(r[head['속도']])))
        finally:w.close()
        source=sorted(raw)
        db=duckdb.connect(str(self.root/'cache'/'analysis.duckdb'),read_only=True)
        try:
            visits=db.execute('SELECT start,"end",duration_s,lat,lon,point_count FROM visits ORDER BY start').fetchall()
            for start,end,seconds,lat,lon,count in visits:
                segment=[p for p in source if start<=p[0]<=end]
                self.assertEqual(len(segment),count)
                self.assertEqual((end-start).total_seconds(),seconds);self.assertGreaterEqual(seconds,180)
                self.assertTrue(all((b[0]-a[0]).total_seconds()<=120 for a,b in zip(segment,segment[1:])))
                self.assertTrue(all(p[3]<=5 for p in segment))
                self.assertTrue(all(distance_m((segment[0][1],segment[0][2]),(p[1],p[2]))<=40 for p in segment))
                self.assertIn((lat,lon),[(p[1],p[2]) for p in segment])
            # Stationary overnight block ends before the observed 9h36m33s gap.
            self.assertEqual(visits[0][0],datetime(2026,6,11,0,0,27))
            self.assertEqual(visits[0][1],datetime(2026,6,11,4,48,58))
            self.assertFalse(any(a<datetime(2026,6,11,14,25,31) and b>datetime(2026,6,11,4,48,58) for a,b,*_ in visits))
        finally:db.close()

    def test_excel_counts_and_charts(self):
        w=load_workbook(self.root/'output'/REPORT_NAME,read_only=True,data_only=True)
        try:
            self.assertEqual(w.sheetnames,SHEETS)
            self.assertEqual(len(list(w[SHEETS[3]].values))-1,12)
            self.assertEqual(sum(r[6] for r in list(w[SHEETS[0]].values)[1:]),12)
            self.assertEqual(sum(r[7] for r in list(w[SHEETS[1]].values)[1:]),12)
            # Every offline place is explicitly unconfirmed and present in review.
            self.assertEqual(len(list(w[SHEETS[6]].values))-1,12)
        finally:w.close()
        with zipfile.ZipFile(self.root/'output'/REPORT_NAME) as z:
            self.assertEqual(len([n for n in z.namelist() if n.startswith('xl/charts/chart') and n.endswith('.xml')]),2)
