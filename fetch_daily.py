#!/usr/bin/env python3
# fetch_daily.py - 운용사별 ETF holdings 수집 + 일별 스냅샷 저장
# 수정 이력:
#   2026-06-14: TIME 도메인 변경 (timefolio.co.kr → timeetf.co.kr)
#               TIGER 403 fix: Session으로 쿠키 발급 후 요청
#               PLUS 도메인 변경 (hanwhafund.co.kr → plusetf.co.kr, API 미확인)

import os, json, re, requests, asyncio
from datetime import datetime, timedelta
from pathlib import Path
from config.etfs import SHEETS, ALL_ETFS, TELEGRAM_CHAT_ID

TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN', '')
TARGET_DATE  = os.environ.get('TARGET_DATE', '')
DRY_RUN      = os.environ.get('DRY_RUN', 'false').lower() == 'true'
DATA_DIR     = Path('data/snapshots')
DATA_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': '*/*',
}

def get_target_date():
    if TARGET_DATE:
        return datetime.strptime(TARGET_DATE, '%Y-%m-%d').date()
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo('Asia/Seoul'))
    d = now.date()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d

def date_str(d):
    return d.strftime('%Y-%m-%d')

def prev_biz(d):
    d2 = d - timedelta(days=1)
    while d2.weekday() >= 5:
        d2 -= timedelta(days=1)
    return d2

# ── 삼성액티브 (KoAct) ─────────────────────────────────────────
def fetch_samsung(etf, date):
    fund_id = etf['params']['fund_id']
    gijun = date.strftime('%Y.%m.%d')
    url = f'https://www.samsungactive.co.kr/api/v1/product/etf-pdf/{fund_id}.do?gijunYMD={gijun}'
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            print(f'  Samsung {fund_id} HTTP {r.status_code}')
            return []
        items = r.json().get('pdf', {}).get('list', [])
        result = []
        for item in items:
            name = str(item.get('secNm') or item.get('secNa') or '').strip()
            qty  = item.get('applyQ') or item.get('holdQ') or 0
            pct  = item.get('ratio') or 0
            if not name or name in ('현금예금', '현금', 'CASH'):
                continue
            try:
                q = int(str(qty).replace(',', ''))
                p = float(str(pct).replace(',', '')) if pct else 0.0
                if q > 0:
                    result.append({'name': name, 'qty': q, 'weight': p})
            except:
                pass
        return result
    except Exception as e:
        print(f'  Samsung {fund_id} 오류: {e}')
        return []

# ── KODEX (삼성자산운용) ────────────────────────────────────────
def fetch_kodex(etf, date):
    fund_id = etf['params']['fund_id']
    gijun = date.strftime('%Y.%m.%d')
    url = f'https://www.samsungfund.com/api/v1/product/etf-pdf/{fund_id}.do?gijunYMD={gijun}'
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            print(f'  KODEX {fund_id} HTTP {r.status_code}')
            return []
        items = r.json().get('pdf', {}).get('list', [])
        result = []
        for item in items:
            name = str(item.get('secNm') or item.get('secNa') or '').strip()
            qty  = item.get('applyQ') or item.get('holdQ') or 0
            pct  = item.get('ratio') or 0
            if not name or name in ('현금예금', '현금', 'CASH'):
                continue
            try:
                q = int(str(qty).replace(',', ''))
                p = float(str(pct).replace(',', '')) if pct else 0.0
                if q > 0:
                    result.append({'name': name, 'qty': q, 'weight': p})
            except:
                pass
        return result
    except Exception as e:
        print(f'  KODEX {fund_id} 오류: {e}')
        return []

# ── 타임폴리오 (TIME) ───────────────────────────────────────────
# 2026-06-14: timeetf.co.kr 로 도메인 변경, past_pdf_json.php 엔드포인트
# 응답: {"today": [{"prodNm": "종목명", "wei": "비중", "increaseWei": "신규"}, ...]}
def fetch_time(etf, date):
    idx = etf['params']['idx']
    date_fmt = date.strftime('%Y%m%d')
    url = f'https://timeetf.co.kr/past_pdf_json.php?idx={idx}&period={date_fmt}'
    try:
        r = requests.get(url, headers={**HEADERS, 'Referer': 'https://timeetf.co.kr/'}, timeout=15)
        if r.status_code != 200:
            print(f'  TIME idx={idx} HTTP {r.status_code}')
            return []
        data = r.json()
        items = data.get('today') or []
        result = []
        for item in items:
            name = str(item.get('prodNm') or '').strip()
            pct  = item.get('wei') or 0
            if not name or '현금' in name:
                continue
            try:
                p = float(str(pct).replace(',', '')) if pct else 0.0
                result.append({'name': name, 'qty': 0, 'weight': p})
            except:
                pass
        return result
    except Exception as e:
        print(f'  TIME idx={idx} 오류: {e}')
        return []

# ── 한화 PLUS ───────────────────────────────────────────────────
# 2026-06-14: plusetf.co.kr 신규 API 확인 완료
# GET /api/v1/product/pdf/list?n={fund_code}&d={YYYYMMDD}&page=0&pageSize=100
# 응답: {"content": [{"jmNm": "종목명", "amount": 수량, "ratio": 비중}, ...]}
def fetch_plus(etf, date):
    fund_code = etf['params']['fund_code']
    date_fmt = date.strftime('%Y%m%d')
    url = f'https://www.plusetf.co.kr/api/v1/product/pdf/list'
    try:
        r = requests.get(url, headers={
            **HEADERS,
            'Referer': f'https://www.plusetf.co.kr/product/detail?n={fund_code}',
        }, params={'n': fund_code, 'd': date_fmt, 'page': 0, 'pageSize': 200}, timeout=15)
        if r.status_code != 200:
            print(f'  PLUS {fund_code} HTTP {r.status_code}')
            return []
        items = r.json().get('content', [])
        result = []
        for item in items:
            name = str(item.get('jmNm') or '').strip()
            qty  = item.get('amount') or 0
            pct  = item.get('ratio') or 0
            if not name or '현금' in name:
                continue
            try:
                q = int(qty)
                p = float(pct)
                if q > 0:
                    result.append({'name': name, 'qty': q, 'weight': p})
            except:
                pass
        return result
    except Exception as e:
        print(f'  PLUS {fund_code} 오류: {e}')
        return []

# ── 미래에셋 TIGER ──────────────────────────────────────────────────
# 2026-06-14: requests 403 → Playwright로 변경 (GitHub Actions IP 차단 우회)
async def fetch_tiger_async(etf, date):
    ksd_fund = etf['params']['ksdFund']
    date_fmt = date.strftime('%Y%m%d')
    base_url = f'https://investments.miraeasset.com/tigeretf/ko/product/search/detail/index.do?ksdFund={ksd_fund}'
    ajax_url = 'https://investments.miraeasset.com/tigeretf/ko/product/search/detail/pdf.ajax'
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            page = await context.new_page()
            # 쿠키 발급
            await page.goto(base_url, wait_until='domcontentloaded', timeout=20000)
            # pdf.ajax POST
            response = await page.evaluate(f"""
                async () => {{
                    const r = await fetch('{ajax_url}', {{
                        method: 'POST',
                        headers: {{
                            'Content-Type': 'application/x-www-form-urlencoded',
                            'Referer': '{base_url}'
                        }},
                        body: 'ksdFund={ksd_fund}&trdDd={date_fmt}'
                    }});
                    return await r.text();
                }}
            """)
            await browser.close()
            # HTML 테이블 파싱
            from html.parser import HTMLParser
            class TableParser(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.rows, self.cur, self.in_td = [], [], False
                def handle_starttag(self, tag, attrs):
                    if tag in ('td', 'th'): self.in_td = True
                def handle_endtag(self, tag):
                    if tag in ('td', 'th'): self.in_td = False
                    elif tag == 'tr':
                        if self.cur: self.rows.append(self.cur[:])
                        self.cur = []
                def handle_data(self, data):
                    if self.in_td: self.cur.append(data.strip())
            p = TableParser()
            p.feed(response)
            result = []
            for row in p.rows[1:]:
                if len(row) < 2: continue
                name = row[0].strip()
                if not name or '현금' in name: continue
                try:
                    qty = int(str(row[1]).replace(',', '')) if len(row) > 1 else 0
                    pct = float(str(row[-1]).replace(',', '').replace('%', '')) if len(row) > 2 else 0.0
                    if qty > 0:
                        result.append({'name': name, 'qty': qty, 'weight': pct})
                except:
                    pass
            return result
    except Exception as e:
        print(f'  TIGER {ksd_fund} 오류: {e}')
        return []

def fetch_tiger(etf, date):
    return asyncio.run(fetch_tiger_async(etf, date))

# ── VITA ────────────────────────────────────────────────────────
async def fetch_vita_async(etf, date):
    fund_cd = etf['params']['fundCD']
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            url = f'https://www.vitaetf.co.kr/etf/view?fundCd={fund_cd}'
            await page.goto(url, wait_until='networkidle', timeout=20000)
            rows = await page.query_selector_all('table tbody tr')
            result = []
            for row in rows:
                cells = await row.query_selector_all('td')
                if len(cells) < 2: continue
                name = (await cells[0].inner_text()).strip()
                if not name or '현금' in name: continue
                try:
                    qty_text = (await cells[1].inner_text()).strip().replace(',', '')
                    qty = int(qty_text) if qty_text.isdigit() else 0
                    pct_text = (await cells[-1].inner_text()).strip().replace(',', '').replace('%', '')
                    pct = float(pct_text) if pct_text else 0.0
                    if qty > 0:
                        result.append({'name': name, 'qty': qty, 'weight': pct})
                except:
                    pass
            await browser.close()
            return result
    except Exception as e:
        print(f'  VITA {fund_cd} 오류: {e}')
        return []

def fetch_vita(etf, date):
    return asyncio.run(fetch_vita_async(etf, date))

FETCHERS = {
    'samsung': fetch_samsung,
    'kodex':   fetch_kodex,
    'time':    fetch_time,
    'plus':    fetch_plus,
    'tiger':   fetch_tiger,
    'vita':    fetch_vita,
}

def fetch_holdings(etf, date):
    source = etf['source']
    fetcher = FETCHERS.get(source)
    if not fetcher:
        print(f'  알 수 없는 source: {source}')
        return []
    return fetcher(etf, date)

def save_snapshot(date, snapshots):
    path = DATA_DIR / f'{date_str(date)}.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(snapshots, f, ensure_ascii=False, indent=2)
    print(f'  스냅샷 저장: {path}')

def load_snapshot(date):
    path = DATA_DIR / f'{date_str(date)}.json'
    if not path.exists():
        return {}
    with open(path, encoding='utf-8') as f:
        return json.load(f)

def send_telegram(text):
    if DRY_RUN:
        print(f'[DRY_RUN] TG: {text[:100]}')
        return True
    try:
        r = requests.post(
            f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage',
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': text, 'parse_mode': 'HTML'},
            timeout=15
        )
        return r.json().get('ok', False)
    except Exception as e:
        print(f'텔레그램 오류: {e}')
        return False

def detect_changes(today_snap, prev_snap):
    today_map = {h['name']: h for h in today_snap}
    prev_map  = {h['name']: h for h in prev_snap}
    new_in  = [n for n in today_map if n not in prev_map]
    removed = [n for n in prev_map  if n not in today_map]
    increased = [
        n for n in today_map
        if n in prev_map and today_map[n]['weight'] > prev_map[n]['weight'] + 0.3
    ]
    return new_in, removed, increased

def main():
    today = get_target_date()
    prev  = prev_biz(today)
    print(f'=== 수집일: {date_str(today)} (전일: {date_str(prev)}) ===')
    prev_snaps = load_snapshot(prev)
    today_snaps = {}
    for etf in ALL_ETFS:
        name = etf['name']
        print(f'[수집] {name}')
        holdings = fetch_holdings(etf, today)
        today_snaps[name] = holdings
        print(f'  → {len(holdings)}개 종목')
    if not DRY_RUN:
        save_snapshot(today, today_snaps)
    else:
        print('[DRY_RUN] 스냅샷 저장 생략')
    lines = ['📊 <b>ETF 주간 트래커 일일 리포트</b>']
    lines.append(f'📅 {date_str(today)} 기준\n')
    has_signal = False
    for sheet in SHEETS:
        sheet_name = sheet['name']
        section_lines = [f'\n🔷 <b>[{sheet_name}]</b>']
        sheet_has_signal = False
        for etf in sheet['etfs']:
            etf_name = etf['name']
            today_h  = today_snaps.get(etf_name, [])
            prev_h   = prev_snaps.get(etf_name, [])
            if not today_h:
                section_lines.append(f'  ⚠️ {etf_name}: 데이터 없음')
                continue
            new_in, removed, increased = detect_changes(today_h, prev_h)
            if new_in or removed or increased:
                sheet_has_signal = True
                has_signal = True
                section_lines.append(f'\n  <b>{etf_name}</b>')
                if new_in:
                    section_lines.append(f'  🟢 신규: {", ".join(new_in[:5])}')
                if increased:
                    section_lines.append(f'  🔼 증가: {", ".join(increased[:5])}')
                if removed:
                    section_lines.append(f'  🔴 제외: {", ".join(removed[:3])}')
        if sheet_has_signal:
            lines.extend(section_lines)
    if not has_signal:
        lines.append('변동 없음 (전일 대비 신규/변화 없음)')
    msg = '\n'.join(lines)
    print(msg)
    send_telegram(msg)

if __name__ == '__main__':
    main()
