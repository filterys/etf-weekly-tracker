#!/usr/bin/env python3
# fetch_daily.py - 운용사별 ETF holdings 수집 + 일별 스냅샷 저장
# 수정 이력:
#   2026-06-14: TIME 도메인 변경 (timefolio.co.kr → timeetf.co.kr)
#               TIGER 403 fix: Session으로 쿠키 발급 후 요청
#               PLUS 도메인 변경 (hanwhafund.co.kr → plusetf.co.kr, API 미확인)

import os, json, re, requests, asyncio
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter
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

def recent_bizdays(d, n=8):
    """d부터 직전 영업일들로 n개 날짜 리스트. 공시지연/휴일 fallback용."""
    days = [d]
    cur = d
    for _ in range(n - 1):
        cur = prev_biz(cur)
        days.append(cur)
    return days

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
def _time_once(idx, d):
    pdf_date = d.strftime('%Y-%m-%d')
    url = f'https://timeetf.co.kr/m11_view.php?idx={idx}&cate=&pdfDate={pdf_date}'
    r = requests.get(url, headers={**HEADERS, 'Referer': 'https://timeetf.co.kr/'}, timeout=15)
    if r.status_code != 200:
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

def fetch_time(etf, date):
    # 2026-06-15: m11_view.php(전체 구성종목+수량) 파싱
    # 2026-06-16: T-1 공시지연 대응 — 데이터 있는 날까지 직전 영업일로 fallback
    idx = etf['params']['idx']
    for d in recent_bizdays(date, 8):
        try:
            res = _time_once(idx, d)
        except Exception as e:
            print(f'  TIME idx={idx} {d} 오류: {e}')
            res = []
        if res:
            return res
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
    # 2026-06-16: prdct-item-list.ajax 정상(memItemname/stockQty/stockRate).
    # TIGER는 PDF 공시가 ~2영업일 지연 → 데이터 있는 날까지 직전 영업일로 fallback
    ksd_fund = etf['params']['ksdFund']
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
            for d in recent_bizdays(date, 8):
                df = d.strftime('%Y%m%d')
                data = await page.evaluate("""
                    async (args) => {
                        const url = `/tigeretf/ko/product/chart/prdct-item-list.ajax`
                            + `?ksdFund=${args.ksd}&prfPrd=Week01&fixDate=${args.date}&listCnt=500`;
                        const r = await fetch(url, {method:'POST', headers:{'X-Requested-With':'XMLHttpRequest'}});
                        try { return await r.json(); } catch(e) { return null; }
                    }
                """, {'ksd': ksd_fund, 'date': df})
                arr = (data or {}).get('rtnData') or []
                if arr:
                    result = []
                    for item in arr:
                        name = (item.get('memItemname') or '').strip()
                        qty = item.get('stockQty') or 0
                        weight = item.get('stockRate') or 0.0
                        if name and '현금' not in name and qty and qty > 0:
                            result.append({'name': name, 'qty': int(qty), 'weight': float(weight)})
                    await browser.close()
                    print(f'  TIGER {ksd_fund} → {len(result)}개 ({df})')
                    return result
            await browser.close()
            print(f'  TIGER {ksd_fund} → 0개 (최근 영업일 데이터 없음)')
            return []
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

# 헤더에 keywords가 모두 들어간 테이블의 tbody tr 반환 (구성종목 표 식별용)
async def _holdings_rows(page, keywords=('종목명', '수량', '비중')):
    tables = await page.query_selector_all('table')
    for t in tables:
        ths = await t.query_selector_all('th')
        htext = ' '.join([(await th.inner_text()) for th in ths])
        if all(k in htext for k in keywords):
            return await t.query_selector_all('tbody tr')
    return []

# ── WON (우리자산운용, wooriam.kr) ───────────────────────────────────
# 실측(2026-06-16): 헤더 [번호, 종목코드, 종목명, 수량(주)/액면(원), 평가금액(원), 비중(%)]
#   → td 컬럼: 종목명=2, 수량=3, 비중=5  / 상위 10종목만 페이지 노출
# 과거조회 ✅: #pdfSearchDate(YYYY.MM.DD) 세팅 후 setPDFList() 호출 → 표 갱신 확인됨
async def fetch_won_async(etf, date):
    slug = etf['params']['view_slug']
    url = f'https://www.wooriam.kr/investment/etf-view/{slug}'
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await (await browser.new_context(user_agent=HEADERS['User-Agent'])).new_page()
            await page.goto(url, wait_until='networkidle', timeout=25000)
            for cand in recent_bizdays(date, 7):
                d = cand.strftime('%Y.%m.%d')
                await page.evaluate("""
                    (d) => {
                        const el = document.querySelector('#pdfSearchDate');
                        if (el) { el.value = d; }
                        if (typeof setPDFList === 'function') { setPDFList(); }
                    }
                """, d)
                await page.wait_for_timeout(2500)
                rows = await _holdings_rows(page, ('종목명', '수량', '비중'))
                result = await _parse_rows(rows, name_col=2, qty_col=3, weight_col=5)
                if result:
                    await browser.close()
                    print(f'  WON {slug} → {len(result)}개 (상위10, {d})')
                    return result
            await browser.close()
            print(f'  WON {slug} → 0개')
            return []
    except Exception as e:
        print(f'  WON {slug} 오류: {e}')
        return []

