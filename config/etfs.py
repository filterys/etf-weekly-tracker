# config/etfs.py
import os

TELEGRAM_CHAT_ID = int(os.environ.get('TG_CHAT_ID', '-1003794316481'))

# source별 fetch 방식
# samsung : samsungactive.co.kr API  (fund_id)
# time    : timefolio API            (idx)
# plus    : PLUS XML API             (fund_code)
# tiger   : miraeasset pdf.ajax      (ksdFund)
# kodex   : samsungfund.com API      (fund_id)

SHEETS = [
    {
        "name": "코스닥액티브",
        "etfs": [
            {"name": "KoAct 코스닥액티브", "code": "0163Y0", "source": "samsung", "params": {"fund_id": "2ETFU6"}},
            {"name": "TIME 코스닥액티브", "code": "0162Y0", "source": "time", "params": {"idx": "24"}},
            {"name": "PLUS 코스닥150액티브", "code": "0166N0", "source": "plus", "params": {"fund_code": "006399"}},
        ],
    },
    {
        "name": "바이오",
        "etfs": [
            {"name": "TIME K바이오액티브", "code": "463050", "source": "time", "params": {"idx": "20"}},
            {"name": "KoAct 바이오헬스케어액티브", "code": "462900", "source": "samsung", "params": {"fund_id": "2ETFJ9"}},
            {"name": "TIGER 기술이전바이오액티브", "code": "0168K0", "source": "tiger", "params": {"ksdFund": "KR70168K0008"}},
        ],
    },
    {
        "name": "AI/로봇",
        "etfs": [
            {"name": "TIME 글로벌AI인공지능액티브", "code": "456600", "source": "time", "params": {"idx": "19"}},
            {"name": "KoAct 글로벌AI&로봇액티브", "code": "471040", "source": "samsung", "params": {"fund_id": "2ETFL3"}},
            {"name": "KoAct AI인프라액티브", "code": "487130", "source": "samsung", "params": {"fund_id": "2ETFN8"}},
            {"name": "TIGER 코리아테크액티브", "code": "471780", "source": "tiger", "params": {"ksdFund": "KR7471780007"}},
        ],
    },
    {
        "name": "K컬처",
        "etfs": [
            {"name": "TIME K컬처액티브", "code": "410870", "source": "time", "params": {"idx": "15"}},
            {"name": "KoAct 글로벌K컬처밸류체인액티브", "code": "0132D0", "source": "samsung", "params": {"fund_id": "2ETFS5"}},
        ],
    },
    {
        "name": "방산",
        "etfs": [
            {"name": "TIME 글로벌우주테크&방산액티브", "code": "478150", "source": "time", "params": {"idx": "22"}},
            {"name": "KoAct K수출핵심기업TOP30액티브", "code": "0074K0", "source": "samsung", "params": {"fund_id": "2ETFR6"}},
            {"name": "PLUS K방산 [패시브]", "code": "449450", "source": "plus", "params": {"fund_code": "006388"}, "is_passive": True},
        ],
    },
    {
        "name": "배당성장",
        "etfs": [
            {"name": "KoAct 배당성장액티브", "code": "0162D0", "source": "samsung", "params": {"fund_id": "2ETFM2"}},
            {"name": "TIME Korea플러스배당액티브", "code": "441800", "source": "time", "params": {"idx": "12"}},
            {"name": "TIGER 코스닥액티브", "code": "0204S0", "source": "tiger", "params": {"ksdFund": "KR70204S0008"}},
        ],
    },
    {
        "name": "신재생/친환경",
        "etfs": [
            {"name": "KODEX 신재생에너지액티브", "code": "385510", "source": "kodex", "params": {"fund_id": "2ETFE5"}},
            {"name": "TIMEFOLIO K신재생에너지액티브", "code": "404120", "source": "time", "params": {"idx": "16"}},
            {"name": "KoAct 글로벌친환경전력인프라액티브", "code": "475070", "source": "samsung", "params": {"fund_id": "2ETFM9"}},
            {"name": "KODEX K-친환경조선해운액티브", "code": "445150", "source": "kodex", "params": {"fund_id": "2ETFH6"}},
        ],
    },
    {
        "name": "기타/혁신",
        "etfs": [
            {"name": "TIME K이노베이션액티브", "code": "385710", "source": "time", "params": {"idx": "17"}},
            {"name": "KoAct 반도체&2차전지핵심소재액티브", "code": "482030", "source": "samsung", "params": {"fund_id": "2ETFN2"}},
            {"name": "KODEX 로봇액티브", "code": "445290", "source": "kodex", "params": {"fund_id": "2ETFH5"}},
            {"name": "VITA MZ소비액티브", "code": "422260", "source": "vita", "params": {"fundCD": "E0001"}},
            {"name": "PLUS K제조업핵심기업액티브", "code": "0166S0", "source": "plus", "params": {"fund_code": "006400"}},
        ],
    },
]

ALL_ETFS = [etf for sheet in SHEETS for etf in sheet["etfs"]]
