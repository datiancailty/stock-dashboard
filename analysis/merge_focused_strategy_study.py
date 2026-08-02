#!/usr/bin/env python3
"""把东方财富三股回测形成的审慎进化规则合并进画像策略。"""
import json
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
profile_path=ROOT/'data/strategy-profile.json'; bt_path=ROOT/'data/strategy-backtest-week-day-down-month-mid.json'
profile=json.loads(profile_path.read_text()); bt=json.loads(bt_path.read_text())
stocks={s['code']:s for s in bt['stocks']}
now=datetime.now().astimezone().isoformat(timespec='seconds')

def compact(code):
 s=stocks[code]; a=s['allSignalDays']; e=s['episodeFirstSignal']
 return {'code':code,'name':s['name'],'signalDays':a['signals'],'episodes':s['episodes'],'day5':a['d5'],'day20':a['d20'],'day30':a['d30'],'day60':a['d60'],'medianMax30Pct':a['max30MedianPct'],'medianDrawdown30Pct':a['drawdown30MedianPct'],'episodeFirstDay30':e['d30'],'sourceRange':s['sourceRange']}

study={
 'id':'mx-2024-week-day-down-month-mid-v1','updatedAt':now,'status':'trial_rule','scope':['600887','600036','000538'],
 'source':'东方财富妙想金融数据，前复权日线','evaluationWindow':'2024-01-01至2026-07-31（数据接口最新交易日）',
 'signalDefinition':'沿用网页画像口径：日位置=当日内收盘所在三等分；周/月位置=截至当日的本周/本月高低区间三等分；周下+日下+月中。',
 'antiLookahead':'周/月仅使用信号日之前及当日数据；同一连续信号簇既统计每日信号，也按首日去重；未来表现不含分红、费用、滑点。',
 'stockEvidence':[compact(c) for c in ('600887','600036','000538')],
 'learnedConclusion':[
  '“周下+日下+月中”不是三只股票通用的立即买入按钮：招商银行短期较强，云南白药20至30日较强，伊利股份短中期表现偏弱。',
  '同一条件必须按股票区分等待周期；如果每天重复满足，只算同一观察窗口，不能把连续信号日当作多次独立胜率。',
  '位置指标只负责择时，仍需通过正式股息率、250日线、仓位身份和用户确认等前置条件。'
 ],
 'evolvedRules':[
  {'code':'600036','name':'招商银行','rule':'周下+日下+月中出现时，升级为“首批候选”，优先观察5至20个交易日；只在正式股息率仍达到本人起买线、未破坏核心仓边界时，由用户确认后小批执行。','evidence':'45个信号日：5日收益中位数+1.57%、胜率62.2%；20日中位数+1.19%、胜率64.4%；30日胜率降至51.1%。','status':'试行，不自动交易'},
  {'code':'000538','name':'云南白药','rule':'周下+日下+月中出现时，列为“耐心分批候选”，评价窗口以20至30个交易日为主，不按5日反弹决定规则成败；仍需正式股息率和仓位确认。','evidence':'36个信号日；20日中位数+1.14%、胜率70.6%，30日中位数+3.49%、胜率67.6%；5日优势较弱。','status':'试行，不自动交易'},
  {'code':'600887','name':'伊利股份','rule':'周下+日下+月中只保留为观察信号，不单独触发首批买入；需增加“正式股息率达标+日线止跌确认（次日不再创新低或重新离开日下）”后再人工复核。','evidence':'38个信号日；5日中位数-0.90%、胜率29.7%，20日中位数-0.50%、胜率40.5%，30日才转为中位数+1.26%、胜率51.4%，30日内中位最大回撤-5.18%。','status':'降级观察，待新样本验证'}
 ],
 'sharedExecutionRule':'首次出现信号可建立观察窗口；连续4个自然日内再次出现视为同一信号簇，不重复加仓。只有新信号簇且前次执行反馈已记录，才允许再次人工复核。',
 'promotionRule':'每只股票新增至少10个独立信号簇后复核；目标窗口胜率≥60%、收益中位数>0，且中位最大回撤不恶化，才考虑升级。否则保持或降级。',
 'limitations':['样本仅三只股票且时间较短，不能外推到其他股票。','未纳入股息现金流、交易费用、滑点、仓位大小和250日线过滤后的联合效果。','历史相关性不保证未来收益；所有动作由用户最终确认。'],
 'backtestArtifact':'data/strategy-backtest-week-day-down-month-mid.json'
}
profile['updatedAt']=now
profile['focusedStockStudies']=[x for x in profile.get('focusedStockStudies',[]) if x.get('id')!=study['id']]+[study]
evo=profile.setdefault('strategyEvolution',{})
evo['currentThoughts']=[
 '三股专项学习后，“周下+日下+月中”应从统一信号进化为分股规则：招商银行偏5至20日首批候选，云南白药偏20至30日耐心候选，伊利股份降级为需止跌确认的观察信号。',
 '连续出现的同一信号不能每天都当新机会；先按信号簇去重，再结合前一笔反馈决定是否复核下一批。',
 *[x for x in evo.get('currentThoughts',[]) if '周下+日下+月中' not in x][:5]
]
evo.setdefault('evolvedRules',[])
for r in study['evolvedRules']:
 evo['evolvedRules'].append({'rule':f"{r['name']}：{r['rule']}",'why':r['evidence'],'status':r['status'],'verificationMetric':study['promotionRule']})
profile.setdefault('auditMeta',{})['focusedEvolutionData']='东方财富妙想前复权日线；三股；2024-01-01至2026-07-31'
profile_path.write_text(json.dumps(profile,ensure_ascii=False,indent=2)+'\n')
checkpoint={'schemaVersion':1,'updatedAt':now,'profileVersion':profile.get('sourceVersion'),'tradeRecordCheckpoint':{'count':len(json.loads((ROOT/'data/trade-records.json').read_text()).get('records',[])),'lastUpdatedAt':json.loads((ROOT/'data/trade-records.json').read_text()).get('updatedAt')},'focusedStudies':{study['id']:{'sourceDataThrough':'2026-07-31','codes':study['scope'],'artifact':study['backtestArtifact'],'nextReview':study['promotionRule']}},'purpose':'下次只处理检查点之后的新增交易与新增行情，再与现有画像结论合并。'}
(ROOT/'data/strategy-analysis-checkpoint.json').write_text(json.dumps(checkpoint,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'ok':True,'focusedStudies':len(profile['focusedStockStudies']),'checkpoint':checkpoint['tradeRecordCheckpoint']},ensure_ascii=False))