def fetch_won(etf, date):
    return asyncio.run(fetch_won_async(etf, date))

# ── RISE (KB자산운용, riseetf.co.kr) ─────────────────────────────────
# 실측(2026-06-16): 구성종목(PDF) 탭 = table.tr_border.align_center_m
#   헤더 [번호, 종목명, 종목코드, 수량㈜, 보유비중(%), 평가금액(원)] (번호는 th)
#   → tbody td 컬럼: 종목명=0, 수량=2, 비중=3
#   현재보유 수집(일별 적재로 WoW 누적). 과거조회는 #datepicker_pdf 있으나 미사용.
#   URL에 ?searchFlag=viewtab3 붙이면 구성종목 탭 바로 로드.
async def fetch_rise_async(etf, date):
    finder_id = etf['params']['finder_id']
    url = f'https://www.riseetf.co.kr/prod/finderDetail/{finder_id}?searchFlag=viewtab3'
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await (await browser.new_context(user_agent=HEADERS['User-Agent'])).new_page()
            await page.goto(url, wait_until='networkidle', timeout=25000)
            await page.wait_for_timeout(2500)
            rows = await page.query_selector_all('table.tr_border.align_center_m tbody tr')
            if not rows:
                rows = await _holdings_rows(page, ('종목명', '수량', '비중'))
            result = await _parse_rows(rows, name_col=0, qty_col=2, weight_col=3)
            await browser.close()
            print(f'  RISE {finder_id} → {len(result)}개 (현재보유)')
            return result
    except Exception as e:
        print(f'  RISE {finder_id} 오류: {e}')
        return []

def fetch_rise(etf, date):
    return asyncio.run(fetch_rise_async(etf, date))

# ── UNICORN (현대자산운용, hyundaiam.com) ────────────────────────────
# 실측(2026-06-16): 자산구성/공시 탭, 헤더 [No, 종목코드, 종목명, 수량(주), 평가금액(원), 비중]
#   → td 컬럼: 종목명=2, 수량=3, 비중=5 (비중값에 % 포함 → 파서가 제거)
# 과거조회 ✅: #datePdf(YYYY-MM-DD) 세팅 후 #btnQueryPdf 클릭 → 표 갱신 확인됨
async def fetch_unicorn_async(etf, date):
    view_id = etf['params']['view_id']
    url = f'https://www.hyundaiam.com/kor/HD-KP-FG/HD-KP-FG-07-D.html?id={view_id}'
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await (await browser.new_context(user_agent=HEADERS['User-Agent'])).new_page()
            await page.goto(url, wait_until='networkidle', timeout=25000)
            try:
                await page.click('text=자산구성/공시', timeout=4000)
            except Exception:
                pass
            for cand in recent_bizdays(date, 7):
                d = cand.strftime('%Y-%m-%d')
                await page.evaluate("""
                    (d) => {
                        const dp = document.querySelector('#datePdf');
                        if (dp) { dp.value = d; dp.dispatchEvent(new Event('change', {bubbles:true})); }
                        const b = document.querySelector('#btnQueryPdf');
                        if (b) { b.click(); }
                    }
                """, d)
                await page.wait_for_timeout(2500)
                rows = await _holdings_rows(page, ('종목명', '수량', '비중'))
                result = await _parse_rows(rows, name_col=2, qty_col=3, weight_col=5)
                if result:
                    await browser.close()
                    print(f'  UNICORN {view_id} → {len(result)}개 ({d})')
                    return result
            await browser.close()
            print(f'  UNICORN {view_id} → 0개')
            return []
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

def _tg_send_message(text):
    r = requests.post(
        f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage',
        json={'chat_id': TELEGRAM_CHAT_ID, 'text': text, 'parse_mode': 'HTML'},
        timeout=15
    )
    return r.json().get('ok', False)

def _chunk_text(text, limit=3800):
    """텔레그램 4096자 제한 대비 라인 단위 안전 분할."""
    chunks, cur = [], ''
    for line in text.split('\n'):
        if cur and len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = f'{cur}\n{line}' if cur else line
    if cur:
        chunks.append(cur)
    return chunks

