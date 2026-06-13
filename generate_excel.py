#!/usr/bin/env python3
# generate_excel.py - 주간 엑셀 리포트 생성 (태린이아빠 방식)

import os, json
from datetime import datetime, timedelta
from pathlib import Path
from config.etfs import SHEETS

try:
    import openpyxl
    from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
except ImportError:
    print('openpyxl 미설치: pip install openpyxl')
    raise

from fetch_daily import load_snapshot, date_str, prev_biz

DATA_DIR   = Path('data/snapshots')
OUTPUT_DIR = Path('output')
OUTPUT_DIR.mkdir(exist_ok=True)

CLR = {
    'header_bg':  '1F3864',
    'header_fg':  'FFFFFF',
    'sheet_bg':   '2E75B6',
    'sheet_fg':   'FFFFFF',
    'etf_bg':     'D6E4F0',
    'new_in':     'C6EFCE',
    'removed':    'FFCCCC',
    'increased':  'FFEB9C',
    'row_odd':    'F8F9FA',
    'row_even':   'FFFFFF',
    'border':     'BDD7EE',
}

def make_fill(hex_color):
    return PatternFill('solid', fgColor=hex_color)

def make_border(color='BDD7EE'):
    s = Side(style='thin', color=color)
    return Border(left=s, right=s, top=s, bottom=s)

def make_font(bold=False, color='000000', size=10):
    return Font(bold=bold, color=color, size=size, name='맑은 고딕')

def get_week_dates(today):
    dow = today.weekday()
    mon = today - timedelta(days=dow)
    return [mon + timedelta(days=i) for i in range(5)]

def load_week_snapshots(today):
    week_dates = get_week_dates(today)
    snaps = {}
    for d in week_dates:
        s = load_snapshot(d)
        if s:
            snaps[date_str(d)] = s
    return snaps, week_dates

def detect_week_changes(week_snaps, week_dates, etf_name):
    date_strs = [date_str(d) for d in week_dates]
    first_snap, last_snap = [], []
    for ds in date_strs:
        if ds in week_snaps and etf_name in week_snaps[ds]:
            if not first_snap:
                first_snap = week_snaps[ds][etf_name]
            last_snap = week_snaps[ds][etf_name]
    if not first_snap or not last_snap:
        return [], [], [], {}
    first_map = {h['name']: h for h in first_snap}
    last_map  = {h['name']: h for h in last_snap}
    new_in  = [n for n in last_map if n not in first_map]
    removed = [n for n in first_map if n not in last_map]
    increased = [
        n for n in last_map
        if n in first_map and last_map[n]['weight'] > first_map[n]['weight'] + 0.3
    ]
    return new_in, removed, increased, last_map

def write_sheet_tab(wb, sheet_cfg, week_snaps, week_dates):
    sheet_name = sheet_cfg['name']
    ws = wb.create_sheet(title=sheet_name[:31])
    etfs = sheet_cfg['etfs']
    ws.cell(1, 1, f'[ {sheet_name} ]').font = Font(bold=True, size=13, color=CLR['header_fg'], name='맑은 고딕')
    ws.cell(1, 1).fill = make_fill(CLR['header_bg'])
    ws.cell(1, 1).alignment = Alignment(horizontal='center')
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=5)
    headers = ['종목명', '비중(%)', '수량', '전주대비', '변화']
    for ci, h in enumerate(headers, 1):
        c = ws.cell(2, ci, h)
        c.fill = make_fill(CLR['sheet_bg'])
        c.font = make_font(bold=True, color='FFFFFF')
        c.alignment = Alignment(horizontal='center')
    cur_row = 3
    for etf in etfs:
        etf_name = etf['name']
        is_passive = etf.get('is_passive', False)
        label = f"{'[패시브] ' if is_passive else ''}{etf_name}"
        c = ws.cell(cur_row, 1, label)
        c.fill = make_fill(CLR['etf_bg'])
        c.font = make_font(bold=True, size=10)
        c.alignment = Alignment(horizontal='left', indent=1)
        ws.merge_cells(start_row=cur_row, start_column=1, end_row=cur_row, end_column=5)
        cur_row += 1
        result = detect_week_changes(week_snaps, week_dates, etf_name)
        new_in, removed, increased, last_map = result
        if not last_map:
            ws.cell(cur_row, 1, '데이터 없음').font = make_font(color='888888')
            cur_row += 1
            continue
        holdings = sorted(last_map.values(), key=lambda x: x['weight'], reverse=True)
        for i, h in enumerate(holdings):
            name = h['name']
            row_fill = make_fill(CLR['row_odd'] if i % 2 == 0 else CLR['row_even'])
            if name in new_in:
                row_fill = make_fill(CLR['new_in'])
                change_label = '🟢 신규'
            elif name in removed:
                row_fill = make_fill(CLR['removed'])
                change_label = '🔴 제외'
            elif name in increased:
                row_fill = make_fill(CLR['increased'])
                change_label = '🔼 증가'
            else:
                change_label = '-'
            vals = [name, h['weight'], h['qty'], '', change_label]
            for ci, val in enumerate(vals, 1):
                c = ws.cell(cur_row, ci, val)
                c.fill = row_fill
                c.font = make_font()
                c.border = make_border()
                if ci == 2:
                    c.number_format = '0.00"%"'
                    c.alignment = Alignment(horizontal='center')
                elif ci == 3:
                    c.number_format = '#,##0'
                    c.alignment = Alignment(horizontal='right')
                elif ci in (4, 5):
                    c.alignment = Alignment(horizontal='center')
                else:
                    c.alignment = Alignment(horizontal='left', indent=1)
            cur_row += 1
        cur_row += 1
    ws.column_dimensions['A'].width = 28
    ws.column_dimensions['B'].width = 10
    ws.column_dimensions['C'].width = 14
    ws.column_dimensions['D'].width = 10
    ws.column_dimensions['E'].width = 10
    ws.freeze_panes = 'A3'

