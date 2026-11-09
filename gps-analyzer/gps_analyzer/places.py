from __future__ import annotations
import csv
import json
import logging
import sqlite3
from .core import stable_id
from .sources import GISBuildings, APIClient, classify, parcel
from .vworld import VWorldBuildings

LOG=logging.getLogger(__name__)
FIELDS=['place_id','name','major','minor','address','road_address','lat','lon','confidence','basis','sources','review','identification','gis_result','registry_result','kakao_result','comparison','vworld_result']


class Places:
    def __init__(self,c,base,work):
        self.c=c;self.gis=GISBuildings(c,base);self.api=APIClient(c,work);self.vworld=VWorldBuildings(c,self.api);self.done={}
        self.db=sqlite3.connect(work/'places.sqlite')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS places(id TEXT PRIMARY KEY, lat REAL, lon REAL, label TEXT, manual TEXT);
          CREATE TABLE IF NOT EXISTS aliases(identity TEXT PRIMARY KEY, place_id TEXT);
          CREATE TABLE IF NOT EXISTS visit_places(visit_id TEXT PRIMARY KEY, place_id TEXT);''')
        self.overrides={}
        p=base/c['overrides_file']
        if p.exists():
            with p.open(encoding='utf-8-sig',newline='') as f:
                for r in csv.DictReader(f):
                    if r.get('place_id'): self.overrides[r['place_id']]=r

    def close(self): self.db.close();self.api.close()

    def building_lookup(self,lat,lon,observed_radius_m):
        local=self.gis.lookup(lat,lon,observed_radius_m)
        local_match=local['match']
        # A complete local public layer avoids unnecessary external requests.
        if local_match and classify(local_match.get('use',''))[1]!='미확인':
            remote={'status':'not_needed','candidates':[],'match':None}
        else:
            try: remote=self.vworld.lookup(lat,lon,observed_radius_m)
            except Exception as e:
                LOG.warning('V-World adapter failure: %s',type(e).__name__)
                remote={'status':'parser_error','candidates':[],'match':None}
        # Never resolve ambiguous local neighbouring buildings by silently ignoring them.
        if not local_match and not local.get('candidates') and remote.get('match'):
            result=dict(remote);result['local_gis']=local
        else:
            result=dict(local)
            if local_match and remote.get('match'):
                other=remote['match']
                # Only a shared stable building ID permits attributes to be combined.
                if local_match['id']==other['id']:
                    result['match']=dict(local_match)
                    if classify(result['match'].get('use',''))[1]=='미확인' and classify(other.get('use',''))[1]!='미확인':
                        result['match']['use']=other.get('use','')
                        result['match']['supplemental_source']=other.get('source','V-World')
        result['vworld']=remote
        return result

    def resolve(self,visit_id,lat,lon,observed_radius_m=0):
        gis=self.building_lookup(lat,lon,observed_radius_m);match=gis['match']
        identity='GIS:'+match['id'] if match else f'GPS:{lat:.7f},{lon:.7f}'
        row=self.db.execute('SELECT place_id FROM aliases WHERE identity=?',(identity,)).fetchone()
        prior=self.db.execute('SELECT place_id FROM visit_places WHERE visit_id=?',(visit_id,)).fetchone()
        pid=row[0] if row else prior[0] if prior else stable_id('P_',identity)
        manual_conflict=False
        if row and prior and row[0]!=prior[0]:
            old=self.db.execute('SELECT manual FROM places WHERE id=?',(prior[0],)).fetchone()
            if old and old[0] and json.loads(old[0]):
                pid=prior[0];manual_conflict=True
        # An explicit canonical ID is the only way to merge manual places.
        override=self.overrides.get(pid,{})
        canonical=override.get('canonical_place_id','').strip() or pid
        if canonical!=pid:
            self.db.execute('UPDATE aliases SET place_id=? WHERE place_id=?',(canonical,pid))
            self.db.execute('UPDATE visit_places SET place_id=? WHERE place_id=?',(canonical,pid))
            pid=canonical
        if not manual_conflict:
            self.db.execute('INSERT OR REPLACE INTO aliases VALUES (?,?)',(identity,pid))
        self.db.execute('INSERT OR REPLACE INTO visit_places VALUES (?,?)',(visit_id,pid))
        self.db.commit()
        if pid in self.done: return pid
        label=self.label(pid,lat,lon,gis)
        if manual_conflict: label['review']+='; 기존 수동 장소 ID·새 건물 연결 충돌·자동 통합 보류'
        old=self.db.execute('SELECT manual FROM places WHERE id=?',(pid,)).fetchone()
        manual=json.loads(old[0]) if old and old[0] else {}
        for key in ('name','major','minor','address','road_address'):
            if override.get(key,'').strip(): manual[key]=override[key].strip()
        if manual:
            label.update(manual);label['confidence']=1.0;label['sources']+='; 수동 수정'
            label['basis']+='; 사용자 확인값 유지';label['identification']='수동 확인'
            # GPS/other source warnings remain visible even after manual identification.
        self.db.execute('INSERT OR REPLACE INTO places VALUES (?,?,?,?,?)',(pid,lat,lon,json.dumps(label,ensure_ascii=False),json.dumps(manual,ensure_ascii=False)))
        self.db.commit();self.done[pid]=label
        return pid

    def label(self,pid,lat,lon,gis):
        m=gis['match'];reviews=[];basis=[];sources=['GPS'];major=minor='미확인'
        name='미확인 장소 '+pid[-6:];addr=road='';confidence=.1
        if m:
            name=m.get('name') or name;addr=m.get('address','');road=m.get('road_address','')
            major,minor=classify(m.get('use',''))
            sources.append(m.get('source',self.c['gis_source']))
            if m.get('supplemental_source'): sources.append(m['supplemental_source'])
            basis.append(f"단일 건물 폴리곤 내부·{self.c.get('building_uncertainty_m',15)}m 오차범위 내 경쟁 건물 없음")
            basis.append('공공 건물 주용도명: '+(m.get('use') or '미확인'))
            confidence=.7
        else:
            reviews.append(gis['status']);basis.append('정확한 건물 식별 불가; 거리만으로 통합하지 않음')
        vworld=gis.get('vworld',{'status':'not_needed'})
        if vworld.get('schema_issues'): reviews.append('V-World 속성 확인 필요: '+', '.join(vworld['schema_issues']))
        vworld_query=vworld.get('query_status',vworld['status'])
        if vworld_query not in ('ok','not_needed','disabled','missing_key','no_features'):
            reviews.append('V-World 확인 보류: '+vworld['status'])
        registry={'status':'not_needed'}
        if m and (minor=='미확인' or self.c['api'].get('verify_known_use',False)):
            try:
                registry=self.api.registry(parcel(m))
            except Exception as e:
                LOG.warning('Registry parser failure: %s',type(e).__name__)
                registry={'status':'parser_error'}
            if registry['status']=='ok':
                items=registry['items'];exact=[x for x in items if m.get('registry_id') and str(x.get('mgmBldrgstPk'))==m['registry_id']]
                selected=exact[0] if len(exact)==1 else items[0] if len(items)==1 else None
                if selected:
                    rmajor,rminor=classify(str(selected.get('etcPurps',''))+' '+str(selected.get('mainPurpsCdNm','')))
                    registry['selected']=selected;sources.append('국토교통부 건축물대장정보 API')
                    if rminor!='미확인':
                        if minor!='미확인' and minor!=rminor:
                            reviews.append('GIS·건축물대장 용도 불일치');major=minor='미확인';confidence=.35
                            basis.append('정보원 용도 불일치로 확정 보류')
                        else:
                            major,minor=rmajor,rminor;confidence=.85;basis.append('건축물대장 표제부 용도 확인')
                    if name.startswith('미확인') and selected.get('bldNm'): name=selected['bldNm']
                    if not addr: addr=selected.get('platPlc','')
                    if not road: road=selected.get('newPlatPlc','')
                else: reviews.append('동일 지번 복수 건물·대장 매칭 불가' if items else '건축물대장 검색 결과 없음')
            elif registry['status'] not in ('disabled','missing_key','missing_parcel'):
                reviews.append('건축물대장 조회 실패: '+registry['status'])
        need_kakao=not m or name.startswith('미확인') or not addr
        try:
            kakao=self.api.kakao(lat,lon,need_poi=not m or name.startswith('미확인')) if need_kakao else {'status':'not_needed'}
        except Exception as e:
            LOG.warning('Kakao parser failure: %s',type(e).__name__)
            kakao={'status':'parser_error'}
        if kakao['status']=='ok':
            sources.append('카카오 Local API');docs=kakao.get('address',[])
            if docs:
                ka=docs[0].get('address') or {};kr=docs[0].get('road_address') or {}
                a=ka.get('address_name','');r=kr.get('address_name','')
                # Keep all addresses in evidence; do not silently overwrite authoritative GIS.
                if addr and a and ''.join(addr.split())!=''.join(a.split()): reviews.append('주소 불일치·표기 차이 포함')
                if not addr: addr=a
                if not road: road=r
                building=kr.get('building_name','')
                if name.startswith('미확인') and building:
                    name=building+' (좌표 기반 추정)';confidence=max(confidence,.35)
                    basis.append('카카오 좌표 주소의 건물명·방문 대상 미확정')
            pois=kakao.get('pois',[])
            if pois:
                # Nearest POI is evidence only, including hospital/pharmacy inside mixed buildings.
                reviews.append('주변 시설 후보 존재·실제 방문 시설 미확정')
                basis.append('주변 검색 결과는 후보로만 기록')
        elif kakao['status'] not in ('not_needed','disabled','missing_key'):
            reviews.append('카카오 조회 실패: '+kakao['status'])
        if minor=='미확인': reviews.append('건물 용도 미확인')
        if name.startswith('미확인'): reviews.append('장소명 미확인')
        if m and gis['status']: basis.append(gis['status'])
        comparison={'GIS':{'status':gis['status'],'name':m.get('name','') if m else '', 'use':m.get('use','') if m else '', 'address':m.get('address','') if m else ''},
          '건축물대장':{'status':registry['status'],'use':registry.get('selected',{}).get('mainPurpsCdNm',''),'address':registry.get('selected',{}).get('platPlc','')},
          'V-World':vworld,
          '카카오':{'status':kakao['status'],'주소':kakao.get('address',[]),'주변장소후보':kakao.get('pois',[])},
          '판정':'; '.join(reviews) or '대조 가능한 정보원 내 불일치 없음·조회하지 않은 정보원은 확인하지 않음'}
        return dict(zip(FIELDS,[pid,name,major,minor,addr,road,lat,lon,confidence,'; '.join(basis),'; '.join(sources),'; '.join(dict.fromkeys(reviews)),gis['status'],json.dumps(gis,ensure_ascii=False),json.dumps(registry,ensure_ascii=False),json.dumps(kakao,ensure_ascii=False),json.dumps(comparison,ensure_ascii=False),json.dumps(vworld,ensure_ascii=False)]))