def send_telegram(text):
    if DRY_RUN or TARGET_DATE:  # 백필 시 텔레 미발송
        print(f'[DRY_RUN] TG: {text[:100]}')
        return True
    try:
        ok = True
        for chunk in _chunk_text(text):
            if not _tg_send_message(chunk):
                ok = False
        return ok
    except Exception as e:
        print(f'텔레그램 오류: {e}')
        return False

def send_telegram_document(filepath, caption=''):
    if DRY_RUN or TARGET_DATE:  # 백필 시 텔레 미발송
        print(f'[DRY_RUN] TG DOC: {filepath}')
        return True
    try:
        with open(filepath, 'rb') as f:
            r = requests.post(
                f'https://api.telegram.org/bot{TG_BOT_TOKEN}/sendDocument',
                data={'chat_id': TELEGRAM_CHAT_ID, 'caption': caption},
                files={'document': f},
                timeout=60
            )
        return r.json().get('ok', False)
    except Exception as e:
        print(f'텔레그램 문서 오류: {e}')
        return False

# 현금성 항목(종목 아님) — 신규/제외/보유 집계에서 전역 제외
_CASH_EXACT = {'설정현금액', '원화예금', '원화현금', '외화예금', '외화현금', 'RP', 'MMF', '단기금융상품'}
def is_cash(name):
    n = (name or '').strip()
    return n in _CASH_EXACT or '예금' in n or '현금' in n
def drop_cash(holdings):
    return [h for h in holdings if not is_cash(h.get('name', ''))]

def detect_changes(today_snap, prev_snap):
    today_map = {h['name']: h for h in today_snap}
    prev_map  = {h['name']: h for h in prev_snap}
    new_in  = [n for n in today_map if n not in prev_map]
    removed = [n for n in prev_map  if n not in today_map]
    increased = [
        n for n in today_map
        if n in prev_map and today_map[n].get('qty', 0) > prev_map[n].get('qty', 0)
    ]
    return new_in, removed, increased

