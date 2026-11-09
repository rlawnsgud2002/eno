from __future__ import annotations
import csv
import hashlib
import json
import logging
import math
import re
from collections import Counter
from datetime import date, datetime, time
from pathlib import Path
import duckdb
from openpyxl import load_workbook

LOG = logging.getLogger(__name__)
ALIASES = {
 'time':['GPS수집시간','수집시간','일시','timestamp','datetime','gps_time','시간'],
 'lat':['위도','latitude','lat'], 'lon':['경도','longitude','lon','lng'],
 'speed':['속도','speed','차량속도'], 'vehicle':['차량번호','호차','vehicle','vehicle_id'],
 'deleted':['삭제여부','deleted']}


def sqlstr(value):
    return "'" + str(value).replace("'", "''") + "'"


def file_hash(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''): h.update(b)
    return h.hexdigest()


def vehicle_number(value):
    if value is None: return None
    m=re.fullmatch(r'\s*(?:ASAN[_ -]?)?(\d+)(?:호차|호)?\s*',str(value), re.I)
    return int(m.group(1)) if m else None


def filename_info(path):
    m=re.search(r'ASAN[_ -](\d+)[_ -](\d{4})[_ -](\d{2})[_ -](\d{2})',path.stem,re.I)
    if m: return int(m[1]),date(int(m[2]),int(m[3]),int(m[4]))
    m=re.search(r'(\d+)호차',path.stem)
    return (int(m[1]) if m else None),None


def parse_time(value, fallback_date):
    if isinstance(value,datetime): t=value
    elif isinstance(value,time) and fallback_date: t=datetime.combine(fallback_date,value)
    else:
        s=str(value).strip()
        try: t=datetime.fromisoformat(s)
        except ValueError:
            if not fallback_date: raise ValueError('datetime_missing')
            t=datetime.combine(fallback_date,time.fromisoformat(s))
    if t.tzinfo is not None: raise ValueError('timezone_aware_timestamp')
    return t


def normalize_header(v): return re.sub(r'[\s_\-()]','',str(v or '')).lower()


def find_header(sheet, configured):
    rows=sheet.iter_rows(values_only=True)
    for line in range(1,31):
        row=next(rows,None)
        if row is None: break
        cols={normalize_header(v):i for i,v in enumerate(row)}
        found={}
        for key,aliases in ALIASES.items():
            choices=[configured[key]] if key in configured else aliases
            for alias in choices:
                if normalize_header(alias) in cols:
                    found[key]=cols[normalize_header(alias)];break
        if {'time','lat','lon'}<=found.keys(): return rows,found,line
    raise ValueError(f'{sheet.title}: 필수 열 time/lat/lon 인식 실패')


