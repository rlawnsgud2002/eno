from __future__ import annotations
import hashlib
import json
import logging
import math
import os
import sqlite3
import time
from pathlib import Path
from urllib.parse import unquote
import requests
from shapely.geometry import Point, shape, box
from shapely.strtree import STRtree
from .core import distance_m

LOG=logging.getLogger(__name__)

# Most specific terms first. A generic hospital never implies general hospital.
TYPES=[('주거시설','아파트',['아파트']),('주거시설','다가구주택',['다가구']),('주거시설','다세대주택',['다세대']),('주거시설','연립주택',['연립']),('주거시설','단독주택',['단독주택']),
 ('의료시설','종합병원',['종합병원']),('의료시설','요양병원',['요양병원']),('의료시설','병원',['병원']),('의료시설','의원',['의원','치과의원','한의원']),('의료시설','약국',['약국']),
 ('복지시설','복지관',['복지관']),('복지시설','노인복지시설',['노인복지']),('복지시설','요양시설',['요양시설','요양원']),('복지시설','복지시설',['복지시설']),
 ('교육시설','어린이집',['어린이집']),('교육시설','유치원',['유치원']),('교육시설','학교',['학교']),('교육시설','학원',['학원']),
 ('상업시설','마트',['대형마트','마트']),('상업시설','편의점',['편의점']),('상업시설','음식점',['음식점','일반음식점','휴게음식점','식당']),('상업시설','상가',['상가','판매시설']),
 ('공공시설','행정복지센터',['행정복지센터','주민센터']),('공공시설','공공기관',['공공기관','공공업무시설']),
 ('기타','공장',['공장']),('기타','창고',['창고']),('기타','주차장',['주차장']),('기타','도로변',['도로변']),('기타','공터',['공터'])]


def classify(text):
    for major,minor,terms in TYPES:
        if any(x in str(text) for x in terms): return major,minor
    return '미확인','미확인'


class GISBuildings:
    """Local licensed MOLIT GIS export, WGS84 polygons. No nearest-building assertion."""
    def __init__(self,c,base):
        self.c=c;self.items=[];self.geoms=[];self.tree=None;self.status='미설정'
        if not c.get('gis_file'): return
        p=(base/c['gis_file']).resolve()
        try:
            data=json.loads(p.read_text(encoding='utf-8-sig'))
            crs=str(data.get('crs',''))
            if crs and not any(x in crs for x in ('4326','CRS84')): raise ValueError('GIS must be WGS84 EPSG:4326')
            for feature in data['features']:
                g=shape(feature['geometry']);props=feature.get('properties',{})
                if g.is_empty or not g.is_valid or g.geom_type not in ('Polygon','MultiPolygon'): raise ValueError('invalid building polygon')
                west,south,east,north=g.bounds
                if not (-180<=west<=east<=180 and -90<=south<=north<=90): raise ValueError('GIS coordinates must be WGS84')
                item={k:str(props.get(v) or '') for k,v in c['gis_properties'].items()}
                if not item.get('id'): raise ValueError('stable GIS building id missing')
                self.geoms.append(g);self.items.append(item)
            if len({x['id'] for x in self.items})!=len(self.items): raise ValueError('duplicate building ids: dissolve geometries first')
            self.tree=STRtree(self.geoms);self.status=f'정상 {len(self.items)}개 건물'
        except Exception as e:
            self.items=[];self.geoms=[];self.tree=None
            self.status='실패: '+type(e).__name__+': '+str(e)
            LOG.error('GIS unavailable: %s',self.status)

    def lookup(self,lat,lon,observed_radius_m=0):
        if self.tree is None: return {'status':self.status,'candidates':[],'match':None}
        uncertainty=max(self.c.get('building_uncertainty_m',15),2*observed_radius_m)
        dy=uncertainty/111195;dx=dy/max(.1,math.cos(math.radians(lat)))
        q=Point(lon,lat);near=[];inside=[]
        for index in self.tree.query(box(lon-dx,lat-dy,lon+dx,lat+dy)):
            i=int(index);g=self.geoms[i]
            # Local equirectangular metric for the small search region.
            from shapely.affinity import affine_transform
            sx=111195*math.cos(math.radians(lat));sy=111195
            metric=affine_transform(g,[sx,0,0,sy,-lon*sx,-lat*sy])
            d=metric.distance(Point(0,0))
            if d<=uncertainty:
                item=dict(self.items[i],distance_m=round(d,2),contains=bool(g.covers(q)))
                near.append(item)
                if item['contains']: inside.append(item)
        # The centre must be inside a single building, with no nearby competing footprint.
        match=inside[0] if len(inside)==1 and len(near)==1 else None
        status='건물 위치 일치·실제 방문 미확인' if match else ('인접 건물 혼동' if len(near)>1 else ('인근 체류 추정' if near else '건물 미확인'))
        return {'status':status,'candidates':sorted(near,key=lambda x:(x['distance_m'],x['id'])),'match':match}


