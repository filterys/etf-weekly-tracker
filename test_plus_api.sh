#!/bin/bash
# PLUS ETF API 파라미터 테스트 스크립트
# 맥미니에서 실행: bash test_plus_api.sh

BASE="https://www.plusetf.co.kr/api/v1/product/pdf/list"
REF="https://www.plusetf.co.kr/product/detail?n=006399"
DATE="20260612"
CODE="006399"

echo "=== PLUS ETF API 파라미터 테스트 ==="

echo -n "1. n + date: "
curl -s "$BASE?n=$CODE&date=$DATE" -H "Referer: $REF" | head -c 300
echo -e "\n---"

echo -n "2. productCode + standardDate: "
curl -s "$BASE?productCode=$CODE&standardDate=$DATE" -H "Referer: $REF" | head -c 300
echo -e "\n---"

echo -n "3. fund_code + std_dt: "
curl -s "$BASE?fund_code=$CODE&std_dt=$DATE" -H "Referer: $REF" | head -c 300
echo -e "\n---"

echo -n "4. POST n+date: "
curl -s -X POST "$BASE" -H "Referer: $REF" -d "n=$CODE&date=$DATE" | head -c 300
echo -e "\n---"

echo "=== 완료 ==="
