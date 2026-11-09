from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, date
import xlsxwriter

MAX_ROWS=1048576
SHEETS=['전체 방문 장소 순위','월별 방문 통계','차량별 방문 통계','방문 상세 기록','장소 자동 분류','시설 유형별 통계','미확인 및 검토 장소']


class Excel:
    def __init__(self,path,max_rows=MAX_ROWS):
        self.book=xlsxwriter.Workbook(path,{'constant_memory':True,'strings_to_formulas':False,'strings_to_urls':False,'remove_timezone':True})
        self.max_rows=max_rows
        self.header=self.book.add_format({'bold':True,'bg_color':'#17365D','font_color':'white','text_wrap':True})
        self.decimal=self.book.add_format({'num_format':'0.00'})
        self.coord=self.book.add_format({'num_format':'0.0000000'})
        self.dt=self.book.add_format({'num_format':'yyyy-mm-dd hh:mm:ss'})
        self.day=self.book.add_format({'num_format':'yyyy-mm-dd'})
        self.percent=self.book.add_format({'num_format':'0%'})
        self.integer=self.book.add_format({'num_format':'#,##0'})

    def table(self,name,headers,rows,chart=None):
        part=0;sheet=None;index=0;sheets=[]
        def start():
            nonlocal part,sheet,index
            part+=1;actual=name if part==1 else name[:25]+'_'+str(part)
            sheet=self.book.add_worksheet(actual);sheets.append(actual);sheet.freeze_panes(1,2)
            sheet.set_row(0,32)
            for j,h in enumerate(headers):
                sheet.write(0,j,h,self.header)
                width=14
                if any(k in h for k in ['주소','근거','검토','출처','결과','품질']): width=40
                elif any(k in h for k in ['시각','ID','장소명']): width=25
                sheet.set_column(j,j,width)
            index=1
        def finish():
            sheet.autofilter(0,0,max(1,index-1),len(headers)-1)
            if chart and index>1:
                # Charts show up to 30 rows for readability; tables always include every place.
                last=min(index-1,30);ch=self.book.add_chart({'type':'column'})
                for value_col,label in chart['series']:
                    ch.add_series({'name':label,'categories':[sheet.name,1,chart['category'],last,chart['category']],
                                   'values':[sheet.name,1,value_col,last,value_col]})
                ch.set_title({'name':chart['title']+' (표에는 전체 장소 포함)'})
                ch.set_legend({'position':'bottom'});ch.set_size({'width':850,'height':400})
                sheet.insert_chart(1,len(headers)+1,ch)
        start()
        for row in rows:
            if index>=self.max_rows: finish();start()
            for j,v in enumerate(row):
                h=headers[j]
                if v is None: continue
                if isinstance(v,datetime): sheet.write_datetime(index,j,v,self.dt)
                elif isinstance(v,date): sheet.write_datetime(index,j,datetime.combine(v,datetime.min.time()),self.day)
                elif isinstance(v,(int,float)):
                    fmt=self.coord if h in ('위도','경도') else self.percent if '신뢰도' in h else self.decimal if any(k in h for k in ['시간','거리','반경']) else self.integer
                    sheet.write_number(index,j,v,fmt)
                else: sheet.write_string(index,j,str(v)[:32760])
            index+=1
        finish();return sheets

    def close(self): self.book.close()


def iter_query(db,sql):
    cursor=db.cursor();cursor.execute(sql)
    try:
        while True:
            rows=cursor.fetchmany(10000)
            if not rows: return
            yield from rows
    finally: cursor.close()


