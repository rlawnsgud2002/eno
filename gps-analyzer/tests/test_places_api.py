import csv
import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch,Mock
import requests
from gps_analyzer.places import Places
from gps_analyzer.sources import GISBuildings,APIClient
from test_core import C


def building(identity,x0,x1,name='A',use='아파트'):
    return {'type':'Feature','properties':{'building_id':identity,'name':name,'use':use},'geometry':{'type':'Polygon','coordinates':[[[x0,36.779],[x1,36.779],[x1,36.781],[x0,36.781],[x0,36.779]]]}}


class PlacesTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.c=deepcopy(C)
    def tearDown(self): self.temp.cleanup()
    def gis(self,features):
        self.c['gis_file']='gis.geojson';(self.root/'gis.geojson').write_text(json.dumps({'type':'FeatureCollection','features':features}),encoding='utf-8')

    def test_adjacent_buildings_are_not_merged(self):
        self.gis([building('A',126.9999,127.0001),building('B',127.00015,127.00035)])
        r=Places(self.c,self.root,self.root)
        try:
            a=r.resolve('v1',36.78,127);b=r.resolve('v2',36.78,127.00025)
            self.assertNotEqual(a,b);self.assertIn('인접 건물 혼동',r.done[a]['review'])
        finally:r.close()

    def test_same_single_building_shared_across_coordinates(self):
        self.gis([building('A',126.998,127.002)])
        r=Places(self.c,self.root,self.root)
        try:
            self.assertEqual(r.resolve('vehicle29visit',36.78,127),r.resolve('vehicle3visit',36.7801,127.0001))
        finally:r.close()

    def test_nearby_without_gis_not_merged(self):
        r=Places(self.c,self.root,self.root)
        try:self.assertNotEqual(r.resolve('a',36.78,127),r.resolve('b',36.78,127.00001))
        finally:r.close()

    def test_manual_changes_persist_after_csv_removed(self):
        r=Places(self.c,self.root,self.root);pid=r.resolve('v',36.78,127);r.close()
        p=self.root/'place_overrides.csv'
        with p.open('w',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=['place_id','name','minor']);w.writeheader();w.writerow({'place_id':pid,'name':'사용자 확인 장소','minor':'의원'})
        r=Places(self.c,self.root,self.root);self.assertEqual(r.resolve('v',36.78,127),pid);r.close();p.unlink()
        r=Places(self.c,self.root,self.root)
        try:
            self.assertEqual(r.resolve('v',36.78,127),pid);self.assertEqual(r.done[pid]['name'],'사용자 확인 장소');self.assertEqual(r.done[pid]['minor'],'의원')
        finally:r.close()

    def test_new_gis_evidence_preserves_existing_visit_id_assignment(self):
        r=Places(self.c,self.root,self.root);pid=r.resolve('v',36.78,127);r.close()
        self.gis([building('A',126.998,127.002)])
        r=Places(self.c,self.root,self.root)
        try:self.assertEqual(r.resolve('v',36.78,127),pid)
        finally:r.close()

    def test_invalid_gis_isolated(self):
        self.c['gis_file']='missing.geojson'
        r=Places(self.c,self.root,self.root)
        try:
            pid=r.resolve('v',36.78,127);self.assertEqual(r.done[pid]['minor'],'미확인');self.assertIn('실패',r.done[pid]['review'])
        finally:r.close()

    def test_nearest_outside_polygon_is_not_confirmed(self):
        self.gis([building('A',127.00005,127.002)])
        g=GISBuildings(self.c,self.root);lookup=g.lookup(36.78,127)
        self.assertIsNone(lookup['match']);self.assertEqual(lookup['status'],'인근 체류 추정')


