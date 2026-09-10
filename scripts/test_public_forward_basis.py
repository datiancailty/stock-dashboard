"""Synthetic-only public-source contracts; no live/provider/private access."""
import unittest, copy, importlib.util
from pathlib import Path

ASOF='2026-09-09T21:00:00+08:00'
STOCKS=[{'code':'600000','name':'合成甲'}]
def row(report='2025-12-31',amount='8.8',stage='实施分配',notice='2026-06-01',**kw):
 return dict(SECURITY_CODE='600000',SECUCODE='600000.SH',SECURITY_NAME_ABBR='合成甲',REPORT_DATE=report+' 00:00:00',PRETAX_BONUS_RMB=amount,ASSIGN_PROGRESS=stage,IMPL_PLAN_PROFILE=f'10派{amount}元(含税,扣税后7.92元)',NOTICE_DATE=notice+' 00:00:00',PLAN_NOTICE_DATE='2026-03-01 00:00:00',EQUITY_RECORD_DATE='2026-06-10 00:00:00' if stage=='实施分配' else None,EX_DIVIDEND_DATE='2026-06-11 00:00:00' if stage=='实施分配' else None,**kw)
def report(text):
 return {'code':'600000','id':'AN2026082000000001','title':'合成甲:2026年半年度报告摘要','date':'2026-08-21','sourceUrl':'https://data.eastmoney.com/notices/detail/600000/AN2026082000000001.html','pages':['重要提示。董事会审议的报告期利润分配预案或公积金转增股本预案'+text+'第二节公司基本情况'],'pageCount':1}
class PublicBasisTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  path=Path(__file__).with_name('public_forward_basis.py')
  assert path.exists(), 'public forward adapter not implemented'
  spec=importlib.util.spec_from_file_location('public_forward_basis',path);cls.mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.mod)
 def build(self,rows,reports):return self.mod.build_records(rows,reports,STOCKS,ASOF)[0]
 def test_foreign_body_codes_are_never_silently_suppressed(self):
  doc=report('公司计划不派发现金红利。')
  cover='合成甲股份有限公司2026年半年度报告摘要证券代码：600000证券简称：合成甲公告编号：2026-001'
  for issuer in ['合成乙股份有限公司','本公司合成甲股份有限公司','关于合成甲股份有限公司']:
   bad=copy.deepcopy(doc)
   bad['pages']=[cover+'一、'+bad['pages'][0]+'三、重要事项。'+issuer+'（股票简称：合成丙，股票代码：000002）。']
   before=copy.deepcopy(bad)
   with self.assertRaisesRegex(ValueError,'body_identity_conflict'):self.build([row()],[bad])
   self.assertEqual(bad,before)

 def test_precise_per_ten_and_latest_interim_replacement(self):
  rows=[row(),row('2025-06-30','5'),row('2026-06-30','5','董事会决议通过','2026-08-21')]
  before=copy.deepcopy(rows);r=self.build(rows,[report('每10股派发现金5元（含税）。')])
  self.assertEqual((r['status'],r['amount']),('ready',1.38));self.assertEqual(rows,before)
  self.assertEqual(r['components']['interim']['year'],2026)
 def test_latest_known_periods_do_not_roll_on_calendar_boundaries(self):
  rows=[row(),row('2025-06-30','5')]
  for asof in [ASOF,'2026-07-01T09:00:00+08:00','2027-01-01T09:00:00+08:00']:
   with self.subTest(asof=asof):
    r=self.mod.build_records(rows,[],STOCKS,asof)[0]
    self.assertEqual((r['status'],r['amount']),('ready',1.38))
    self.assertEqual(r['components']['interim']['year'],2025)
    self.assertEqual(r['components']['annual']['year'],2025)
  # The known report's own fiscal year, not the observation year, supplies zero.
  doc=report('公司计划不派发现金红利。');doc.update(title='合成甲:2025年半年度报告摘要',date='2025-08-21')
  r=self.mod.build_records([row()], [doc],STOCKS,ASOF)[0]
  self.assertEqual((r['status'],r['amount']),('ready',.88));self.assertEqual(r['components']['interim']['year'],2025)
  # A genuinely known new period without a quantified plan DOES replace old good data.
  r=self.build(rows,[report('本报告期暂无预案。')]);self.assertEqual(r['status'],'missing')
  self.assertEqual(r['components']['interim']['year'],2026)
 def test_known_new_report_without_bound_section_cannot_fall_back(self):
  doc=report('无');doc['pages']=['合成甲2026年半年度报告摘要。财务资料，没有可绑定分配章节。']
  r=self.build([row(),row('2025-06-30','5')],[doc])
  self.assertEqual(r['status'],'missing');self.assertEqual(r['components']['interim']['year'],2026)
  self.assertIsNotNone(r['components']['interim']['source']['official_notice'])
 def test_explicit_non_distribution_is_only_zero_source(self):
  r=self.build([row()],[report('公司计划不派发现金红利，不送红股，不以公积金转增股本。')])
  self.assertEqual((r['status'],r['amount']),('ready',.88));self.assertEqual(r['components']['interim']['status'],'no_distribution')
 def test_no_plan_not_same_as_zero(self):
  for text in ['无','否','本报告期无利润分配预案及公积金转增股本预案。','公司董事会将适时制定并实施中期分红方案。']:
   with self.subTest(text=text):
    r=self.build([row(),row('2025-06-30','5')],[report(text)])
    self.assertEqual(r['status'],'missing');self.assertIsNone(r['amount'])
 def test_explicit_fiscal_non_distribution(self):
  r=self.build([row()],[report('公司2026年半年度不进行利润分配或公积金转增股本。')])
  self.assertEqual(r['components']['interim']['status'],'no_distribution')
 def test_wrong_year_negative_does_not_supply_zero(self):
  r=self.build([row()],[report('公司2025年半年度不进行利润分配或公积金转增股本。')])
  self.assertNotEqual(r['status'],'ready')
 def test_report_quantified_distribution_fills_structured_omission(self):
  r=self.build([row()],[report('公司经本次董事会审议通过的利润分配预案为：向全体股东每10股派发现金红利10.38元（含税）。')])
  self.assertEqual((r['status'],r['amount']),('ready',1.918))
  self.assertEqual(r['components']['interim']['status'],'announced')
 def test_current_distribution_scheme_heading_is_bound(self):
  doc=report('向全体股东每10股派发现金6.7元（含税）。')
  doc['pages'][0]=doc['pages'][0].replace('利润分配预案或公积金转增股本预案','利润分配方案或公积金转增股本方案')
  r=self.build([row(),row('2025-06-30','5')],[doc])
  self.assertEqual((r['status'],r['components']['interim']['amount']),('ready',.67))
  self.assertEqual(r['components']['interim']['year'],2026)
 def test_current_cash_claims_support_explicit_per_share_and_ordinary_stock(self):
  cases=[('本次中期每股派发现金红利人民币0.37元（含税）。',.37),
         ('2026年中报分红每股派发股息0.06元（含税）。',.06),
         ('董事会建议派发2026年半年度现金股息每股人民币0.137元（含税）。',.137),
         ('本公司拟向全体股东派发2026年中期现金股利，每股0.47元（含税）。',.47),
         ('董事会建议派发2026年中期普通股现金股息，每10股派发人民币1.327元（含税）。',.1327)]
  for text,amount in cases:
   with self.subTest(text=text):
    r=self.build([row(),row('2025-06-30','5')],[report(text)])
    self.assertEqual(r['status'],'ready')
    self.assertEqual(r['components']['interim']['amount'],amount)
    self.assertEqual(r['components']['interim']['status'],'announced')
  for text in ['2025年中报分红每股派发股息0.37元（含税）。本报告期暂无预案。',
               '本次中期股息每股0.37港元（含税），A股按另行公告汇率折算。',
               '本报告期每股收益0.37元（含税）。',
               '本次中期现金股息不超过每股0.37元（含税）。']:
   with self.subTest(blocked=text):self.assertIsNone(self.build([row()],[report(text)])['amount'])
 def test_important_notice_window_binds_current_interim_only(self):
  for end in ['第二节公司基本情况','二、释义','2.公司基本情况简介']:
   with self.subTest(end=end):
    doc=report('unused')
    doc['pages']=['合成甲2026年半年度报告摘要。一、重要提示。2025年中期现金股息每股人民币0.91元（含税）。'
                  '2025年度现金股息每股人民币1.2元（含税）。董事会建议派发2026年半年度现金股息每股人民币0.137元（含税）。'
                  +end+'本报告期优先股每10股派发现金红利99元（含税）。']
    r=self.build([row(),row('2025-06-30','5')],[doc])
    self.assertEqual(r['status'],'ready');self.assertEqual(r['components']['interim']['amount'],.137)
    self.assertNotIn('优先股',r['components']['interim']['source']['official_notice']['excerpt'])
  for text in ['一、重要提示。每股现金股息人民币0.137元（含税）。二、释义',
               '一、重要提示。2025年半年度现金股息每股人民币0.137元（含税）。第二节公司基本情况',
               '一、重要提示。2026年半年度现金股息每股人民币0.137元（含税）。没有可验证结束边界',
               '一、重要提示。无。第二节公司基本情况。2026年半年度现金股息每股人民币0.137元（含税）。']:
   with self.subTest(blocked=text):
    doc=report('unused');doc['pages']=[text]
    self.assertIsNone(self.build([row(),row('2025-06-30','5')],[doc])['amount'])
 def test_midyear_cash_wording_with_business_section_boundary(self):
  doc=report('unused');doc['title']='合成甲:合成甲2026年中期报告摘要'
  doc['pages']=['合成甲2026年中期报告摘要。一、重要提示。经董事会批准，本公司将向股东派发2026年中期股息每股现金人民币0.29元（含税）。'
                '本次中期股息总额以登记日总股数为准。二、报告期主要业务。公司回顾每股现金人民币9元（含税）的历史股息。']
  r=self.build([row()],[doc]);self.assertEqual(r['status'],'ready')
  self.assertEqual(r['components']['interim']['amount'],.29)
  self.assertNotIn('主要业务',r['components']['interim']['source']['official_notice']['excerpt'])
 def test_interim_scope_cannot_absorb_final_full_year_or_preferred_cash(self):
  for other in ['2026年全年现金股息每股人民币0.93元（含税）。',
                '2026年末期现金股息每股人民币0.93元（含税）。',
                '2026年中期优先股现金股息每股人民币0.93元（含税）。']:
   with self.subTest(other=other):
    doc=report('2026年中期普通股现金股息每股人民币0.37元（含税）。'+other)
    r=self.build([row()],[doc]);self.assertEqual(r['status'],'ready')
    self.assertEqual(r['components']['interim']['amount'],.37)
  for text in ['2026年全年现金股息每股人民币0.93元（含税）。',
               '2026年中期优先股现金股息每股人民币0.93元（含税）。']:
   with self.subTest(only_other=text):self.assertIsNone(self.build([row()],[report(text)])['amount'])
 def test_distribution_claims_are_bound_to_current_period(self):
  cases=[('2025年度利润分配已实施：每10股派发现金红利5元（含税）。2026年半年度暂未制定利润分配预案。','missing',None),
         ('2025年半年度公司计划不派发现金红利。本报告期每10股派发现金红利5元（含税）。','ready',1.38),
         ('公司计划不派发现金红利。本报告期每10股派发现金红利5元（含税）。','conflict',None),
         ('2025年度利润分配已实施。每10股派发现金红利5元（含税）。本报告期暂无预案。','missing',None)]
  for text,status,amount in cases:
   with self.subTest(text=text):
    r=self.build([row()],[report(text)]);self.assertEqual((r['status'],r['amount']),(status,amount))
 def test_cancelled_or_terminated_amount_is_negative_evidence(self):
  for word in ['取消','终止','撤销']:
   with self.subTest(word=word):
    doc=report(f'原每10股派发现金红利5元（含税）的中期利润分配预案已{word}。')
    r=self.build([row()],[doc]);self.assertEqual(r['status'],'negated');self.assertIsNone(r['amount'])
 def test_conditional_total_adjustment_keeps_unchanged_per_share_plan(self):
  clauses=['如实施前公司总股本发生变动，维持每股分配金额不变，以登记日总股数为基准相应调整分配总额。',
           '如享有利润分配权的股本总额发生变动，则以登记日股本为基数，按照每股分配金额不变的原则对分红总额进行调整。']
  for clause in clauses:
   with self.subTest(clause=clause):
    doc=report('本次中期每股派发现金红利人民币0.37元（含税）。'+clause)
    r=self.build([row(),row('2026-06-30','3.7','董事会决议通过','2026-08-21')],[doc])
    self.assertEqual((r['status'],r['components']['interim']['amount']),('ready',.37))
    self.assertEqual(r['components']['interim']['status'],'announced')
  for extra,status in [('本次方案已修订。','missing'),('本次分配金额调整为每股0.47元（含税）。','missing'),
                       ('本次方案已取消。','negated'),('本次方案更正。','missing')]:
   with self.subTest(extra=extra):
    doc=report('本次中期每股派发现金红利人民币0.37元（含税）。'+clauses[0].rstrip('。')+'，'+extra)
    old=row('2026-06-30','3.7','董事会决议通过','2026-08-01')
    r=self.build([row(),old],[doc]);self.assertEqual(r['status'],status);self.assertIsNone(r['amount'])
 def test_newer_report_cannot_corroborate_old_draft(self):
  old=row('2026-06-30','5','董事会决议通过','2026-08-01')
  for text,status in [('原中期利润分配预案已取消，本报告期无利润分配预案。','negated'),
                      ('本报告期暂无量化分配预案。','missing')]:
   with self.subTest(text=text):
    r=self.build([row(),old],[report(text)]);self.assertEqual(r['status'],status)
    self.assertIsNone(r['amount']);self.assertEqual(r['components']['interim']['published_at'],'2026-08-21')
  implemented=copy.deepcopy(old);implemented.update(ASSIGN_PROGRESS='实施分配',EQUITY_RECORD_DATE='2026-08-09',EX_DIVIDEND_DATE='2026-08-10')
  r=self.build([row(),implemented],[report('本报告期无新利润分配预案。')])
  self.assertEqual(r['status'],'ready')
  self.assertIsNone(r['components']['interim']['source']['official_notice'])
  self.assertTrue(r['components']['interim']['source']['field'].get('subsequentUnquantifiedReports'))
 def test_foreign_currency_and_policy_not_inferred(self):
  for text in ['每10股派发现金红利10港元（含税）。','现金分红金额占净利润35%。','预计每10股派发现金红利10元（含税）。']:
   self.assertIsNone(self.build([row()],[report(text)])['amount'])
 def test_future_payment_is_announced_not_implemented(self):
  value=row('2026-06-30','5');value['NOTICE_DATE']='2026-09-01 00:00:00';value['EQUITY_RECORD_DATE']='2026-09-10 00:00:00';value['EX_DIVIDEND_DATE']='2026-09-11 00:00:00'
  r=self.build([row(),value],[]);self.assertEqual(r['components']['interim']['status'],'announced')
 def test_precision_not_rounded_by_provider_display(self):
  value=row('2026-06-30','13.448110');value['NOTICE_DATE']='2026-08-25 00:00:00'
  r=self.build([row(),value],[]);self.assertEqual(r['components']['interim']['amount'],1.344811)
 def test_contradictory_plan_and_amount_block(self):
  value=row('2026-06-30','5','董事会决议通过');value['IMPL_PLAN_PROFILE']='10派6元(含税)'
  self.assertEqual(self.build([row(),value],[])['status'],'conflict')
 def test_no_report_or_unquantified_pre_disclosure_stays_missing(self):
  value=row('2026-06-30',None,'预披露');value['IMPL_PLAN_PROFILE']='现金分红占利润35%'
  r=self.build([row(),value],[report('无')]);self.assertEqual(r['status'],'missing')
 def test_identity_date_and_full_page_validation(self):
  for change in [{'code':'000001'},{'pageCount':2},{'sourceUrl':'https://evil.invalid/doc'},{'date':'2026-10-01'}]:
   with self.subTest(change=change):
    notice=report('公司计划不派发现金红利。');notice.update(change)
    with self.assertRaises(ValueError):self.build([row()],[notice])
 def test_body_identity_period_and_pdf_page_contradictions_rejected(self):
  for prefix in ['证券代码：000001 合成乙 2026年半年度报告摘要。',
                 '合成甲2025年半年度报告摘要。', '第1页 共6页。']:
   with self.subTest(prefix=prefix):
    doc=report('每10股派发现金红利5元（含税）。');doc['pages'][0]=prefix+doc['pages'][0]
    doc['collection']={'coverageComplete':True,'issuerVerified':True}
    with self.assertRaisesRegex(ValueError,'forward_basis_public_notice_'):self.build([row()],[doc])
 def test_explicit_preferred_instrument_code_is_not_ordinary_issuer(self):
  doc=report('本次中期每股派发现金红利人民币0.37元（含税）。')
  doc['pages'][0]='股票代码：600000 合成甲2026年半年度报告摘要。'+doc['pages'][0]
  doc['pages'][0]+='本行境内优先股（股票简称：合成优1，股票代码：360099）本期股息已支付。'
  r=self.build([row()],[doc])
  self.assertEqual((r['status'],r['components']['interim']['amount']),('ready',.37))
  self.assertIn('360099',doc['pages'][0])
  for extra in ['。股票代码：000002。', '。优先股股息已支付。股票代码：000002。',
                '。优先股情况：普通股股票代码：000002。']:
   with self.subTest(extra=extra):
    wrong=copy.deepcopy(doc);wrong['pages'][0]+=extra
    with self.assertRaisesRegex(ValueError,'body_identity_conflict'):self.build([row()],[wrong])
 def test_preferred_mention_cannot_hide_an_unbound_main_code(self):
  for clause in ['优先股股息已支付，证券代码：000002。',
                 '优先股（股票简称：合成优1），证券代码：000002。']:
   with self.subTest(clause=clause):
    doc=report('每10股派发现金红利5元（含税）。')
    doc['pages'][0]='证券代码：600000。'+doc['pages'][0]+clause
    with self.assertRaisesRegex(ValueError,'body_identity_conflict'):self.build([row()],[doc])
 def test_pdf_pages_are_not_api_text_page_count(self):
  doc=report('每10股派发现金红利5元（含税）。')
  doc['pages'][0]='证券代码：600000 合成甲2026年半年度报告摘要。第1页 共2页。'+doc['pages'][0]+'第2页 共2页。'
  r=self.build([row()],[doc]);self.assertEqual(r['status'],'ready')
  self.assertEqual(r['components']['interim']['source']['official_notice']['pdfPageCount'],2)
  self.assertEqual(r['components']['interim']['source']['official_notice']['pageCount'],1)
 def test_quarterly_cash_is_not_silently_called_interim(self):
  r=self.build([row(),row('2025-09-30','5')],[report('无')]);self.assertIsNone(r['amount'])
 def test_special_distribution_is_labelled_and_correlated_to_report(self):
  summary=report('公司经本次董事会审议通过的利润分配预案为：向全体股东每10股派发现金红利10.38元（含税）。')
  special=copy.deepcopy(summary);special.update(id='AN2026082000000002',title='合成甲:关于2026年特别分红方案的公告',sourceUrl='https://data.eastmoney.com/notices/detail/600000/AN2026082000000002.html')
  special['pages']=['公司2026年特别分红方案：向全体股东每10股派发现金红利10.38元（含税）。']
  r=self.build([row()],[summary,special])
  self.assertEqual(r['components']['interim']['source']['field']['distributionNature'],'special')
  self.assertEqual(r['components']['interim']['source']['official_notice']['relatedNotice']['sourceUrl'],special['sourceUrl'])

 def test_direct_current_interim_special_is_labelled_including_table_tie(self):
  doc=report('公司本次中期特别分红方案为每10股派发现金红利5元（含税）。')
  for rows in [[row()],[row(),row('2026-06-30','5','董事会决议通过','2026-08-21')]]:
   with self.subTest(table=len(rows)):
    r=self.build(rows,[doc]);self.assertEqual((r['status'],r['amount']),('ready',1.38))
    self.assertEqual(r['components']['interim']['source']['field']['distributionNature'],'special')
  historic=report('2025年度曾特别分红。本报告期每10股派发现金红利5元（含税）。')
  self.assertNotEqual(self.build([row()],[historic])['components']['interim']['source']['field']['distributionNature'],'special')
 def test_per_share_special_cash_is_labelled_before_inclusion(self):
  for label in ['特别现金股息','非经常性现金分红']:
   with self.subTest(label=label):
    doc=report(f'本次中期{label}每股人民币0.37元（含税）。')
    r=self.build([row()],[doc]);self.assertEqual(r['status'],'ready')
    self.assertEqual(r['components']['interim']['amount'],.37)
    self.assertEqual(r['components']['interim']['source']['field']['distributionNature'],'special')
 def test_normalized_precision_and_total_contract_are_checked_locally(self):
  for value in ['8.800000000001','10000000','-1','NaN']:
   with self.subTest(table=value),self.assertRaisesRegex(ValueError,'forward_basis_component_amount_invalid'):
    self.build([row(amount=value)],[report('公司计划不派发现金红利。')])
  for value in ['5.000000000001','10000000']:
   with self.subTest(report=value),self.assertRaisesRegex(ValueError,'forward_basis_component_amount_invalid'):
    self.build([row()],[report(f'本报告期每10股派发现金红利{value}元（含税）。')])
  with self.assertRaisesRegex(ValueError,'forward_basis_total_amount_invalid'):
   self.build([row(amount='5000000')],[report('本报告期每10股派发现金红利5000000元（含税）。')])
  r=self.build([row(amount='8.800000000010')],[report('公司计划不派发现金红利。')])
  self.assertEqual(r['components']['annual']['amount'],.880000000001)
  r=self.build([row(amount='5000000')],[report('本报告期每10股派发现金红利4999999元（含税）。')])
  self.assertEqual(r['amount'],999999.9)
 def test_hkd_declared_a_share_payment_is_unconfirmed_cny_not_two_dividends(self):
  doc=report('unused')
  doc['pages']=['公司代码：600000。合成甲2026年半年度报告摘要。重要提示。'
    '董事会决定派发2026年中期股息每股0.94港元（含税）。'
    '股息以港元计值和宣派，其中A股股息以人民币支付，折算汇率按宣派日前一周中间价平均值计算；港股股息以港元支付。公司简介。其他业务。']
  pending=row('2026-06-30','8.16437','董事会决议通过','2026-08-21')
  result=self.build([row(),pending],[doc])
  self.assertEqual((result['status'],result['amount']),('conflict',None))
  self.assertEqual(result['reason'],'current_interim_cny_unconfirmed')
  evidence=result['components']['interim']['evidence']
  official=next(e['official_notice'] for e in evidence if e.get('official_notice'))
  self.assertIn('每股0.94港元',official['excerpt'])
  self.assertNotIn('公司简介',official['excerpt'])
  self.assertEqual(set(result),{'code','amount','status','reason','components','asOf'})
  direct=report('2026年中期A股每股派发现金红利人民币0.81644元（含税）；港股股息每股0.94港元（含税）。')
  result=self.build([row()],[direct])
  self.assertEqual(result['components']['interim']['amount'],.81644)
  self.assertEqual(result['components']['interim']['status'],'announced')
 def test_source_contract_is_explicit_and_sql_compatible(self):
  r=self.build([row()],[report('公司计划不派发现金红利，不送红股，不以公积金转增股本。')])
  src=r['components']['annual']['source'];self.assertEqual(src['field']['provider'],'eastmoney_public_dividend_table')
  self.assertIn('RPT_SHAREBONUS_DET',str(src));self.assertEqual(set(src),{'dto_title','header','raw_plan','raw_progress','raw_pretax','dto_code','field','name_map','published_at','normalization_reason','official_notice','scope_evidence','row_index','distribution_dates'})
if __name__=='__main__':unittest.main()