def write_summary_tab(wb, week_snaps, week_dates):
    ws = wb.create_sheet(title='📋 주간요약', index=0)
    ws.cell(1, 1, '📊 주간 ETF 신호 요약').font = Font(bold=True, size=14, name='맑은 고딕', color=CLR['header_fg'])
    ws.cell(1, 1).fill = make_fill(CLR['header_bg'])
    ws.cell(1, 1).alignment = Alignment(horizontal='center')
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=6)
    week_label = f"{date_str(week_dates[0])} ~ {date_str(week_dates[-1])}"
    ws.cell(2, 1, week_label).font = make_font(color='666666')
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=6)
    headers = ['섹터', 'ETF명', '신규 편입', '비중 증가', '제외', '신호']
    for ci, h in enumerate(headers, 1):
        c = ws.cell(3, ci, h)
        c.fill = make_fill(CLR['sheet_bg'])
        c.font = make_font(bold=True, color='FFFFFF')
        c.alignment = Alignment(horizontal='center')
    cur_row = 4
    for sheet_cfg in SHEETS:
        sheet_name = sheet_cfg['name']
        for etf in sheet_cfg['etfs']:
            etf_name = etf['name']
            result = detect_week_changes(week_snaps, week_dates, etf_name)
            new_in, removed, increased, last_map = result
            if not (new_in or removed or increased):
                continue
            signal = ''
            if len(new_in) >= 2:
                signal = '🔴 주목'
            elif new_in:
                signal = '🟡 신규'
            elif increased:
                signal = '🔼 증가'
            row_data = [sheet_name, etf_name, ', '.join(new_in[:3]) or '-', ', '.join(increased[:3]) or '-', ', '.join(removed[:3]) or '-', signal]
            for ci, val in enumerate(row_data, 1):
                c = ws.cell(cur_row, ci, val)
                c.font = make_font()
                c.border = make_border()
                c.alignment = Alignment(horizontal='left' if ci <= 2 else 'center', wrap_text=True)
                if signal == '🔴 주목':
                    c.fill = make_fill(CLR['new_in'])
            cur_row += 1
    ws.column_dimensions['A'].width = 14
    ws.column_dimensions['B'].width = 30
    ws.column_dimensions['C'].width = 25
    ws.column_dimensions['D'].width = 25
    ws.column_dimensions['E'].width = 20
    ws.column_dimensions['F'].width = 10
    ws.freeze_panes = 'A4'

def main():
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo('Asia/Seoul')).date()
    while today.weekday() >= 5:
        today -= timedelta(days=1)
    print(f'=== 주간 엑셀 생성: {date_str(today)} 기준 ===')
    week_snaps, week_dates = load_week_snapshots(today)
    print(f'로드된 스냅샷: {list(week_snaps.keys())}')
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    write_summary_tab(wb, week_snaps, week_dates)
    for sheet_cfg in SHEETS:
        write_sheet_tab(wb, sheet_cfg, week_snaps, week_dates)
    filename = f'etf_weekly_{date_str(today)}.xlsx'
    path = OUTPUT_DIR / filename
    wb.save(path)
    print(f'✅ 엑셀 저장 완료: {path}')
    return str(path)

if __name__ == '__main__':
    main()