def build_weekly_summary(today):
    """금요일 주간(WoW) 요약: 이번주 vs 지난주 마지막 거래일 비교. 데이터 없으면 None."""
    prev_ref, this_ref = get_wow_pair(today)
    if not (prev_ref and this_ref):
        return None
    wk_today = load_snapshot(this_ref)
    wk_prev  = load_snapshot(prev_ref)
    new_c, inc_c, rem_c, hold_c = Counter(), Counter(), Counter(), Counter()
    tn = ti = tr = 0
    for sheet in SHEETS:
        for etf in sheet['etfs']:
            th = drop_cash(wk_today.get(etf['name'], []))
            ph = drop_cash(wk_prev.get(etf['name'], []))
            for h in th:
                hold_c[h['name']] += 1
            if not th:
                continue
            ni, rm, inc = detect_changes(th, ph)
            for n in ni: new_c[n] += 1
            for n in inc: inc_c[n] += 1
            for n in rm: rem_c[n] += 1
            tn += len(ni); ti += len(inc); tr += len(rm)
    lines = ['📅 <b>주간 요약</b> (WoW · 이번주 vs 지난주)',
             f'🗓 {date_str(prev_ref)} → {date_str(this_ref)}',
             f'  • 신규 {tn} · 증가 {ti} · 제외 {tr}']
    def _cons(c, label, emoji):
        items = [(n, x) for n, x in c.most_common() if x >= 2][:5]
        out = []
        if items:
            out.append(f'  {emoji} <b>{label}</b>')
            for n, x in items:
                out.append(f'    · {n} ({x}개 ETF)')
        return out
    lines += _cons(new_c, '공통 신규', '🤝')
    lines += _cons(inc_c, '공통 증가', '⏫')
    lines += _cons(rem_c, '공통 제외', '🔻')
    top = hold_c.most_common(3)
    if top:
        lines.append('  🏆 최다 보유: ' + ', '.join(f'{n}({x})' for n, x in top))
    return '\n'.join(lines)

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
    # === 시그널 집계 (한 패스: 시트 detail + 교차 ETF 통계) ===
    new_counter  = Counter()   # 종목 → 신규 편입한 ETF 수 (공통 신규=매수 컨센서스)
    inc_counter  = Counter()   # 종목 → 비중 확대한 ETF 수 (공통 증가)
    rem_counter  = Counter()   # 종목 → 제외한 ETF 수 (공통 제외=매도 컨센서스)
    hold_counter = Counter()   # 종목 → 보유 중인 ETF 수 (최다 보유)
    total_new = total_inc = total_rem = 0
    sheet_sections = []
    unchanged = []   # 데이터는 있으나 신규/증가/제외 변동이 없는 ETF
    for sheet in SHEETS:
        sheet_name = sheet['name']
        section_lines = [f'\n🔷 <b>[{sheet_name}]</b>']
        sheet_has_signal = False
        for etf in sheet['etfs']:
            etf_name = etf['name']
            today_h  = drop_cash(today_snaps.get(etf_name, []))
            prev_h   = drop_cash(prev_snaps.get(etf_name, []))
            for h in today_h:
                hold_counter[h['name']] += 1
            if not today_h:
                section_lines.append(f'  ⚠️ {etf_name}: 데이터 없음')
                continue
            today_w = {h['name']: h.get('weight', 0) for h in today_h}
            prev_w  = {h['name']: h.get('weight', 0) for h in prev_h}
            today_q = {h['name']: h.get('qty', 0) for h in today_h}
            prev_q  = {h['name']: h.get('qty', 0) for h in prev_h}
            new_in, removed, increased = detect_changes(today_h, prev_h)
            for n in new_in:    new_counter[n] += 1
            for n in increased: inc_counter[n] += 1
            for n in removed:   rem_counter[n] += 1
            total_new += len(new_in); total_inc += len(increased); total_rem += len(removed)
            if new_in or removed or increased:
                sheet_has_signal = True
                section_lines.append(f'\n  <b>{etf_name}</b>')
                if new_in:
                    s = sorted(new_in, key=lambda n: today_w.get(n, 0), reverse=True)
                    section_lines.append('  🟢 신규: ' + ', '.join(f'{n}({today_w.get(n,0):.1f}%, +{today_q.get(n,0):,}주)' for n in s[:5]))
                if increased:
                    s = sorted(increased, key=lambda n: today_w.get(n, 0), reverse=True)
                    section_lines.append('  🔼 증가: ' + ', '.join(f'{n}({today_w.get(n,0):.1f}%, {today_q.get(n,0)-prev_q.get(n,0):+,}주, {today_w.get(n,0)-prev_w.get(n,0):+.1f}%p)' for n in s[:5]))
                if removed:
                    s = sorted(removed, key=lambda n: prev_w.get(n, 0), reverse=True)
                    section_lines.append('  🔴 제외: ' + ', '.join(f'{n}({prev_w.get(n,0):.1f}%, -{prev_q.get(n,0):,}주)' for n in s[:3]))
            else:
                unchanged.append(etf_name)
        if sheet_has_signal:
            sheet_sections.append(section_lines)

    # === 리포트 작성: 요약 헤더 + 시트별 detail ===
    lines = ['📊 <b>ETF 주간 트래커 일일 리포트</b>', f'📅 {date_str(today)} 기준']
    lines.append('\n📈 <b>오늘의 요약</b>')
    lines.append(f'  • 신규 {total_new} · 증가 {total_inc} · 제외 {total_rem}')
    def _consensus(counter, k=5):
        return [(n, c) for n, c in counter.most_common() if c >= 2][:k]
    cons_new = _consensus(new_counter)
    if cons_new:
        lines.append('  🤝 <b>공통 신규</b> (2개+ ETF 동시 편입·매수)')
        for n, c in cons_new:
            lines.append(f'    · {n} ({c}개 ETF)')
    cons_inc = _consensus(inc_counter)
    if cons_inc:
        lines.append('  ⏫ <b>공통 증가</b> (2개+ ETF 동시 확대)')
        for n, c in cons_inc:
            lines.append(f'    · {n} ({c}개 ETF)')
    cons_rem = _consensus(rem_counter)
    if cons_rem:
        lines.append('  🔻 <b>공통 제외</b> (2개+ ETF 동시 매도)')
        for n, c in cons_rem:
            lines.append(f'    · {n} ({c}개 ETF)')
    top_held = hold_counter.most_common(3)
    if top_held:
        lines.append('  🏆 최다 보유: ' + ', '.join(f'{n}({c})' for n, c in top_held))

    for section_lines in sheet_sections:
        lines.extend(section_lines)
    if not sheet_sections:
        lines.append('\n변동 없음 (전일 대비 신규/변화 없음)')
    if unchanged:
        lines.append(f'\n📍 <b>변동 없음</b>: {", ".join(unchanged)}')

    msg = '\n'.join(lines)
    print(msg)
    send_telegram(msg)

    # === 금요일: 주간 WoW 엑셀 첨부 ===
    if today.weekday() == 4:
        try:
            wk_summary = build_weekly_summary(today)
            if wk_summary:
                send_telegram(wk_summary)
                print('  📅 주간 요약 발송 완료')
            import generate_excel
            xlsx_path = generate_excel.main()
            if xlsx_path:
                send_telegram_document(xlsx_path, caption=f'📑 주간 ETF 엑셀 리포트 ({date_str(today)})')
                print(f'  📎 금요일 주간 엑셀 첨부 완료: {xlsx_path}')
            else:
                print('  ⚠️ 주간 엑셀 생성 결과 없음 — 첨부 생략')
        except Exception as e:
            print(f'  ⚠️ 주간 엑셀 생성/첨부 실패: {e}')

if __name__ == '__main__':
    main()

