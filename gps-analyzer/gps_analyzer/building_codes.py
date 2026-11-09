"""Verified building-use code subset from Korean architectural-registry references.

Unknown codes remain unclassified; prefixes are NEVER guessed as subtypes.
Legal use != proof that a GPS stop entered or visited a particular facility.

Sources:
- https://www.hub.go.kr/portal/opn/tyb/idx-bdrg-ttlldr.do (official codebook link)
- https://www.codil.or.kr/filebank/moct2014/200301/MOCT605_1.PDF?nserialno=605
- https://www.xn--s39a8hknmph62hpdbq8y.kr/publicationFileDirect.es?bid=research&list_no=2211&seq=1
"""
from __future__ import annotations
import re

# code: (legal major use, analytical facility major, exact code description)
EXACT_USE_CODES = {
    '01000': ('단독주택', '주거시설', '단독주택'),
    '01001': ('단독주택', '주거시설', '단독주택'),
    '01002': ('단독주택', '주거시설', '다중주택'),
    '01003': ('단독주택', '주거시설', '다가구주택'),
    '02000': ('공동주택', '주거시설', '공동주택'),
    '02001': ('공동주택', '주거시설', '아파트'),
    '02002': ('공동주택', '주거시설', '연립주택'),
    '02003': ('공동주택', '주거시설', '다세대주택'),
    '02005': ('공동주택', '주거시설', '부대시설'),
    '03000': ('제1종근린생활시설', '근린생활시설', '제1종근린생활시설'),
    '03001': ('제1종근린생활시설', '상업시설', '소매점'),
    '03002': ('제1종근린생활시설', '상업시설', '휴게음식점'),
    '03005': ('제1종근린생활시설', '의료시설', '의원'),
    '04000': ('제2종근린생활시설', '근린생활시설', '제2종근린생활시설'),
    '04001': ('제2종근린생활시설', '상업시설', '일반음식점'),
    '04010': ('제2종근린생활시설', '교육시설', '학원'),
    '05000': ('문화및집회시설', '문화시설', '문화및집회시설'),
    '06000': ('종교시설', '종교시설', '종교시설'),
    '07000': ('판매시설', '상업시설', '판매시설'),
    '08000': ('운수시설', '교통시설', '운수시설'),
    '09000': ('의료시설', '의료시설', '의료시설'),
    '10000': ('교육연구시설', '교육시설', '교육연구시설'),
    '11000': ('노유자시설', '복지시설', '노유자시설'),
    '12000': ('수련시설', '수련시설', '수련시설'),
    '13000': ('운동시설', '운동시설', '운동시설'),
    '14000': ('업무시설', '업무시설', '업무시설'),
    '15000': ('숙박시설', '숙박시설', '숙박시설'),
    '16000': ('위락시설', '위락시설', '위락시설'),
    '17000': ('공장', '산업시설', '공장'),
    '18000': ('창고시설', '산업시설', '창고시설'),
    '19000': ('위험물저장및처리시설', '기타시설', '위험물저장및처리시설'),
    '20000': ('자동차관련시설', '교통시설', '자동차관련시설'),
    '21000': ('동물및식물관련시설', '기타시설', '동물및식물관련시설'),
}

def normalize_code(value):
    """Preserve five-digit official codes including leading zeros."""
    if value is None or isinstance(value, bool):
        return ''
    s = str(value).strip()
    if re.fullmatch(r'\d{1,5}', s):
        return s.zfill(5)
    return s

def decode_use_code(value):
    """Return (legal_major, analytical_major, minor) or None.

    Codes are not inferred from name, nearby POIs or matching prefixes.
    """
    return EXACT_USE_CODES.get(normalize_code(value))
