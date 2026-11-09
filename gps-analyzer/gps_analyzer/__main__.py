from __future__ import annotations
import argparse
import json
import logging
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from .pipeline import run


def main():
    parser=argparse.ArgumentParser(description='아산 GPS 체류 후보 분석 및 Excel 통합 보고서')
    parser.add_argument('--input',required=True,type=Path)
    parser.add_argument('--output',type=Path,default=Path('output'))
    parser.add_argument('--work',type=Path,default=Path('work_data'))
    parser.add_argument('--config',type=Path,default=Path('config.json'))
    args=parser.parse_args()
    base=args.config.resolve().parent
    load_dotenv(base/'.env',override=False)
    args.work.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s: %(message)s',handlers=[logging.StreamHandler(),logging.FileHandler(args.work/'analysis.log',encoding='utf-8')])
    status=args.work/'run_status.json'
    # An OS lock is released on crash; prevents concurrent runs from corrupting shared caches.
    lockfile=(args.work/'run.lock').open('a+b')
    try:
        if os.name=='nt':
            import msvcrt
            lockfile.seek(0);lockfile.write(b'0');lockfile.flush();lockfile.seek(0)
            msvcrt.locking(lockfile.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lockfile,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        logging.error('동일 중간결과 폴더에서 다른 분석이 실행 중입니다');return 2
    try:
        c=json.loads(args.config.read_text(encoding='utf-8-sig'))
        for k in ['min_stop_seconds','radius_m','max_gap_seconds','max_path_m','batch_rows']:
            if c[k]<=0: raise ValueError(k+' must be positive')
        if c['max_stop_speed_kmh']<0: raise ValueError('max_stop_speed_kmh must be nonnegative')
        if args.input.resolve()==base or base.is_relative_to(args.input.resolve()): raise ValueError('프로젝트 폴더 전체가 아니라 GPS 원본 전용 폴더를 입력하세요')
        status.write_text(json.dumps({'status':'running'}),encoding='utf-8')
        result=run(args.input,args.output,args.work,c,base)
        status.write_text(json.dumps({'status':'passed','visits':result['visits']},ensure_ascii=False),encoding='utf-8')
        print(json.dumps({'status':'passed','visits':result['visits'],'places':result['sources']['places'],'output':str(args.output.resolve())},ensure_ascii=False));return 0
    except KeyboardInterrupt:
        logging.warning('사용자 중단; 완료된 캐시를 보존합니다')
        status.write_text(json.dumps({'status':'interrupted'},ensure_ascii=False),encoding='utf-8');return 130
    except Exception as e:
        logging.exception('ANALYSIS FAILED; completed input and vehicle caches are retained')
        status.write_text(json.dumps({'status':'failed','error_type':type(e).__name__,'message':str(e)},ensure_ascii=False),encoding='utf-8');return 1
    finally: lockfile.close()


if __name__=='__main__': sys.exit(main())
