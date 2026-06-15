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

# ── 주간/WoW 헬퍼 (휴일은 데이터 유무로 자동 판별) ──────────────────
def week_bounds(d):
    """d가 속한 주의 (월요일, 금요일)."""
    mon = d - timedelta(days=d.weekday())
    return mon, mon + timedelta(days=4)

def snapshot_has_data(snap):
    """스냅샷에 실제 보유종목 데이터가 하나라도 있으면 True (빈/휴일 스냅샷 = False)."""
    return bool(snap) and any(v for v in snap.values())

def last_data_date_in_range(start, end):
    """[start, end] 구간에서 '데이터가 있는' 가장 마지막 날짜. 휴일/주말은 자동 스킵. 없으면 None."""
    d = end
    while d >= start:
        if snapshot_has_data(load_snapshot(d)):
            return d
        d -= timedelta(days=1)
    return None

def last_data_date_before(d, max_back=14):
    """d 직전(미포함)으로 데이터가 있는 가장 가까운 날짜. 휴일/주말 자동 스킵."""
    cur = d - timedelta(days=1)
    limit = d - timedelta(days=max_back)
    while cur >= limit:
        if snapshot_has_data(load_snapshot(cur)):
            return cur
        cur -= timedelta(days=1)
    return None

def get_wow_pair(report_date):
    """(직전주 마지막 거래일, 이번주 마지막 거래일) — 둘 다 '데이터 있는' 날 기준.
    이번주 금요일이 휴일이면 이번주 목요일, 지난주 금요일이 휴일이면 지난주 목요일이 자동 선택됨."""
    this_mon, this_fri = week_bounds(report_date)
    this_ref = last_data_date_in_range(this_mon, min(this_fri, report_date))
    prev_mon = this_mon - timedelta(days=7)
    prev_fri = prev_mon + timedelta(days=4)
    prev_ref = last_data_date_in_range(prev_mon, prev_fri)
    return prev_ref, this_ref

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
    # 2026-06-15: past_pdf_json(상위10 요약) → m11_view.php(전체 구성종목+수량) 파싱
    idx = etf['params']['idx']
    pdf_date = date.strftime('%Y-%m-%d')
    url = f'https://timeetf.co.kr/m11_view.php?idx={idx}&cate=&pdfDate={pdf_date}'
    try:
        r = requests.get(url, headers={**HEADERS, 'Referer': 'https://timeetf.co.kr/'}, timeout=15)
        if r.status_code != 200:
            print(f'  TIME idx={idx} HTTP {r.status_code}')
            return []
        body = r.text
        ti = body.find('moreList1')
        if ti >= 0:
            tend = body.find('</table>', ti)
            body = body[ti:tend] if tend > 0 else body[ti:]
        pat = r'<tr>\s*<td>([^<]*)</td>\s*<td>([^<]+)</td>\s*<td>([\d,]+)</td>\s*<td>[\d,]+</td>\s*<td>([\d.]+)</td>\s*</tr>'
        result = []
        for code, name, qty, pct in re.findall(pat, body):
            name = name.strip()
            if not name or '현금' in name:
                continue
            try:
                result.append({'name': name, 'qty': int(qty.replace(',', '')), 'weight': float(pct)})
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
# 2026-06-14: prdct-item-list.ajax (JSON) 직접 호출 방식으로 변경
#   pdf.ajax는 빈 테이블 틀(헤더+빈 tbody)만 반환 → 실제 구성종목은
#   prdct-item-list.ajax 가 JSON(rtnData[])으로 제공. listCnt 크게 주면 전체 수신.
#   GitHub Actions IP 차단은 Playwright 브라우저 컨텍스트로 우회.
async def fetch_tiger_async(etf, date):
    ksd_fund = etf['params']['ksdFund']
    date_fmt = date.strftime('%Y%m%d')
    base_url = f'https://investments.miraeasset.com/tigeretf/ko/product/search/detail/index.do?ksdFund={ksd_fund}'
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            page = await context.new_page()
            await page.goto(base_url, wait_until='domcontentloaded', timeout=20000)
            # 세션 쿠키 포함 상태에서 구성종목 JSON 요청
            data = await page.evaluate("""
                async (args) => {
                    const url = `/tigeretf/ko/product/chart/prdct-item-list.ajax`
                        + `?ksdFund=${args.ksd}&prfPrd=Week01&fixDate=${args.date}&listCnt=500`;
                    const r = await fetch(url, {method:'POST', headers:{'X-Requested-With':'XMLHttpRequest'}});
                    const txt = await r.text();
                    try { return JSON.parse(txt); } catch(e) { return null; }
                }
            """, {'ksd': ksd_fund, 'date': date_fmt})
            await browser.close()
            if not data or 'rtnData' not in data:
                print(f'  TIGER {ksd_fund} 응답 없음/형식오류')
                return []
            result = []
            for item in data['rtnData']:
                name = (item.get('memItemname') or '').strip()
                qty = item.get('stockQty') or 0
                weight = item.get('stockRate') or 0.0
                if name and '현금' not in name and qty and qty > 0:
                    result.append({'name': name, 'qty': int(qty), 'weight': float(weight)})
            print(f'  TIGER {ksd_fund} → {len(result)}개')
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

