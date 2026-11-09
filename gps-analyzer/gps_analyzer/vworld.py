"""V-World 2D GetFeature adapter and local Windows preflight.

No Referer/Origin spoofing, key rotation or WFS fallback after auth failures.
Opaque a0/a1/a9 fields are never interpreted without an explicit mapping.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlparse
from dotenv import load_dotenv
from shapely.geometry import shape
from shapely.errors import GEOSException
from shapely.strtree import STRtree
from .sources import GISBuildings, APIClient

ENDPOINT='https://api.vworld.kr/req/data'
SOURCE='V-World 2D데이터 API / 국토교통부 GIS건물통합정보'
DEFAULT_FIELDS={
 'id':['gis_bld_id','gis_buld_id','buld_mgt_sn','bd_mgt_sn','building_id'],
 'name':['buld_nm','bld_nm','building_name','buldNm'],
 'use':['main_prpos_cd_nm','main_prpos_code_nm','mainPrposCodeNm','main_purps_cd_nm','건축물용도명'],
 'use_code':['main_prpos_cd','main_prpos_code','mainPrposCode','main_purps_cd'],
 'address':['jibun_addr','jibun_address','address','plat_plc'],
 'road_address':['road_addr','road_address','new_plat_plc'],
 'pnu':['pnu'], 'registry_id':['mgm_bldrgst_pk','mgmBldrgstPk']}


def is_windows_runtime():
    return os.name=='nt'


def response_error(data):
    """Normalize V-World HTTP-200 errors before any successful-result caching."""
    body=data.get('response')
    if not isinstance(body,dict): return {'status':'invalid_response'}
    state=str(body.get('status','')).upper()
    if state in ('OK','NOT_FOUND'): return None
    error=body.get('error') or {}
    code=str(error.get('code','UNKNOWN')) if isinstance(error,dict) else 'UNKNOWN'
    # Report only fixed categories, never server text or request URLs (which can echo keys).
    upper=code.upper()
    status='authentication_or_domain_error' if any(s in upper for s in ('KEY','AUTH','DOMAIN','REFERER','PERMISSION','ACCESS')) else 'service_error'
    return {'status':status}


def redact(value):
    """Also protect persisted evidence when a provider echoes the query key."""
    secret=os.getenv('VWORLD_API_KEY','')
    if isinstance(value,dict):
        return {k:('[REDACTED]' if str(k).lower() in ('key','apikey','api_key','servicekey') else redact(v)) for k,v in value.items()}
    if isinstance(value,list):return [redact(v) for v in value]
    if isinstance(value,str) and secret:return value.replace(secret,'[REDACTED]')
    return value


def extract(properties,field,cfg):
    aliases=cfg.get('properties',{}).get(field,DEFAULT_FIELDS.get(field,[]))
    if isinstance(aliases,str):aliases=[aliases]
    lower={str(k).lower():k for k in properties}
    values=[]
    for alias in aliases:
        actual=lower.get(str(alias).lower())
        if actual is not None and properties[actual] is not None and str(properties[actual]).strip():
            values.append((actual,str(properties[actual]).strip()))
    if len({v for _,v in values})>1:return '',[k for k,_ in values],True
    return (values[0][1],[values[0][0]],False) if values else ('',[],False)


class VWorldBuildings:
    def __init__(self,c,api):
        self.c=c;self.cfg=c.get('vworld',{});self.api=api;self.failure=None;self.tiles={}
        self.status='미설정';self.last_probe=None

    def readiness(self):
        if not self.cfg.get('enabled',False):return 'disabled'
        if not os.getenv('VWORLD_API_KEY','').strip():return 'missing_key'
        if not self.api.c['enabled']:return 'disabled'
        service_url=os.getenv('VWORLD_SERVICE_URL') or self.cfg.get('service_url','http://localhost:8000')
        parsed=urlparse(service_url)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:return 'invalid_service_url'
        # A localhost registration is not evidence that a cloud client is authorized.
        if not is_windows_runtime() and (self.cfg.get('local_windows_only',True) or parsed.hostname in ('localhost','127.0.0.1','::1')):
            return 'local_windows_required'
        return 'ready'

    def params(self,lat,lon,radius,page):
        # Quantized query tiles allow nearby unique stops to reuse building downloads.
        step=.002
        west=math.floor(lon/step)*step;south=math.floor(lat/step)*step
        padding=math.ceil(max(1,radius)/50)*50
        dy=padding/111195;dx=dy/max(.1,math.cos(math.radians(south)))
        bbox=f'BOX({west-dx:.9f},{south-dy:.9f},{west+step+dx:.9f},{south+step+dy:.9f})'
        return dict(service='data',version='2.0',request='GetFeature',format='json',
                    data=self.cfg.get('layer','LT_C_BLDGINFO'),crs='EPSG:4326',
                    geometry='true',attribute='true',geomFilter=bbox,
                    size=self.cfg.get('page_size',100),page=page,
                    domain=os.getenv('VWORLD_SERVICE_URL') or self.cfg.get('service_url','http://localhost:8000'),
                    key=os.getenv('VWORLD_API_KEY',''))

    def fetch(self,lat,lon,radius,bypass_cache=False):
        ready=self.readiness()
        if ready!='ready':return {'status':ready,'features':[]}
        if self.failure and not bypass_cache:return {'status':self.failure,'features':[]}
        initial=self.params(lat,lon,radius,1)
        tile=(initial['geomFilter'],initial['data'])
        if not bypass_cache and tile in self.tiles:return self.tiles[tile]
        features=[];seen={};previous_page=None
        for page in range(1,self.cfg.get('max_pages',10)+1):
            result=self.api.get('vworld',ENDPOINT,self.params(lat,lon,radius,page),bypass_cache=bypass_cache)
            if result['status']!='ok':
                self.failure=result['status'];return {'status':result['status'],'features':[]}
            body=result['data'].get('response',{})
            problem=response_error(result['data'])
            if problem:
                self.failure=problem['status'];return dict(problem,features=[])
            if str(body.get('status','')).upper()=='NOT_FOUND':
                if page!=1:return {'status':'incomplete_pagination','features':[]}
                found=[];total=0
            else:
                try:
                    fc=body['result']['featureCollection']
                    if fc.get('type')!='FeatureCollection':raise ValueError()
                    crs=str(fc.get('crs',''))
                    if crs and not any(x in crs for x in ('4326','CRS84')):return {'status':'unexpected_crs','features':[]}
                    found=fc['features']
                    if not isinstance(found,list) or not all(isinstance(f,dict) and isinstance(f.get('properties'),dict) for f in found):raise ValueError()
                    total=int(body['record']['total'])
                    if total<0:raise ValueError()
                except (ValueError,TypeError,KeyError,AttributeError):return {'status':'invalid_response','features':[]}
            token=json.dumps(found,sort_keys=True,ensure_ascii=False)
            if page>1 and token==previous_page:return {'status':'incomplete_pagination','features':[]}
            previous_page=token
            for f in found:
                # Preserve distinct buildings on a common parcel; PNU is never the dedup key.
                key=json.dumps(f,sort_keys=True,ensure_ascii=False)
                if key not in seen:seen[key]=True;features.append(redact(f))
            if page*self.cfg.get('page_size',100)>=total:
                if len(features)<total:return {'status':'incomplete_pagination','features':[]}
                answer={'status':'ok' if features else 'no_features','features':features,'total':total,'pages':page}
                self.tiles[tile]=answer;return answer
            if not found:return {'status':'incomplete_pagination','features':[]}
        return {'status':'page_limit','features':[]}

    def normalize(self,result):
        items=[];geoms=[];fields={};problems=[];ids=set()
        for f in result.get('features',[]):
            props=f['properties'];item={}
            for key in DEFAULT_FIELDS:
                val,used,conflict=extract(props,key,self.cfg);item[key]=val
                fields.setdefault(key,set()).update(used)
                if conflict:problems.append('conflicting_'+key)
            item['id']=item['id'] or str(f.get('id') or '')
            if not item['id']:problems.append('missing_building_id');continue
            if item['id'] in ids:problems.append('duplicate_building_id');continue
            try:
                geom=shape(f['geometry'])
                if geom.is_empty or not geom.is_valid or geom.geom_type not in ('Polygon','MultiPolygon'):raise ValueError()
                west,south,east,north=geom.bounds
                if not (-180<=west<=east<=180 and -90<=south<=north<=90):raise ValueError()
                # An Asan query must not accept swapped axes or an unrelated region.
                cwest,csouth,ceast,cnorth=self.c['coordinate_bounds']
                if not (cwest<=west<=east<=ceast and csouth<=south<=north<=cnorth):raise ValueError()
            except (TypeError,ValueError,KeyError,AttributeError,IndexError,OverflowError,GEOSException):problems.append('invalid_geometry');continue
            ids.add(item['id'])
            item.update(source=SOURCE,raw_properties=redact(props),geometry=f['geometry'])
            # A code on its own cannot distinguish detached vs multi-household housing.
            use=item['use']
            concrete=[s for s in ('아파트','다가구','다세대','연립') if s in use]
            conflicting=len(concrete)>1 or ('단독주택' in use and any(s in use for s in ('아파트','다세대','연립')))
            if conflicting:
                item['use']='';item['original_use']=use;problems.append('ambiguous_use_name')
            if not item['use']:problems.append('unmapped_or_empty_use_name')
            items.append(item);geoms.append(geom)
        return items,geoms,{k:sorted(v) for k,v in fields.items()},sorted(set(problems))

    def lookup(self,lat,lon,observed_radius_m=0,bypass_cache=False):
        uncertainty=max(self.c.get('building_uncertainty_m',15),2*observed_radius_m)
        raw=self.fetch(lat,lon,uncertainty,bypass_cache)
        self.status=raw['status']
        summary={'status':raw['status'],'source':SOURCE,'layer':self.cfg.get('layer','LT_C_BLDGINFO'),'candidates':[],'match':None,'fields':{},'schema_issues':[]}
        if raw['status']!='ok':return summary
        items,geoms,fields,problems=self.normalize(raw)
        summary.update(fields=fields,schema_issues=problems,feature_count=len(raw['features']))
        # An invalid/skipped competitor must never make the remaining building seem unique.
        fatal=set(problems)-{'unmapped_or_empty_use_name','ambiguous_use_name'}
        if fatal:
            self.status='schema_or_geometry_error'
            return dict(summary,status='schema_or_geometry_error')
        layer=object.__new__(GISBuildings)
        layer.c=self.c;layer.items=items;layer.geoms=geoms;layer.tree=STRtree(geoms) if geoms else None;layer.status='건물 미확인'
        spatial=layer.lookup(lat,lon,observed_radius_m)
        self.status=spatial['status']
        return dict(summary,**spatial,fields=fields,schema_issues=problems,query_status='ok')

    def probe(self,lat,lon):
        before=self.api.count
        result=self.fetch(lat,lon,self.c.get('building_uncertainty_m',15),bypass_cache=True)
        items,geoms,fields,problems=self.normalize(result)
        names=sorted({str(k) for f in result.get('features',[]) for k in f['properties']})
        geometry_ok=bool(items) and not(set(problems)-{'unmapped_or_empty_use_name','ambiguous_use_name'})
        use_ok=geometry_ok and any(item['use'] for item in items)
        schema_ok=geometry_ok and use_ok and 'unmapped_or_empty_use_name' not in problems
        self.last_probe=redact({
          'checked_at_utc':datetime.now(timezone.utc).isoformat(),'status':result['status'],
          'query_verified':result['status']=='ok' and self.api.count>before,
          'geometry_verified':geometry_ok,'use_field_verified':use_ok,'schema_ready':schema_ok,
          'endpoint':ENDPOINT,'layer':self.cfg.get('layer','LT_C_BLDGINFO'),
          'service_url':os.getenv('VWORLD_SERVICE_URL') or self.cfg.get('service_url','http://localhost:8000'),
          'api_requests':self.api.count-before,'feature_count':len(result.get('features',[])),
          'property_names':names,'mapped_fields':fields,'schema_issues':problems,
          'sample_features':result.get('features',[])[:3],
          'note':'서비스 URL/인증 오류는 우회하지 않습니다. a0/a1/a9 등 불투명 필드는 공식 스키마와 확인 후 config.json에서 명시적으로 매핑하세요.'})
        return self.last_probe


def main():
    parser=argparse.ArgumentParser(description='V-World GIS건물통합정보 실조회·속성 검증 (기존 localhost 키는 Windows에서 실행)')
    parser.add_argument('--config',type=Path,default=Path('config.json'))
    parser.add_argument('--lat',type=float,default=36.7782377)
    parser.add_argument('--lon',type=float,default=127.0086517)
    parser.add_argument('--output',type=Path,default=Path('output/vworld_연동검증.json'))
    args=parser.parse_args()
    base=args.config.resolve().parent;load_dotenv(base/'.env',override=False)
    c=deepcopy(json.loads(args.config.read_text(encoding='utf-8-sig')))
    west,south,east,north=c['coordinate_bounds']
    if not (south<=args.lat<=north and west<=args.lon<=east):parser.error('위도·경도 또는 축 순서를 확인하세요')
    # The explicit diagnostic only enables the V-World request, not any other provider.
    c['api']['enabled']=True;c.setdefault('vworld',{})['enabled']=True
    args.output.parent.mkdir(parents=True,exist_ok=True)
    import tempfile
    with tempfile.TemporaryDirectory() as work:
        api=APIClient(c,Path(work))
        try:result=VWorldBuildings(c,api).probe(args.lat,args.lon)
        finally:api.close()
    temporary=args.output.with_suffix('.tmp.json');temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(args.output)
    print(json.dumps({k:result[k] for k in ('status','query_verified','schema_ready','api_requests','feature_count')},ensure_ascii=False))
    print('검증 결과: '+str(args.output.resolve()))
    return 0 if result['query_verified'] and result['schema_ready'] else 2


if __name__=='__main__':sys.exit(main())
