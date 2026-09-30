"""All distinct multi-issuer source rows fail before calendar writers."""
import contextlib,io,json,unittest
from datetime import date
from unittest.mock import patch
import part4_official_announcement_sync as m

class MultiIssuerTests(unittest.TestCase):
 def test_publisher_wording_never_overrides_distinct_provider_issuers(self):
  titles=['合成甲:关于合成乙2026年中期利润分配预案公告',
          '合成甲:合成乙及合成甲2026年中期利润分配预案公告',
          '合成甲:2026年中期利润分配预案公告']
  for title in titles:
   row={'art_code':'AN202609200000000001','notice_date':'2026-09-20','title':title,'columns':[],
        'codes':[{'stock_code':'600000','short_name':'合成甲'},{'stock_code':'600999','short_name':'合成乙'}]}
   with self.subTest(title=title),contextlib.ExitStack() as stack:
    output=io.StringIO();written=[]
    def rpc(w,c,t,name,body):
     if name=='personal_sync_part4_dividend_notices':written.extend(body['p_events']);return {'stored':len(written)}
     if name=='personal_sync_part4_dividend_dates':return {'stored':len(body['p_events'])}
     return {'events':written}
    stack.enter_context(patch('sys.argv',['sync','sync','--from','2026-09-01','--to','2026-09-30','--include-implementation-dates']))
    stack.enter_context(patch.object(m,'load_private_session',return_value=(None,None,None)))
    stack.enter_context(patch.object(m,'private_watchlist',return_value=[m.Stock('600000','合成甲')]))
    stack.enter_context(patch.object(m,'fetch_notice_page',return_value={'list':[row],'page_size':100,'total_hits':1}))
    secret=stack.enter_context(patch.object(m,'part4_writer_secret',return_value='synthetic'))
    calls=stack.enter_context(patch.object(m,'private_rpc',side_effect=rpc))
    stack.enter_context(contextlib.redirect_stdout(output))
    self.assertEqual(m.main(),2)
    secret.assert_not_called();calls.assert_not_called()
    self.assertEqual(json.loads(output.getvalue())['status'],'error')

if __name__=='__main__':unittest.main()
