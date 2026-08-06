#!/usr/bin/env python3
"""把四只新增重点股票的日/周/月专项学习合并到个人画像与增量检查点。"""
import json
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PROFILE=ROOT/'data/strategy-profile.json'; STUDY=ROOT/'data/strategy-backtest-new-focused-stocks.json'; CHECK=ROOT/'data/strategy-analysis-checkpoint.json'
profile=json.loads(PROFILE.read_text(encoding='utf-8')); study=json.loads(STUDY.read_text(encoding='utf-8'))
now=datetime.now().astimezone().isoformat(timespec='seconds')

rules={
 '601985':{'rule':'中国核电：周下＋日下＋月中只能作为观察信号；在新增信号中，先等日线连续两天不再创新低或重新离开日下，再同时复核正式股息率、250日线和核心/机动仓。','assist':'若这三个位置再次同时出现，我只提示“观察/等待止跌”，不会把它列为首批买入。2024年曾较好，但2025—2026转弱，说明需要当前状态门控。'},
 '003816':{'rule':'中国广核：周下＋日下＋月中进入20—60个交易日观察窗口，不作为立即买入按钮；只有正式股息率达到你的门槛、250日线未恶化且新信号簇确认后，才人工复核小批分批。','assist':'该信号20日中位收益约为正但胜率仅中等，60日略好于20/30日；我会提供耐心观察和条件复核，不提供确定性买入结论。'},
 '601318':{'rule':'中国平安：周下＋日下＋月中暂不升级；增加止跌确认与估值/股息率前置条件，并优先确认250日线趋势。','assist':'历史20日略有正中位数，但最近年度明显转弱，不能用全历史平均数覆盖当前状态；出现信号时先告诉你“历史有过优势、近期降级”。'},
 '000423':{'rule':'东阿阿胶：历史上周下＋日下＋月中的20—60日结果最强，但最近年度转弱；暂定为“历史优势、当前降级”的条件式候选，不直接买入。','assist':'只有当新的独立信号簇出现、当前年度20日结果重新转正、正式股息率与250日线同时满足时，才允许小批试行；不能因为历史20日胜率62.2%就追入。'},
}

compact=[]
for s in study['stocks']:
 code=s['code']; a=s['primary']['signalDays']; e=s['primary']['episodeFirst']
 compact.append({'code':code,'name':s['name'],'source':s['source'],'sourceRange':s['sourceRange'],'signalDays':a['signals'],'episodes':e['signals'],'d5':e['d5'],'d20':e['d20'],'d30':e['d30'],'d60':e['d60'],'max30MedianPct':e['max30MedianPct'],'drawdown30MedianPct':e['drawdown30MedianPct'],'yearStats':s['primary']['yearStats'],'latestState':s['latestState'],'classification':s['learnedClassification'],'rule':rules[code]['rule'],'decisionAssist':rules[code]['assist']})

