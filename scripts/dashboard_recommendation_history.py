"""Read-only unadjusted daily-bar adapter for historical recommendation outcomes.

Provider order, parameters, parsing, cache and error behavior match the legacy
Eastmoney/Tencent reader. Constructing a fetcher performs no network request.
The caller still enforces complete sessions/freshness; not total return or PIT.
"""
from datetime import date, timedelta
from dashboard_recommendation_outcomes import number


def history_fetcher(http=None):
    """A fresh per-run cache and an injectable HTTP transport, never an AI entry."""
    if http is None:
        import requests
        http = requests
    requests = http
    KLINE_CACHE = {}
    HEADERS = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'}

    def kline_rows(code,target,end_target=None):
        end_target=end_target or target
        cache_key=(code,target.isoformat(),end_target.isoformat())
        if cache_key in KLINE_CACHE:return KLINE_CACHE[cache_key]
        # 沪市可转债以 11 开头；其余现有记录按股票/基金常用代码前缀判断。
        market='sh' if code.startswith(('5','6','9','11')) else 'sz';secid=('1.' if market=='sh' else '0.')+code
        start=(target.replace(day=1)-timedelta(days=10)).strftime('%Y%m%d')
        end=(end_target+timedelta(days=3)).strftime('%Y%m%d')
        rows=[]
        try:
            params={'secid':secid,'klt':'101','fqt':'0','lmt':'1000','beg':start,'end':end,'fields1':'f1,f2,f3,f4,f5,f6','fields2':'f51,f52,f53,f54,f55,f56','ut':'fa5fd1943c7b386f172d6893dbfba10b'}
            response=requests.get('https://push2his.eastmoney.com/api/qt/stock/kline/get',params=params,headers=HEADERS,timeout=20)
            response.raise_for_status();lines=(response.json().get('data') or {}).get('klines') or []
            for line in lines:
                cells=line.split(',')
                if len(cells)>=5:rows.append({'date':date.fromisoformat(cells[0]),'close':number(cells[2]),'high':number(cells[3]),'low':number(cells[4])})
        except (requests.RequestException,ValueError,TypeError):pass
        if rows:
            result=[x for x in rows if x['high']>0 and x['low']>0];KLINE_CACHE[cache_key]=result;return result
        try:
            symbol=market+code
            param=f'{symbol},day,{start[:4]}-{start[4:6]}-{start[6:]},{end[:4]}-{end[4:6]}-{end[6:]},80,'
            response=requests.get('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get',params={'param':param},headers=HEADERS,timeout=20)
            response.raise_for_status();data=(response.json().get('data') or {}).get(symbol) or {};raw=data.get('day') or []
            for cells in raw:
                if len(cells)>=5:rows.append({'date':date.fromisoformat(cells[0]),'close':number(cells[2]),'high':number(cells[3]),'low':number(cells[4])})
        except (requests.RequestException,ValueError,TypeError):pass
        result=[x for x in rows if x['high']>0 and x['low']>0];KLINE_CACHE[cache_key]=result;return result

    return kline_rows