def make_report(db,path,c,max_rows=MAX_ROWS):
    excel=Excel(path,max_rows)
    db.execute('''CREATE OR REPLACE VIEW details AS SELECT v.*,p.name,p.major,p.minor,p.address,p.road_address,p.confidence,p.review,p.identification FROM visits v JOIN labels p USING(place_id)''')
    db.execute('''CREATE OR REPLACE VIEW ranking AS SELECT place_id,any_value(name) name,any_value(major) major,any_value(minor) minor,
      any_value(address) address,count(*) visits,count(DISTINCT vehicle) vehicles,avg(duration_s)/60 avg_minutes,median(duration_s)/60 median_minutes,
      any_value(lat) lat,any_value(lon) lon FROM details GROUP BY place_id''')
    try:
        excel.table(SHEETS[0],['순위','장소 ID','장소명','대분류','시설 유형','주소','방문 횟수','방문 차량 수','평균 체류시간(분)','중앙 체류시간(분)','위도','경도'],iter_query(db,'SELECT row_number() OVER(ORDER BY visits DESC,place_id),* FROM ranking ORDER BY visits DESC,place_id'),{'category':2,'series':[(6,'방문 횟수')],'title':'장소별 방문 횟수'})
        months=range(4,9)
        sums=','.join(f'count(*) FILTER (WHERE month(start)={m}) AS m{m}' for m in months)
        excel.table(SHEETS[1],['장소 ID','장소명','4월','5월','6월','7월','8월','합계'],iter_query(db,f'SELECT place_id,any_value(name),{sums},count(*) FROM details GROUP BY place_id ORDER BY count(*) DESC,place_id'),{'category':1,'series':[(i+2,f'{m}월') for i,m in enumerate(months)],'title':'월별 방문 비교'})
        # A complete grid includes zeros for all 21 vehicles, with count and duration columns.
        vehicles=c['vehicles'];columns=[];headers=['장소 ID','장소명']
        for v in vehicles:
            columns.extend([f'count(*) FILTER (WHERE vehicle={v})',f'coalesce(sum(duration_s) FILTER (WHERE vehicle={v}),0)/60'])
            headers.extend([f'{v}호차 방문 횟수',f'{v}호차 체류시간(분)'])
        excel.table(SHEETS[2],headers,iter_query(db,f'SELECT place_id,any_value(name),{",".join(columns)} FROM details GROUP BY place_id ORDER BY place_id'))
        excel.table(SHEETS[3],['방문 ID','차량번호','날짜','시작시각','종료시각','체류시간(분)','장소 ID','장소명','주소','위도','경도','라벨 신뢰도','GPS 기록 수','최대 기록 간격(초)','누적 이동거리(m)','최대 반경(m)','탐지 품질','장소 식별 상태'],iter_query(db,'''SELECT visit_id,vehicle,CAST(start AS DATE),start,"end",duration_s/60,place_id,name,address,lat,lon,confidence,point_count,max_gap_s,path_m,max_radius_m,quality,identification FROM details ORDER BY vehicle,start'''))
        excel.table(SHEETS[4],['장소 ID','장소명','대분류','세부 분류','지번 주소','도로명 주소','위도','경도','분류 근거','라벨 신뢰도','정보 출처','장소 식별 상태','GIS 조회 결과','건축물대장 조회 결과','카카오 조회 결과','정보원 비교','V-World 조회 결과'],iter_query(db,'SELECT place_id,name,major,minor,address,road_address,lat,lon,basis,confidence,sources,identification,gis_result,registry_result,kakao_result,comparison,vworld_result FROM labels ORDER BY place_id'))
        excel.table(SHEETS[5],['대분류','시설 유형','방문 횟수','방문 차량 수','4월','5월','6월','7월','8월'],iter_query(db,f'SELECT major,minor,count(*),count(DISTINCT vehicle),{sums} FROM details GROUP BY major,minor ORDER BY count(*) DESC,major,minor'))
        excel.table(SHEETS[6],['장소 ID','장소명','대분류','세부 분류','주소','위도','경도','검토 사유','라벨 신뢰도','정보 출처'],iter_query(db,"SELECT place_id,name,major,minor,address,lat,lon,review,confidence,sources FROM labels WHERE review<>'' OR minor='미확인' ORDER BY place_id"))
    finally: excel.close()


def candidate_report(db,path,max_rows=MAX_ROWS):
    excel=Excel(path,max_rows)
    headers=['방문 ID','차량번호','시작시각','종료시각','체류시간(분)','위도','경도','GPS 기록 수','최대 기록 간격(초)','누적 이동거리(m)','최대 반경(m)','종료 사유','품질','핵심 통계 포함']
    try:
        for eligible,name in [(True,'3분 이상 체류 후보'),(False,'핵심통계 제외 검증 구간')]:
            excel.table(name,headers,iter_query(db,f'SELECT visit_id,vehicle,start,"end",duration_s/60,lat,lon,point_count,max_gap_s,path_m,max_radius_m,reason,quality,eligible FROM segments WHERE eligible={str(eligible).lower()} ORDER BY vehicle,start'))
    finally: excel.close()
