"""Synthetic V-World responses: never a substitute for live-key verification."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from openpyxl import load_workbook
from gps_analyzer.sources import APIClient,classify
from gps_analyzer.vworld import VWorldBuildings,response_error
from gps_analyzer.places import Places
from gps_analyzer.pipeline import run,REPORT_NAME
from test_core import C
from test_pipeline import write_input


def feature(identity='building-A',use='아파트',left=126.998,right=127.002,props=None):
    return {'type':'Feature','id':identity,'properties':props if props is not None else {'main_prpos_cd_nm':use,'buld_nm':'공공 건물 A','pnu':'4420010100101230004'},
      'geometry':{'type':'Polygon','coordinates':[[[left,36.779],[right,36.779],[right,36.781],[left,36.781],[left,36.779]]]}}


def payload(features,total=None):
    return {'response':{'status':'OK','record':{'total':str(len(features) if total is None else total)},
       'result':{'featureCollection':{'type':'FeatureCollection','features':features}}}}


def response(data,status=200):return Mock(status_code=status,json=lambda:deepcopy(data))


class VWorldTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.c=deepcopy(C)
        self.c['api'].update(enabled=True,min_interval_seconds=0,retries=0)
        self.c['vworld']={'enabled':True,'service_url':'http://localhost:8000','local_windows_only':True,'layer':'LT_C_BLDGINFO','page_size':100,'max_pages':10,'properties':{}}
        self.env=patch.dict(os.environ,{'VWORLD_API_KEY':'unit-secret-123','VWORLD_SERVICE_URL':'http://localhost:8000'},clear=True);self.env.start()
        self.windows=patch('gps_analyzer.vworld.is_windows_runtime',return_value=True);self.windows.start()
        self.api=APIClient(self.c,self.root);self.v=VWorldBuildings(self.c,self.api)
    def tearDown(self):self.api.close();self.windows.stop();self.env.stop();self.temp.cleanup()

    @patch('gps_analyzer.sources.requests.get')
    def test_missing_key_means_no_network(self,request):
        os.environ.pop('VWORLD_API_KEY')
        p=self.v.probe(36.78,127);self.assertEqual(p['status'],'missing_key');self.assertFalse(p['query_verified']);request.assert_not_called()

    @patch('gps_analyzer.sources.requests.get')
    def test_cloud_does_not_impersonate_localhost(self,request):
        with patch('gps_analyzer.vworld.is_windows_runtime',return_value=False):p=self.v.probe(36.78,127)
        self.assertEqual(p['status'],'local_windows_required');self.assertEqual(p['api_requests'],0);request.assert_not_called()

    @patch('gps_analyzer.sources.requests.get')
    def test_probe_validates_real_response_shape_and_attribute_names(self,request):
        request.return_value=response(payload([feature()]))
        p=self.v.probe(36.78,127)
        self.assertTrue(p['query_verified']);self.assertTrue(p['schema_ready']);self.assertEqual(p['feature_count'],1)
        self.assertIn('main_prpos_cd_nm',p['property_names']);self.assertEqual(p['mapped_fields']['use'],['main_prpos_cd_nm'])
        params=request.call_args.kwargs['params']
        self.assertEqual(params['key'],'unit-secret-123');self.assertEqual(params['domain'],'http://localhost:8000')
        self.assertEqual(params['data'],'LT_C_BLDGINFO');self.assertEqual(params['crs'],'EPSG:4326')
        self.assertIsNone(request.call_args.kwargs.get('headers'))
        self.assertNotIn('unit-secret-123',json.dumps(p))

    @patch('gps_analyzer.sources.requests.get')
    def test_four_residential_types_are_read_from_explicit_use(self,request):
        for use in ['아파트','단독주택','다가구주택','다세대주택']:
            with self.subTest(use=use):
                request.return_value=response(payload([feature(use=use)]))
                p=self.v.lookup(36.78,127,bypass_cache=True)
                self.assertIsNotNone(p['match']);self.assertEqual(classify(p['match']['use']),('주거시설',use))

    @patch('gps_analyzer.sources.requests.get')
    def test_generic_housing_does_not_become_apartment(self,request):
        request.return_value=response(payload([feature(use='공동주택')]))
        p=self.v.lookup(36.78,127);self.assertEqual(classify(p['match']['use']),('미확인','미확인'))

    @patch('gps_analyzer.sources.requests.get')
    def test_opaque_schema_requires_mapping_and_never_guesses_code(self,request):
        request.return_value=response(payload([feature(props={'a1':'GIS-1','a8':'01003','a9':'다가구주택'})]))
        p=self.v.probe(36.78,127)
        self.assertTrue(p['geometry_verified']);self.assertFalse(p['schema_ready']);self.assertIn('a9',p['property_names'])
        self.c['vworld']['properties']={'id':'a1','use':'a9','use_code':'a8'}
        p=self.v.probe(36.78,127);self.assertTrue(p['schema_ready']);self.assertEqual(p['mapped_fields']['use'],['a9'])

    @patch('gps_analyzer.sources.requests.get')
    def test_code_only_stays_unconfirmed(self,request):
        request.return_value=response(payload([feature(props={'main_prpos_cd':'01000'})]))
        p=self.v.lookup(36.78,127)
        self.assertEqual(p['match']['use'],'');self.assertIn('unmapped_or_empty_use_name',p['schema_issues'])

    @patch('gps_analyzer.sources.requests.get')
    def test_auth_failure_not_cached_or_retried_for_next_places(self,request):
        request.return_value=response({'response':{'status':'ERROR','error':{'code':'INVALID_KEY','text':'unit-secret-123'}}})
        first=self.v.lookup(36.78,127);second=self.v.lookup(36.79,127.1)
        self.assertEqual(first['status'],'authentication_or_domain_error');self.assertEqual(second['status'],'authentication_or_domain_error')
        self.assertEqual(request.call_count,1);self.assertEqual(self.api.cache.execute('SELECT count(*) FROM cache').fetchone()[0],0)
        self.assertNotIn('unit-secret-123',json.dumps(first))

    @patch('gps_analyzer.sources.requests.get')
    def test_same_tile_avoids_duplicate_download(self,request):
        request.return_value=response(payload([feature()]))
        self.v.lookup(36.7801,127.0001);self.v.lookup(36.7802,127.0002)
        self.assertEqual(request.call_count,1)

    @patch('gps_analyzer.sources.requests.get')
    def test_probe_bypasses_old_cache(self,request):
        request.return_value=response(payload([feature()]))
        self.v.probe(36.78,127);self.v.probe(36.78,127);self.assertEqual(request.call_count,2)

    @patch('gps_analyzer.sources.requests.get')
    def test_adjacent_buildings_not_combined(self,request):
        request.return_value=response(payload([feature('A',left=126.9999,right=127.0001),feature('B',left=127.00015,right=127.00035)]))
        p=self.v.lookup(36.78,127);self.assertIsNone(p['match']);self.assertEqual(p['status'],'인접 건물 혼동')

    @patch('gps_analyzer.sources.requests.get')
    def test_truncated_or_repeated_pages_cannot_confirm_unique_building(self,request):
        self.c['vworld']['page_size']=1
        request.return_value=response(payload([feature()],total=2))
        p=self.v.lookup(36.78,127);self.assertEqual(p['status'],'incomplete_pagination');self.assertIsNone(p['match'])

    @patch('gps_analyzer.sources.requests.get')
    def test_two_pages_read_all_buildings(self,request):
        self.c['vworld']['page_size']=1
        request.side_effect=[response(payload([feature('A')],total=2)),response(payload([feature('B')],total=2))]
        p=self.v.lookup(36.78,127);self.assertEqual(p['feature_count'],2);self.assertIsNone(p['match'])

    @patch('gps_analyzer.sources.requests.get')
    def test_wrong_crs_and_swapped_axes_not_used(self,request):
        f=feature();f['geometry']['coordinates']=[[[36.779,126.998],[36.781,126.998],[36.781,127.002],[36.779,127.002],[36.779,126.998]]]
        request.return_value=response(payload([f]));p=self.v.lookup(36.78,127)
        self.assertEqual(p['status'],'schema_or_geometry_error');self.assertIsNone(p['match'])

    @patch('gps_analyzer.sources.requests.get')
    def test_invalid_competitor_not_silently_discarded(self,request):
        bad=feature('bad');bad['geometry']=None
        request.return_value=response(payload([feature(),bad]));p=self.v.lookup(36.78,127)
        self.assertEqual(p['status'],'schema_or_geometry_error');self.assertIsNone(p['match'])

    @patch('gps_analyzer.sources.requests.get')
    def test_no_result_is_not_authenticated_building_success(self,request):
        request.return_value=response({'response':{'status':'NOT_FOUND'}})
        p=self.v.probe(36.78,127);self.assertEqual(p['status'],'no_features');self.assertFalse(p['schema_ready']);self.assertFalse(p['query_verified'])

    @patch('gps_analyzer.sources.requests.get')
    def test_vworld_and_kakao_keep_building_type_and_place_name_evidence(self,request):
        request.return_value=response(payload([feature()]))
        kakao={'status':'ok','address':[{'address':{'address_name':'아산시 테스트 지번'},'road_address':{'address_name':'아산시 테스트 도로','building_name':'A아파트'}}],
                'pois':[{'place_name':'옆 건물 약국','category_name':'의료 > 약국'}]}
        with patch('gps_analyzer.places.APIClient.kakao',return_value=kakao):
            r=Places(self.c,self.root,self.root)
            try:
                pid=r.resolve('visit29',36.78,127);label=r.done[pid]
                self.assertEqual(label['minor'],'아파트');self.assertIn('V-World',label['sources']);self.assertIn('카카오',label['sources'])
                self.assertIn('옆 건물 약국',label['kakao_result']);self.assertIn('실제 방문 시설 미확정',label['review'])
            finally:r.close()

    @patch('gps_analyzer.sources.requests.get')
    def test_complete_local_gis_saves_remote_requests(self,request):
        self.c['gis_file']='local.geojson'
        f=feature();f['properties']={'building_id':'localA','name':'공공 A','use':'아파트','address':'아산'}
        (self.root/'local.geojson').write_text(json.dumps({'type':'FeatureCollection','features':[f]}),encoding='utf-8')
        r=Places(self.c,self.root,self.root)
        try:r.resolve('v',36.78,127);request.assert_not_called()
        finally:r.close()

    @patch('gps_analyzer.sources.requests.get')
    def test_excel_survives_vworld_auth_error_and_records_status(self,request):
        request.return_value=response({'response':{'status':'ERROR','error':{'code':'INCORRECT_DOMAIN'}}})
        inputdir=self.root/'input';inputdir.mkdir();write_input(inputdir/'ASAN_29_2026_06_11.xlsx')
        result=run(inputdir,self.root/'output',self.root/'work',self.c,self.root)
        self.assertEqual(result['visits'],1)
        w=load_workbook(self.root/'output'/REPORT_NAME,read_only=True,data_only=True)
        try:
            rows=list(w['장소 자동 분류'].values);header=list(rows[0]);i=header.index('V-World 조회 결과')
            self.assertIn('authentication_or_domain_error',rows[1][i]);self.assertEqual(rows[1][3],'미확인')
        finally:w.close()

    @patch('gps_analyzer.sources.requests.get')
    def test_mixed_residential_use_is_unconfirmed(self,request):
        request.return_value=response(payload([feature(use='아파트 및 다세대주택')]))
        p=self.v.lookup(36.78,127)
        self.assertEqual(p['match']['use'],'');self.assertIn('ambiguous_use_name',p['schema_issues'])

    @patch('gps_analyzer.sources.requests.get')
    def test_shared_id_supplements_generic_local_public_use(self,request):
        self.c['gis_file']='local.geojson'
        f=feature();f['properties']={'building_id':'shared','name':'공공 A','use':'공동주택','address':'아산'}
        (self.root/'local.geojson').write_text(json.dumps({'type':'FeatureCollection','features':[f]}),encoding='utf-8')
        request.return_value=response(payload([feature('shared',use='아파트')]))
        r=Places(self.c,self.root,self.root)
        try:
            p=r.resolve('v',36.78,127)
            self.assertEqual(r.done[p]['minor'],'아파트');self.assertIn('V-World',r.done[p]['sources'])
            self.assertIn('주용도명: 아파트',r.done[p]['basis'])
        finally:r.close()