def ingest_file(path, work, c, db):
    digest=file_hash(path)
    sig=hashlib.sha256(json.dumps({k:c[k] for k in ['start_date','end_date','vehicles','coordinate_bounds','columns']},sort_keys=True).encode()).hexdigest()
    key=hashlib.sha256((str(path.resolve())+digest+sig+'ingest-v2').encode()).hexdigest()
    parquet=work/'parquet'/f'{key}.parquet'
    meta=parquet.with_suffix('.json')
    if parquet.exists() and meta.exists():
        LOG.info('SKIP completed file %s',path.name)
        return parquet,json.loads(meta.read_text(encoding='utf-8'))
    LOG.info('INGEST %s',path.name)
    csvpath=parquet.with_suffix('.tmp.csv'); tmpparquet=parquet.with_suffix('.tmp.parquet')
    counts=Counter(); groups=Counter()
    fallback_vehicle,fallback_date=filename_info(path)
    wb=load_workbook(path,read_only=True,data_only=True)
    try:
        with csvpath.open('w',newline='',encoding='utf-8') as f:
            writer=csv.writer(f);writer.writerow(['vehicle','ts','lat','lon','speed','source','sheet','row_no'])
            for sheet in wb:
                rows,cols,header_line=find_header(sheet,c['columns'])
                for rowno,row in enumerate(rows,header_line+1):
                    if not any(x is not None for x in row): continue
                    counts['raw']+=1
                    v=fallback_vehicle;month='unknown'
                    def value(k):
                        i=cols.get(k);return row[i] if i is not None and i<len(row) else None
                    reason='valid'
                    try:
                        explicit=vehicle_number(value('vehicle'))
                        if value('vehicle') is not None and explicit is None: raise ValueError('invalid_vehicle')
                        if explicit is not None:
                            if fallback_vehicle is not None and explicit!=fallback_vehicle: raise ValueError('vehicle_filename_mismatch')
                            v=explicit
                        if v not in c['vehicles']: raise ValueError('vehicle_out_of_scope')
                        t=parse_time(value('time'),fallback_date);month=t.strftime('%Y-%m')
                        if not(c['start_date']<=str(t.date())<=c['end_date']): raise ValueError('date_out_of_scope')
                        if str(value('deleted')).upper() in ('Y','YES','TRUE','1'): raise ValueError('deleted')
                        lat,lon=float(value('lat')),float(value('lon'))
                        west,south,east,north=c['coordinate_bounds']
                        if not(math.isfinite(lat) and math.isfinite(lon) and south<=lat<=north and west<=lon<=east): raise ValueError('invalid_coordinate')
                        s=value('speed');speed=None if s is None or str(s).strip()=='' else float(s)
                        if speed is not None and (not math.isfinite(speed) or speed<0 or speed>300): raise ValueError('invalid_speed')
                        writer.writerow([v,t.isoformat(' '),lat,lon,speed,str(path.resolve()),sheet.title,rowno])
                    except (ValueError,TypeError,OverflowError) as e:
                        reason=str(e) if str(e) in {'invalid_vehicle','vehicle_filename_mismatch','vehicle_out_of_scope','date_out_of_scope','deleted','invalid_coordinate','invalid_speed','timezone_aware_timestamp'} else 'invalid_value'
                    counts[reason]+=1;groups[(str(v),month,reason)]+=1
        types="{'vehicle':'INTEGER','ts':'TIMESTAMP','lat':'DOUBLE','lon':'DOUBLE','speed':'DOUBLE','source':'VARCHAR','sheet':'VARCHAR','row_no':'BIGINT'}"
        db.execute(f"COPY (SELECT * FROM read_csv({sqlstr(csvpath)}, header=true, columns={types})) TO {sqlstr(tmpparquet)} (FORMAT PARQUET, COMPRESSION ZSTD)")
        tmpparquet.replace(parquet)
        info={'source':str(path.resolve()),'sha256':digest,'counts':dict(counts),'groups':[{'vehicle':a,'month':b,'reason':d,'count':n} for (a,b,d),n in sorted(groups.items())]}
        tempmeta=meta.with_suffix('.json.tmp');tempmeta.write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8');tempmeta.replace(meta)
        LOG.info('COMPLETE %s raw=%s valid=%s',path.name,counts['raw'],counts['valid'])
        return parquet,info
    finally:
        wb.close();csvpath.unlink(missing_ok=True);tmpparquet.unlink(missing_ok=True)


def ingest_folder(folder, work, c, db):
    (work/'parquet').mkdir(parents=True,exist_ok=True)
    files=sorted(p for p in folder.rglob('*') if p.is_file() and p.suffix.lower() in ('.xlsx','.xlsm') and not p.name.startswith('~$') and not p.resolve().is_relative_to(work.resolve()))
    if not files: raise ValueError('입력 폴더에 .xlsx/.xlsm 파일이 없습니다')
    if any(folder.rglob('*.xls')): LOG.warning('구형 .xls 파일은 지원하지 않습니다. 원본 사본을 .xlsx로 변환하세요.')
    manifest=[];parquets=[]
    for path in files:
        try:
            parquet,info=ingest_file(path,work,c,db);parquets.append(str(parquet));manifest.append(info)
        except Exception:
            LOG.exception('FILE FAILED %s; completed files retained',path.name)
            raise
    sources=','.join(sqlstr(p) for p in parquets)
    db.execute(f'CREATE OR REPLACE VIEW raw AS SELECT * FROM read_parquet([{sources}])')
    db.execute('''CREATE OR REPLACE TABLE unique_points AS
      SELECT vehicle,ts,lat,lon,speed,min(source) source FROM raw
      GROUP BY vehicle,ts,lat,lon,speed''')
    db.execute('''CREATE OR REPLACE TABLE time_conflicts AS SELECT vehicle,ts,count(*) n FROM unique_points GROUP BY vehicle,ts HAVING count(*)>1''')
    db.execute('''CREATE OR REPLACE TABLE points AS SELECT u.* FROM unique_points u
      ANTI JOIN time_conflicts c ON u.vehicle=c.vehicle AND u.ts=c.ts''')
    totals={k:sum(m['counts'].get(k,0) for m in manifest) for k in ['raw','valid']}
    totals['invalid']=totals['raw']-totals['valid']
    totals['deduplicated']=db.execute('SELECT count(*) FROM unique_points').fetchone()[0]
    totals['duplicates']=totals['valid']-totals['deduplicated']
    totals['conflicting_points']=db.execute('SELECT coalesce(sum(n),0) FROM time_conflicts').fetchone()[0]
    totals['clean']=db.execute('SELECT count(*) FROM points').fetchone()[0]
    assert totals['raw']==totals['invalid']+totals['duplicates']+totals['conflicting_points']+totals['clean']
    (work/'input_validation.json').write_text(json.dumps({'files':manifest,'totals':totals},ensure_ascii=False,indent=2),encoding='utf-8')
    return totals
