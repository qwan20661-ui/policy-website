# PEMatching 评分机制实现说明

> 当前实现基线：2026-07-31
> 业务口径以《企业政策匹配打分规则说明.md》为准，开发步骤与验证记录以《企业政策匹配打分与模型判断优化实施计划.md》为准。

## 1. 运行路径

`score_policy_against_enterprise()` 根据政策是否存在规则表选择路径：

| 路径 | 实现 |
|---|---|
| 规则表主路径 | `rule_engine_service.score_policy_against_rule_profile()` |
| 无规则表兜底 | `scoring_service.score_policy_against_enterprise()` 内部结构化条件评分 |

两条路径共用 `DEFAULT_MISSING_SCORE=0.1`、`PRESENT_DATA_BASE_SCORE=0.3` 和 `RECOMMENDATION_SCORE_THRESHOLD=0.6`。

## 2. 条件求值

- 缺失字段或语义事实为10分，来源 `default_missing_assumed`。
- 软性下限条件使用 `min(1, 0.3 + current/target)`。
- 软性上限或等值不满足为30分。
- 硬性数值或定性条件明确不满足为0分并形成硬性失败。
- 定性支持、反证、缺失在软性组为100、30、10；硬性反证为0。
- 规则不可计算为 `not_evaluable`，跳出评分分母并阻止推荐。

## 3. 聚合

规则表只提供政策规则和操作符，`ruleKind` 不再决定最终分。运行时根据企业事实、证据映射和校验结果确定证据层级；规则表原始 `ruleKind` 仅保留在逐条件结果中，供提取链路审计。

| 证据层级 | 计分桶 | 判定含义 | 普通计分处理 |
|---|---|---|---|
| `A1_exact` | `verified` | 企业原始字段或已回读的语义事实可与政策条件直接比较 | 参与逐条件同权平均 |
| `A2_derived` | `verified` | 由完整组合字段或本地确定性公式推导 | 参与逐条件同权平均 |
| `B_supporting` | `supporting` | 仅存在代理、引用或辅助证据，不能视作等价事实 | 参与逐条件同权平均 |
| `U_missing` | `unverified` | 未形成可核验企业事实，或按缺失默认口径处理 | 条件可评分时以该条件得分参与平均，默认缺失分为10分 |
| `R_rejected` | `unverified` | 证据路径非法或语义兼容性校验拒绝 | 不将被拒绝证据视作真实证据；后续按缺失/不可计算的条件结果处理 |
| `N_not_evaluable` | `excluded` | 规则目标、单位或结构无法可靠计算 | 不进入普通计分分母，并阻止推荐 |
| `S_skipped` | `excluded` | 当前企业不适用该条件分支 | 不进入普通计分分母 |

设本次运行中所有适用且可评分条件的得分集合为 `condition_score`，则：

```text
final_score = sum(condition_score) / count(condition_score)
```

每一条参与条件的权重相同。`verified`、`supporting`、`unverified` 三个桶用于展示证据质量及审计统计，不使用固定70%/30%再次加权；`coeff_C` 固定为 `1.0`。兼容输出中的 `score_A_direct`、`score_B_indirect` 分别表示核验证据桶和辅助证据桶的条件均分，不是最终分的固定权重分量。

无规则表路径根据结构化条件提取结果使用相同的逐条件等权方法，不复制缺失类别，也不重复向B类追加同一条件。

## 4. 模型事实

`semantic_match_service.py` 只提取事实，`rule_engine_service.py` 负责：

1. 校验证据路径存在。
2. 回读原始企业值。
3. 校验人员指标、主体角色、资质荣誉、地址、知识产权、年度和单位兼容性。
4. 不兼容事实降级为 `missing`，记录 `semantic_evidence_incompatible`。
5. 本地换算、比较和算分。

## 5. 推荐与接口

推荐要求无硬性失败、无 `not_evaluable` 且最终分不低于60。该结论仅表示当前企业数据与已结构化政策规则的匹配/召回信号，不能替代完整申报资格认定；日期、地域和主体等规则外资格守卫不再进入运行链路，仍需由业务人员结合政策原文、申报窗口和材料清单复核。API继续保留 `recommendationBlockers`，正常结果为空数组。

完整归档保留逐条件分数、`passSource`、模型事实、证据覆盖率、`evidenceTier`、`scoreBucket`、`bucketReason`、规则表原始 `ruleKind` 和兼容字段 `coeff_C=1.0`。轻量响应与完整归档先写入文件，再经 JDBC 服务写入达梦的运行索引；任一保存步骤失败，接口返回 `500`，不会返回不可追溯的成功结果。

## 6. 专项

- 直通车只由真实证据触发，最终分至少90。
- 科技型中小企业评价使用官方分档；部分维度缺失按维度满分10%补足，全部缺失交给条件级10分。

## 7. 回归入口

```powershell
python -m pytest backend/PEMatching/test_rule_engine_growth_scoring.py backend/PEMatching/test_semantic_rule_scoring.py backend/PEMatching/test_semantic_match_service.py backend/PEMatching/test_single_enterprise_match_api.py backend/PEMatching/test_excel_score_explanation.py backend/PEMatching/test_enterprise_api_loader.py backend/PEMatching/test_prompt_contracts.py -q
```