class APITest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.c=deepcopy(C)
        self.c['api'].update(enabled=True,retries=1,min_interval_seconds=0,max_requests_per_run=2)
    def tearDown(self):self.temp.cleanup()

    @patch.dict(os.environ,{},clear=True)
    def test_missing_keys_do_not_fail(self):
        a=APIClient(self.c,self.root)
        try:
            self.assertEqual(a.kakao(36,127,True)['status'],'missing_key');self.assertEqual(a.registry({})['status'],'missing_key');self.assertEqual(a.count,0)
        finally:a.close()

    @patch('gps_analyzer.sources.time.sleep')
    @patch('gps_analyzer.sources.requests.get',side_effect=requests.Timeout)
    def test_failure_retry_and_quota(self,request,sleep):
        a=APIClient(self.c,self.root)
        try:
            self.assertEqual(a.get('kakao','https://example.test',{})['status'],'network_or_response_error')
            self.assertEqual(request.call_count,2);self.assertEqual(a.get('kakao','https://example.test',{})['status'],'quota_limit')
        finally:a.close()

    @patch('gps_analyzer.sources.requests.get')
    def test_cache_and_no_default_kakao_disk_retention(self,request):
        request.return_value=Mock(status_code=200,json=lambda:{'documents':[]})
        a=APIClient(self.c,self.root)
        try:
            for _ in range(2):self.assertEqual(a.get('kakao','https://example.test',{'x':1})['status'],'ok')
            self.assertEqual(request.call_count,1);self.assertEqual(a.cache.execute('SELECT count(*) FROM cache').fetchone()[0],0)
        finally:a.close()

    @patch('gps_analyzer.sources.requests.get')
    def test_public_service_error_in_http200(self,request):
        request.return_value=Mock(status_code=200,json=lambda:{'response':{'header':{'resultCode':'30'}}})
        a=APIClient(self.c,self.root)
        try:self.assertEqual(a.get('registry','https://example.test',{})['status'],'service_error')
        finally:a.close()

    @patch.dict(os.environ,{'KAKAO_REST_API_KEY':'test'},clear=True)
    @patch('gps_analyzer.places.APIClient.kakao',return_value={'status':'network_or_response_error'})
    def test_failed_api_keeps_place(self,call):
        r=Places(self.c,self.root,self.root)
        try:
            p=r.resolve('v',36.78,127);self.assertIn(p,r.done);self.assertIn('카카오 조회 실패',r.done[p]['review'])
        finally:r.close()

class SourceComparisonTest(unittest.TestCase):
    setUp=PlacesTest.setUp
    tearDown=PlacesTest.tearDown
    gis=PlacesTest.gis

    @patch('gps_analyzer.places.APIClient.registry')
    def test_conflicting_uses_are_left_unconfirmed(self,request):
        self.c['api']['verify_known_use']=True
        self.gis([building('A',126.998,127.002)])
        request.return_value={'status':'ok','items':[{'mainPurpsCdNm':'병원','bldNm':'A병원'}]}
        r=Places(self.c,self.root,self.root)
        try:
            pid=r.resolve('v',36.78,127);label=r.done[pid]
            self.assertEqual(label['minor'],'미확인');self.assertIn('용도 불일치',label['review'])
            self.assertIn('건축물대장',label['comparison'])
        finally:r.close()

    def test_observed_spread_prevents_overconfident_match(self):
        self.gis([building('A',126.9999,127.0001),building('B',127.00045,127.00065)])
        g=GISBuildings(self.c,self.root)
        self.assertIsNotNone(g.lookup(36.78,127)['match'])
        self.assertIsNone(g.lookup(36.78,127,observed_radius_m=40)['match'])

class ManualIDTest(unittest.TestCase):
    setUp=PlacesTest.setUp
    tearDown=PlacesTest.tearDown
    gis=PlacesTest.gis

    def test_two_manual_places_keep_ids_after_new_shared_gis_match(self):
        r=Places(self.c,self.root,self.root)
        a=r.resolve('a',36.78,127);b=r.resolve('b',36.7801,127.0001);r.close()
        with (self.root/'place_overrides.csv').open('w',encoding='utf-8',newline='') as f:
            w=csv.writer(f);w.writerow(['place_id','name']);w.writerow([a,'수동 A']);w.writerow([b,'수동 B'])
        r=Places(self.c,self.root,self.root);r.resolve('a',36.78,127);r.resolve('b',36.7801,127.0001);r.close()
        self.gis([building('shared',126.998,127.002)])
        r=Places(self.c,self.root,self.root)
        try:
            self.assertEqual(r.resolve('a',36.78,127),a)
            self.assertEqual(r.resolve('b',36.7801,127.0001),b)
            self.assertIn('자동 통합 보류',r.done[b]['review']);self.assertEqual(r.done[b]['name'],'수동 B')
        finally:r.close()