focused_id='public-2022-four-stocks-day-week-month-v1'
new_focus={
 'id':focused_id,'updatedAt':now,'status':'trial_rule_with_recent_regime_gate','scope':[s['code'] for s in study['stocks']],
 'stocks':compact,'signal':'日下+周下+月中（沿用现有位置画像口径）','evaluationWindow':study['evaluationWindow'],'entryMethod':study['entryMethod'],'dataSource':study['dataSource'],
 'selfLearningFindings':[
  '位置组合不是跨股票通用买点：同一“日下+周下+月中”在核电、广核、平安、阿胶上的观察窗口和近期稳定性不同。',
  '信号日收盘后必须用下一交易日开盘作为入场基准；这比用信号日收盘计算更保守，也更接近实际决策。',
  '全历史优势必须经过最近年度状态门控：东阿阿胶历史20日中位收益+2.17%、胜率62.2%，但最近年度20日中位收益-0.88%；历史结论不能直接覆盖当前转弱。',
  '连续信号先按4个自然日合并成观察窗口，避免把同一次下跌中的多天信号误当成多次独立机会。',
  '日、周、月信号负责择时，正式股息率、250日线、核心/机动仓与用户确认仍是前置条件；专项学习不会改写固定底线。'
 ],
 'decisionProtocol':[
  '第一层：先看正式股息率是否达到你的买入门槛；不满足时，位置信号只能观察。',
  '第二层：看250日线趋势是否恶化；若趋势向下，不因月线中部就立即接刀。',
  '第三层：看日/周/月位置和股票自己的观察窗口；不得把四只股票统一处理。',
  '第四层：区分核心仓、机动仓和例外买入；用户最终确认，系统不自动下单。'
 ],
 'upgradeDowngrade':'每只股票新增至少10个独立信号簇后复核；若当前年度与全历史方向一致且20日胜率≥60%、收益中位数>0、30日最大回撤不恶化，才考虑升级；若最近两个窗口转弱则降级为观察。',
 'limitations':['本次妙想API因调用次数达到上限未返回；新增四只采用官方公开行情端点失败后的腾讯公开日K回退，未提供前复权参数，结果仅作保守研究参考。','不含分红、费用、滑点、仓位大小和250日线联合过滤；不能证明未来收益。','当前年度部分信号簇数量较少，近期降级优先于全历史平均。']
}
old=profile.get('focusedStockStudies',[])
profile['focusedStockStudies']=[x for x in old if x.get('id')!=focused_id]+[new_focus]
profile['updatedAt']=now
evo=profile.setdefault('strategyEvolution',{})
new_thoughts=[
 '这次新增四只股票后，我把“历史表现”与“当前状态”分开：历史有优势不等于现在可以执行，最近年度转弱时必须先降级。',
 '我把信号的入场基准改为下一交易日开盘，主动避免用信号日收盘价造成的未来函数；以后辅助决策会优先给观察窗口和条件，而不是只给一个买卖动作。',
 '日、周、月位置的作用已经从统一买点进化为“股票特征化择时”：核电和平安偏等待确认，广核偏耐心观察，阿胶虽历史强但近期仍需状态确认。',
 '我会把同一连续信号合并为一个观察窗口，并把核心仓、机动仓、股息率和250日线作为前置门槛，减少重复提示和过度加仓。'
]
existing=evo.get('currentThoughts',[])
evo['currentThoughts']=(new_thoughts+[x for x in existing if x not in new_thoughts])[:10]
new_rules=[
 {'rule':x['rule'],'why':x['decisionAssist'],'status':('历史优势但近期降级' if x['classification']['tier']=='历史有优势但近期降级' else '试行观察'),'verificationMetric':new_focus['upgradeDowngrade']} for x in compact
]
old_rules=evo.get('evolvedRules',[])
for nr in new_rules:
 if not any(nr['rule']==x.get('rule') for x in old_rules): old_rules.append(nr)
evo['evolvedRules']=old_rules
evo['disclaimer']='新增四只股票专项结论采用保守的下一交易日开盘回测；数据来源与复权限制已单独标注，所有规则只供人工复核，不自动下单。'
meta=profile.setdefault('auditMeta',{})
meta['focusedEvolutionData']='原三只专项 + 新增四只专项；新增四只数据截止 '+study['stocks'][0]['sourceRange'][1]
meta['newFocusedStudySource']='妙想API额度耗尽；官方公开历史K线端点受限时使用腾讯公开日K线回退，未提供前复权参数'
PROFILE.write_text(json.dumps(profile,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
checkpoint=json.loads(CHECK.read_text(encoding='utf-8')) if CHECK.exists() else {'schemaVersion':1,'focusedStudies':{}}
checkpoint['updatedAt']=now
checkpoint.setdefault('focusedStudies',{})[focused_id]={'sourceDataThrough':study['stocks'][0]['sourceRange'][1],'codes':new_focus['scope'],'artifact':'data/strategy-backtest-new-focused-stocks.json','nextReview':new_focus['upgradeDowngrade'],'rawCache':'analysis/mx-focused-v2/（本地回测缓存，不上传）'}
checkpoint['purpose']='下次只处理检查点之后的新增交易与新增行情，再与现有画像结论合并；新增四只股票按照独立信号簇和最近状态增量复核。'
CHECK.write_text(json.dumps(checkpoint,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'ok':True,'studyId':focused_id,'stocks':[(x['name'],x['classification']['tier']) for x in compact],'profile':str(PROFILE),'checkpoint':str(CHECK)},ensure_ascii=False,indent=2))