# ════════════════════════════════════════════════════════════════════
# 반도체 탭 신규 3종 — 맥미니(KB/우리 차단 없음)에서 동작 검증 필요
# 아래 셀렉터/컬럼인덱스는 작업노트 기준 추정값 → 맥미니에서 실DOM 확인 후 보정
# ════════════════════════════════════════════════════════════════════

# 공통: Playwright 테이블 행 → holdings 파서
async def _parse_rows(rows, name_col, qty_col, weight_col):
    result = []
    for row in rows:
        cells = await row.query_selector_all('td')
        if len(cells) <= max(name_col, qty_col, weight_col):
            continue
        name = (await cells[name_col].inner_text()).strip()
        if not name or '현금' in name or '소계' in name or '합계' in name:
            continue
        try:
            qty_t = (await cells[qty_col].inner_text()).strip().replace(',', '').replace('주', '')
            pct_t = (await cells[weight_col].inner_text()).strip().replace(',', '').replace('%', '')
            qty = int(float(qty_t)) if qty_t and qty_t.replace('.', '').isdigit() else 0
            pct = float(pct_t) if pct_t else 0.0
            if qty > 0:
                result.append({'name': name, 'qty': qty, 'weight': pct})
        except Exception:
            pass
    return result

# ── WON (우리자산운용, wooriam.kr) ───────────────────────────────────
# 과거조회 가능 ✅ / 단 페이지엔 상위10만 표시 (전체는 엑셀다운로드)
# #pdfSearchDate 에 YYYY.MM.DD 넣고 setPDFList() 호출 → 테이블 갱신
# 테이블 컬럼: 종목코드 / 종목명 / 수량(주) / 평가금액 / 비중
async def fetch_won_async(etf, date):
    slug = etf['params']['view_slug']
    d = date.strftime('%Y.%m.%d')
    url = f'https://www.wooriam.kr/investment/etf-view/{slug}'   # TODO: www 유무 실서버 확인
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await (await browser.new_context(user_agent=HEADERS['User-Agent'])).new_page()
            await page.goto(url, wait_until='networkidle', timeout=25000)
            # 과거날짜 조회: 날짜 input 세팅 후 갱신 함수 호출
            await page.evaluate("""
                (d) => {
                    const el = document.querySelector('#pdfSearchDate');
                    if (el) { el.value = d; }
                    if (typeof setPDFList === 'function') { setPDFList(); }
                }
            """, d)
            await page.wait_for_timeout(2500)
            # TODO(맥미니): 구성종목 테이블 셀렉터 확인. 우선 페이지 내 모든 table tbody tr 스캔
            rows = await page.query_selector_all('table tbody tr')
            result = await _parse_rows(rows, name_col=1, qty_col=2, weight_col=4)
            await browser.close()
            print(f'  WON {slug} → {len(result)}개 (상위10 한정 가능)')
            return result
    except Exception as e:
        print(f'  WON {slug} 오류: {e}')
        return []

def fetch_won(etf, date):
    return asyncio.run(fetch_won_async(etf, date))

