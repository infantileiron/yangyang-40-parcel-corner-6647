from collections import defaultdict
from functools import lru_cache
import numpy as np
from scipy.special import roots_legendre
from scipy.stats import beta, betabinom


FIELDS = ("old_test","old_fail","new_test","new_fail")
COLUMNS = (
    ("stage", "观察轮次"), ("old_test", "累计旧试样"), ("old_fail", "旧组损坏"),
    ("new_test", "累计新试样"), ("new_fail", "新组损坏"),
    ("posterior_gap", "后验平均改善（百分点）"), ("success", "达到改善的后验概率（%）"),
    ("projected", "末轮预测通过概率（%）"), ("decision", "试验动作"),
)
NODES, QUAD_WEIGHTS = roots_legendre(64)
QUANTILES = (NODES+1)/2
QUAD_WEIGHTS = QUAD_WEIGHTS/2


def validate(row):
    if row["stage"] not in range(1,5):
        raise ValueError("仅接受 1–4 轮既定观察时点")
    for key in ("old","new"):
        if not 0 <= row[f"{key}_fail"] <= row[f"{key}_test"] or row[f"{key}_test"] < 1:
            raise ValueError("各组至少投入一件，损坏数不能高于投入数")


@lru_cache(maxsize=64)
def old_posterior(tested, failed):
    return beta.ppf(QUANTILES,1+failed,1+tested-failed)


def posterior(old,failed,new,new_failed,gap):
    old_rates = old_posterior(old,failed)
    return float(np.dot(QUAD_WEIGHTS,beta.cdf(old_rates-gap,1+new_failed,1+new-new_failed)))


def predictive(old,old_failed,new,new_failed,remaining,gap,cutoff):
    future = np.arange(remaining+1)
    old_weights = betabinom.pmf(future,remaining,1+old_failed,1+old-old_failed)
    new_weights = betabinom.pmf(future,remaining,1+new_failed,1+new-new_failed)
    new_a = 1+new_failed+future
    new_b = 1+new+remaining-new_failed-future
    score = 0.0
    for count,weight in enumerate(old_weights):
        shifted = old_posterior(old+remaining,old_failed+count)-gap
        posterior_grid = QUAD_WEIGHTS @ beta.cdf(shifted[:,None],new_a[None,:],new_b[None,:])
        score += float(weight * np.sum(new_weights[posterior_grid >= cutoff]))
    return min(1.,max(0.,score))


def analyze(rows,parameters):
    cutoff = parameters.get("pass_prob",95)/100
    gap = parameters.get("min_effect",8)/100
    per_round = parameters.get("per_round",4)
    futility = parameters.get("futility",2)/100
    stages = defaultdict(lambda:[0,0,0,0])
    seen = set()
    for row in rows:
        validate(row)
        if row["sheet"] in seen:
            raise ValueError(f"试样批号 {row['sheet']} 重复")
        seen.add(row["sheet"])
        for index,field in enumerate(FIELDS):
            stages[row["stage"]][index] += row[field]
    if not stages or set(stages) != set(range(1,max(stages)+1)):
        raise ValueError("观察时点必须由第一轮连续录入")
    totals = [0,0,0,0]
    table, chart = [], []
    stopped = None
    for stage,batch in sorted(stages.items()):
        totals = [old+current for old,current in zip(totals,batch)]
        old,o_fail,new,n_fail = totals
        success = posterior(old,o_fail,new,n_fail,gap)
        expected_gap = (o_fail+1)/(old+2)-(n_fail+1)/(new+2)
        remaining = (4-stage)*per_round
        projected = predictive(*totals,remaining,gap,cutoff) if remaining else None
        if stopped:
            decision = "历史建议停点后的追加披露"
        elif success >= cutoff:
            stopped = (stage,"扩大试投")
            decision = "进入扩大试投人工复核"
        elif projected is not None and projected < futility:
            stopped = (stage,"暂缓")
            decision = "末轮通过机会不足，暂缓试制"
        elif remaining:
            decision = "保留下一轮预定跌落架时"
        else:
            decision = "末轮证据未达到扩投要求"
        table.append(dict(stage=stage,old_test=old,old_fail=o_fail,new_test=new,new_fail=n_fail,
                          posterior_gap=round(100*expected_gap,3),success=round(100*success,4),
                          projected=None if projected is None else round(100*projected,4),decision=decision))
        chart.append((f"第{stage}轮",round(100*success,3)))
    title = f"第 {stopped[0]} 轮首次建议{stopped[1]}" if stopped else table[-1]["decision"]
    return report(title,
                  f"至少改善 {100*gap:g} 个百分点的后验概率达到 {100*cutoff:g}% 才复核扩投。末轮预测假设剩余每轮、每组安排 {per_round} 件。",
                  [("已登记",len(table),"轮"),("累计试样",totals[0]+totals[2],"件"),("末次后验概率",table[-1]["success"],"%")],
                  COLUMNS,table,"各轮达到改善门槛的后验概率",chart,"%")


def seed():
    plans = [(2,1,3,1),(5,1,4,0),(5,0,6,1),(4,1,6,0)]
    return [dict(stage=stage,sheet=f"MQ-{stage}{index+1:02d}",
                 old_test=10+(stage+index)%3,old_fail=failures[2*index],
                 new_test=10+(stage+index)%3,new_fail=failures[2*index+1])
            for stage,failures in enumerate(plans,1) for index in range(2)]


def report(headline, summary, metrics, columns, rows, chart_label, chart_values, unit=""):
    return {
        "headline": headline, "summary": summary,
        "metrics": [{"label": label, "value": value, "unit": suffix} for label, value, suffix in metrics],
        "columns": [{"key": key, "label": label} for key, label in columns],
        "rows": [{key: row.get(key) for key, _ in columns} for row in rows],
        "chart": {"label": chart_label, "unit": unit,
                  "points": [{"label": label, "value": value} for label, value in chart_values]},
    }
