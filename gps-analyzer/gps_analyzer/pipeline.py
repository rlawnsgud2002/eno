from __future__ import annotations
import csv
import hashlib
import json
import logging
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path
import duckdb
from .core import Detector, Point, Stay
from .ingest import ingest_folder, sqlstr
from .places import Places, FIELDS
from .report import candidate_report, make_report, iter_query

LOG=logging.getLogger(__name__)
REPORT_NAME='아산시_21대차량_2026년4월~8월_방문장소_통합분석.xlsx'


def detect_all(db,work,c):
    cache=work/'segments';cache.mkdir(exist_ok=True)
    columns=[f.name for f in fields(Stay)]
    types="{'visit_id':'VARCHAR','vehicle':'INTEGER','start':'TIMESTAMP','end':'TIMESTAMP','duration_s':'DOUBLE','lat':'DOUBLE','lon':'DOUBLE','point_count':'BIGINT','max_gap_s':'DOUBLE','path_m':'DOUBLE','max_radius_m':'DOUBLE','reason':'VARCHAR','quality':'VARCHAR','eligible':'BOOLEAN'}"
    outputs=[]
    params={k:c[k] for k in ['min_stop_seconds','radius_m','max_gap_seconds','max_stop_speed_kmh','max_path_m','jump_speed_kmh']}
    for (vehicle,) in db.execute('SELECT DISTINCT vehicle FROM points ORDER BY vehicle').fetchall():
        fingerprint=db.execute('SELECT count(*),bit_xor(hash(ts,lat,lon,speed)) FROM points WHERE vehicle=?',[vehicle]).fetchone()
        key=hashlib.sha256(json.dumps([vehicle,fingerprint,params,'detector-v5',duckdb.__version__],sort_keys=True).encode()).hexdigest()
        target=cache/f'{vehicle}_{key}.parquet';outputs.append(str(target))
        if target.exists(): LOG.info('SKIP detected vehicle %s',vehicle);continue
        temporary=target.with_suffix('.csv');part=target.with_suffix('.tmp.parquet')
        cursor=db.cursor();cursor.execute('SELECT vehicle,ts,lat,lon,speed FROM points WHERE vehicle=? ORDER BY ts',[vehicle])
        def points():
            while True:
                rows=cursor.fetchmany(c['batch_rows'])
                if not rows: return
                for row in rows: yield Point(*row)
        try:
            with temporary.open('w',encoding='utf-8',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=columns);writer.writeheader()
                for segment in Detector(c).detect(points()): writer.writerow(asdict(segment))
            db.execute(f'COPY (SELECT * FROM read_csv({sqlstr(temporary)},header=true,columns={types})) TO {sqlstr(part)} (FORMAT PARQUET,COMPRESSION ZSTD)')
            part.replace(target);LOG.info('DETECTED vehicle %s',vehicle)
        finally:
            cursor.close();temporary.unlink(missing_ok=True);part.unlink(missing_ok=True)
    if outputs:
        db.execute('CREATE OR REPLACE TABLE segments AS SELECT * FROM read_parquet(['+','.join(sqlstr(x) for x in outputs)+'])')
    else:
        db.execute('''CREATE OR REPLACE TABLE segments(visit_id VARCHAR,vehicle INTEGER,start TIMESTAMP,"end" TIMESTAMP,duration_s DOUBLE,lat DOUBLE,lon DOUBLE,point_count BIGINT,max_gap_s DOUBLE,path_m DOUBLE,max_radius_m DOUBLE,reason VARCHAR,quality VARCHAR,eligible BOOLEAN)''')
    db.execute('CREATE OR REPLACE TABLE visits AS SELECT *, CAST(NULL AS VARCHAR) place_id FROM segments WHERE eligible')


def label_places(db,work,c,base):
    resolver=Places(c,base,work)
    schema=','.join(f'{f} '+('DOUBLE' if f in ('lat','lon','confidence') else 'VARCHAR') for f in FIELDS)
    db.execute(f'CREATE OR REPLACE TABLE labels({schema})')
    cursor=db.cursor();cursor.execute('SELECT visit_id,lat,lon,max_radius_m FROM visits ORDER BY start,vehicle,visit_id')
    try:
        while True:
            rows=cursor.fetchmany(c['batch_rows'])
            if not rows: break
            for visit_id,lat,lon,observed_radius_m in rows:
                pid=resolver.resolve(visit_id,lat,lon,observed_radius_m)
                db.execute('UPDATE visits SET place_id=? WHERE visit_id=?',[pid,visit_id])
        labels=list(resolver.done.values())
        if labels: db.executemany('INSERT INTO labels VALUES ('+','.join('?' for _ in FIELDS)+')',[[x[f] for f in FIELDS] for x in labels])
        stats={'gis_status':resolver.gis.status,'vworld_status':resolver.vworld.status,'api_requests':resolver.api.count,'places':len(labels),'review_places':sum(bool(x['review']) for x in labels)}
        with (work/'source_evidence.jsonl').open('w',encoding='utf-8') as f:
            for x in labels: f.write(json.dumps(x,ensure_ascii=False)+'\n')
        return stats
    finally: cursor.close();resolver.close()