# ── RISE (KB자산운용, riseetf.co.kr) ─────────────────────────────────
# 현재보유만 확보 (과거조회 ajax는 CSRF/토큰 요구 → 일별 적재로 WoW 누적)
# 테이블 컬럼: 종목코드 / 종목명 / 수량㈜ / 보유비중 / 평가금액
async def fetch_rise_async(etf, date):
    finder_id = etf['params']['finder_id']
    url = f'https://www.riseetf.co.kr/prod/finderDetail/{finder_id}'
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await (await browser.new_context(user_agent=HEADERS['User-Agent'])).new_page()
            await page.goto(url, wait_until='networkidle', timeout=25000)
            await page.wait_for_timeout(2000)
            # TODO(맥미니): 구성종목 탭이 클릭 필요할 수 있음(PDF/구성종목 탭). 실DOM 확인.
            # 컬럼 순서가 비중<->평가금액 다를 수 있으니 헤더 보고 보정.
            rows = await page.query_selector_all('table tbody tr')
            result = await _parse_rows(rows, name_col=1, qty_col=2, weight_col=3)
            await browser.close()
            print(f'  RISE {finder_id} → {len(result)}개 (현재보유)')
            return result
    except Exception as e:
        print(f'  RISE {finder_id} 오류: {e}')
        return []

def fetch_rise(etf, date):
    return asyncio.run(fetch_rise_async(etf, date))

# ── UNICORN (현대자산운용, hyundaiam.com) ────────────────────────────
# 현재보유만 (datePdf 숨김 + 검색버튼 없음 → 과거조회 어려움)
async def fetch_unicorn_async(etf, date):
    view_id = etf['params']['view_id']
    url = f'https://www.hyundaiam.com/kor/HD-KP-FG/HD-KP-FG-07-D.html?id={view_id}'
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await (await browser.new_context(user_agent=HEADERS['User-Agent'])).new_page()
            await page.goto(url, wait_until='networkidle', timeout=25000)
            await page.wait_for_timeout(2000)
            # TODO(맥미니): 구성종목 테이블 셀렉터 확인. 컬럼 순서 헤더 보고 보정.
            rows = await page.query_selector_all('table tbody tr')
            result = await _parse_rows(rows, name_col=1, qty_col=2, weight_col=3)
            await browser.close()
            print(f'  UNICORN {view_id} → {len(result)}개 (현재보유)')
            return result
    except Exception as e:
        print(f'  UNICORN {view_id} 오류: {e}')
        return []

def fetch_unicorn(etf, date):
    return asyncio.run(fetch_unicorn_async(etf, date))

FETCHERS = {
    'samsung': fetch_samsung,
    'kodex':   fetch_kodex,
    'time':    fetch_time,
    'plus':    fetch_plus,
    'tiger':   fetch_tiger,
    'vita':    fetch_vita,
    'won':     fetch_won,
    'rise':    fetch_rise,
    'unicorn': fetch_unicorn,
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
    if DRY_RUN or TARGET_DATE:  # 백필 시 텔레 미발송
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
    prev  = last_data_date_before(today) or prev_biz(today)
    print(f'=== 수집일: {date_str(today)} (직전 거래일: {date_str(prev)}) ===')
    prev_snaps = load_snapshot(prev)
    today_snaps = {}
    for etf in ALL_ETFS:
        name = etf['name']
        print(f'[수집] {name}')
        holdings = fetch_holdings(etf, today)
        today_snaps[name] = holdings
        print(f'  → {len(holdings)}개 종목')
    if DRY_RUN:
        print('[DRY_RUN] 스냅샷 저장 생략')
    else:
        n_with_data = sum(1 for v in today_snaps.values() if v)
        if n_with_data == 0:
            # 전 종목 0개 = 휴일/비거래일 추정. 이미 데이터 있는 스냅샷이면 덮어쓰지 않음.
            if snapshot_has_data(load_snapshot(today)):
                print('  ⚠️ 수집결과 전부 비어있음(휴일 추정) — 기존 스냅샷 유지(미덮어씀)')
            else:
                save_snapshot(today, today_snaps)
                print('  ⚠️ 비거래일 추정(데이터 0개) — 빈 스냅샷 기록')
        else:
            save_snapshot(today, today_snaps)
            print(f'  💾 일간 누적 저장: {n_with_data}/{len(today_snaps)}종목 데이터 확보')
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