def parcel(item):
    pnu=item.get('pnu','')
    if len(pnu)==19 and pnu.isdigit():
        return dict(sigunguCd=pnu[:5],bjdongCd=pnu[5:10],platGbCd='1' if pnu[10]=='2' else '0',bun=pnu[11:15],ji=pnu[15:19])
    keys=['sigunguCd','bjdongCd','platGbCd','bun','ji']
    if all(item.get(k) for k in keys):
        return {k:item[k].zfill(4) if k in ('bun','ji') else item[k] for k in keys}
    return None


class APIClient:
    def __init__(self,c,work):
        self.c=c['api'];self.count=0;self.last_call=0.;self.memory={}
        self.cache=sqlite3.connect(work/'api_cache.sqlite')
        self.cache.execute('CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, provider TEXT, expires REAL, payload TEXT)')
        self.cache.execute('DELETE FROM cache WHERE expires<=?',(time.time(),));self.cache.commit()
        # Disabling retention removes previously permitted Kakao cache too.
        if not self.c.get('kakao_persistent_cache'):
            self.cache.execute("DELETE FROM cache WHERE provider='kakao'");self.cache.commit()

    def close(self): self.cache.close()

    def get(self,provider,url,params,headers=None,bypass_cache=False):
        safeparams={k:v for k,v in params.items() if k not in ('serviceKey','key')}
        scope=hashlib.sha256(str(params.get('key','')).encode()).hexdigest() if provider=='vworld' else ''
        key=hashlib.sha256(json.dumps([provider,url,safeparams,scope] if provider=='vworld' else [provider,url,safeparams],sort_keys=True).encode()).hexdigest()
        if not bypass_cache and key in self.memory: return self.memory[key]
        if not self.c['enabled']: return {'status':'disabled'}
        entry=self.cache.execute('SELECT payload FROM cache WHERE key=? AND expires>?',(key,time.time())).fetchone()
        if entry and not bypass_cache:
            result=json.loads(entry[0]);self.memory[key]=result;return result
        if self.count>=self.c['max_requests_per_run']: return {'status':'quota_limit'}
        result={'status':'failed'}
        for attempt in range(self.c['retries']+1):
            if self.count>=self.c['max_requests_per_run']: break
            wait=self.c['min_interval_seconds']-(time.monotonic()-self.last_call)
            if wait>0: time.sleep(wait)
            self.count+=1;self.last_call=time.monotonic()
            try:
                response=requests.get(url,params=params,headers=headers,timeout=self.c['timeout_seconds'])
                if response.status_code==429 or response.status_code>=500:
                    result={'status':'http_'+str(response.status_code)}
                    if attempt<self.c['retries']: time.sleep(min(2**attempt,8))
                    continue
                if response.status_code>=400:
                    result={'status':'http_'+str(response.status_code)};break
                data=response.json()
                if not isinstance(data,dict):
                    result={'status':'invalid_response'};break
                # A public-data API may return an error inside HTTP 200.
                response_body=data.get('response') or {}
                head=response_body.get('header',{}) if isinstance(response_body,dict) else {}
                if head and str(head.get('resultCode')) not in ('00','0','000'):
                    result={'status':'service_error','code':str(head.get('resultCode'))};break
                if provider=='vworld':
                    from .vworld import response_error,redact
                    problem=response_error(data)
                    if problem:
                        result=problem;break
                    data=redact(data)
                result={'status':'ok','data':data};break
            except (requests.RequestException,ValueError,TypeError,AttributeError):
                result={'status':'network_or_response_error'}
                if attempt<self.c['retries']: time.sleep(min(2**attempt,8))
        if result['status']!='ok':
            LOG.warning('%s request failed: %s',provider,result['status']);return result
        self.memory[key]=result
        ttl=(self.c.get('cache_ttl_hours',0) if provider=='kakao' and self.c.get('kakao_persistent_cache') else self.c.get('public_cache_ttl_hours',24) if provider!='kakao' else 0)
        if ttl>0:
            self.cache.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?,?)',(key,provider,time.time()+3600*ttl,json.dumps(result,ensure_ascii=False)));self.cache.commit()
        return result

    def registry(self,parcel_fields):
        if not self.c.get('registry_enabled',True): return {'status':'disabled'}
        key=os.getenv('DATA_GO_KR_SERVICE_KEY')
        if not key: return {'status':'missing_key'}
        if not parcel_fields: return {'status':'missing_parcel'}
        # Return all titles for a parcel. Multiple buildings require exact matching later.
        results=[];page=1
        while True:
            result=self.get('registry','https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo',dict(parcel_fields,serviceKey=unquote(key),_type='json',numOfRows=100,pageNo=page))
            if result['status']!='ok': return result
            container=result['data'].get('response',{})
            if not isinstance(container,dict): return {'status':'invalid_response'}
            body=container.get('body',{})
            if not isinstance(body,dict): return {'status':'invalid_response'}
            items=body.get('items') or {};items=items.get('item',[]) if isinstance(items,dict) else []
            if isinstance(items,dict): items=[items]
            if not isinstance(items,list) or not all(isinstance(x,dict) for x in items): return {'status':'invalid_response'}
            results.extend(items)
            total=int(body.get('totalCount',len(results)) or 0)
            if len(results)>=total: return {'status':'ok','items':results}
            if not items: return {'status':'incomplete_pagination','items':results}
            page+=1

    def kakao(self,lat,lon,need_poi):
        key=os.getenv('KAKAO_REST_API_KEY')
        if not key: return {'status':'missing_key'}
        headers={'Authorization':'KakaoAK '+key}
        address=self.get('kakao','https://dapi.kakao.com/v2/local/geo/coord2address.json',{'x':lon,'y':lat,'input_coord':'WGS84'},headers)
        docs=address.get('data',{}).get('documents',[])
        if not isinstance(docs,list) or not all(isinstance(d,dict) and all(d.get(k) is None or isinstance(d.get(k),dict) for k in ('address','road_address')) for d in docs):
            address={'status':'invalid_response'};docs=[]
        pois=[];statuses=[]
        if need_poi:
            calls=[('category.json',{'category_group_code':'HP8'}),('category.json',{'category_group_code':'PM9'}),('keyword.json',{'query':'복지관'})]
            for endpoint,extra in calls:
                r=self.get('kakao','https://dapi.kakao.com/v2/local/search/'+endpoint,dict(x=lon,y=lat,radius=80,sort='distance',size=15,**extra),headers)
                statuses.append(r['status'])
                if r['status']=='ok':
                    found=r['data'].get('documents',[])
                    if isinstance(found,list) and all(isinstance(x,dict) for x in found): pois.extend(found)
                    else: statuses[-1]='invalid_response'
        return {'status':address['status'],'address':docs,'pois':pois,'poi_statuses':statuses}