def validation(db,work,c,totals,source_stats):
    def scalar(sql,args=None): return db.execute(sql,args or []).fetchone()[0]
    visits=scalar('SELECT count(*) FROM visits')
    checks={
      'input_balance': totals['raw']==sum(totals[k] for k in ['invalid','duplicates','conflicting_points','clean']),
      'visit_ids_unique':scalar('SELECT count(DISTINCT visit_id) FROM visits')==visits,
      'minimum_duration':scalar('SELECT count(*) FROM visits WHERE duration_s<?',[c['min_stop_seconds']])==0,
      'gap_limit':scalar('SELECT count(*) FROM visits WHERE max_gap_s>?',[c['max_gap_seconds']])==0,
      'duration_matches_endpoints':scalar('SELECT count(*) FROM visits WHERE abs(epoch("end"-start)-duration_s)>.001')==0,
      'radius_limit':scalar('SELECT count(*) FROM visits WHERE max_radius_m>?',[c['radius_m']])==0,
      'path_limit':scalar('SELECT count(*) FROM visits WHERE path_m>?',[c['max_path_m']])==0,
      'all_visits_have_places':scalar('SELECT count(*) FROM visits v JOIN labels p USING(place_id)')==visits,
      'vehicle_total_matches':scalar('SELECT coalesce(sum(n),0) FROM (SELECT count(*) n FROM visits GROUP BY vehicle)')==visits,
      'monthly_total_matches':scalar('SELECT coalesce(sum(n),0) FROM (SELECT count(*) n FROM visits GROUP BY year(start),month(start))')==visits,
      'place_total_matches':scalar('SELECT coalesce(sum(n),0) FROM (SELECT count(*) n FROM visits GROUP BY place_id)')==visits,
      'non_overlapping_visits':scalar('''SELECT count(*) FROM (SELECT *, lag("end") OVER (PARTITION BY vehicle ORDER BY start) previous_end FROM visits) WHERE previous_end>=start''')==0,
    }
    covered=db.execute("SELECT vehicle,strftime(ts,'%Y-%m'),count(*),min(ts),max(ts) FROM points GROUP BY vehicle,strftime(ts,'%Y-%m') ORDER BY 1,2").fetchall()
    covered_pairs={(r[0],r[1]) for r in covered}
    missing=[f'{v}호차 {m}월' for v in c['vehicles'] for m in range(4,9) if (v,f'2026-{m:02d}') not in covered_pairs]
    report={'status':'passed' if all(checks.values()) else 'failed','input':totals,'visits':visits,
            'short_segments':scalar('SELECT count(*) FROM segments WHERE duration_s<?',[c['min_stop_seconds']]),
            'rejected_long_segments':scalar('SELECT count(*) FROM segments WHERE NOT eligible AND duration_s>=?',[c['min_stop_seconds']]),
            'checks':checks,'sources':source_stats,'coverage':[dict(vehicle=v,month=m,records=n,first=str(a),last=str(b)) for v,m,n,a,b in covered],
            'missing_vehicle_months':missing,'notes':['GPS 체류 후보 횟수이며 승하차·실제 시설 방문 확인 횟수가 아닙니다.','월별 집계는 체류 시작시각 기준입니다.','라벨 신뢰도는 규칙 점수이며 통계적 확률이 아닙니다.']}
    (work/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if not all(checks.values()): raise ValueError('validation failed: '+str([k for k,v in checks.items() if not v]))
    return report


def export_ledger(db,work,out):
    inputmeta=json.loads((work/'input_validation.json').read_text(encoding='utf-8'))
    groups={}
    for file in inputmeta['files']:
        for g in file['groups']:
            key=(g['vehicle'],g['month']);x=groups.setdefault(key,{'raw':0,'valid':0,'invalid':0})
            x['raw']+=g['count'];x['valid' if g['reason']=='valid' else 'invalid']+=g['count']
    def tally(table):
        return {(str(v),m):n for v,m,n in db.execute(f"SELECT vehicle,strftime(ts,'%Y-%m'),count(*) FROM {table} GROUP BY 1,2").fetchall()}
    unique=tally('unique_points');clean=tally('points')
    with (out/'차량월별_원본검증.csv').open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.writer(f);w.writerow(['차량번호','월','원본','정상좌표등','정제제외','중복제외','동시각충돌제외','최종정상','검증일치'])
        for (v,m),x in sorted(groups.items()):
            u=unique.get((v,m),0);n=clean.get((v,m),0)
            w.writerow([v,m,x['raw'],x['valid'],x['invalid'],x['valid']-u,u-n,n,x['raw']==x['invalid']+(x['valid']-u)+(u-n)+n])


def run(input_dir,output_dir,work_dir,c,base):
    work=Path(work_dir).resolve();out=Path(output_dir).resolve();inp=Path(input_dir).resolve()
    # Keep generated Excel out of input discovery, including custom output locations.
    if out==inp or out.is_relative_to(inp): raise ValueError('출력 폴더는 입력 폴더 밖으로 지정하세요')
    if work==inp: raise ValueError('중간결과 폴더와 입력 폴더는 달라야 합니다')
    out.mkdir(parents=True,exist_ok=True);work.mkdir(parents=True,exist_ok=True)
    db=duckdb.connect(str(work/'analysis.duckdb'))
    try:
        db.execute('SET memory_limit='+sqlstr(c['duckdb_memory_limit']))
        db.execute('SET temp_directory='+sqlstr(work/'duckdb_tmp'))
        db.execute('SET preserve_insertion_order=false')
        totals=ingest_folder(inp,work,c,db)
        detect_all(db,work,c)
        source_stats=label_places(db,work,c,base)
        result=validation(db,work,c,totals,source_stats)
        candidates=out/'체류후보_검증.xlsx';report=out/REPORT_NAME
        tempc=candidates.with_suffix('.tmp.xlsx');tempr=report.with_suffix('.tmp.xlsx')
        candidate_report(db,tempc);tempc.replace(candidates)
        make_report(db,tempr,c);tempr.replace(report)
        export_ledger(db,work,out)
        for file in ['validation.json','input_validation.json','source_evidence.jsonl']:
            (out/file).write_bytes((work/file).read_bytes())
        LOG.info('SUCCESS visits=%s places=%s api_requests=%s',result['visits'],source_stats['places'],source_stats['api_requests'])
        return result
    finally: db.close()
