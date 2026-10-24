# 批次规则

init后analysis_rules.json中的确认状态为false。代理先依据当前用户实际指令填写，缺少实质信息时询问，不捏造确认原话。拍摄信息另存batch_metadata.json。

目前仅实现RGB8非加权原始亮度、不扣背景；其他profile会被拒绝。

```json
{
  "schema_version": 1,
  "measurement": {"profile": "RGB8_unweighted_original_pixels_no_background", "confirmed": false},
  "inclusion": {
    "confirmed": false,
    "border_policy": null,
    "crossing_policy": null,
    "small_worm_policy": null,
    "minimum_visible_fraction": null
  },
  "evidence": {"user_quote": "", "scope": ""}
}
```

| 字段 | 可选值 | 含义 |
| --- | --- | --- |
| border_policy | visible_if_near_complete / complete_only / ask / visible_identifiable_body_exclude_short / all_visible_identifiable_segments | 近完整测可见 / 仅完整 / 询问 / 足够长可辨虫身且排除短段 / 所有可辨片段；以实际用户指令为准 |
| crossing_policy | independent_shared_pixels / exclude / ask | 每虫独立并允许共享像素 / 排除 / 逐图询问 |
| small_worm_policy | include_unless_abnormal / exclude / ask | 正常小虫纳入 / 排除 / 逐图询问 |
| minimum_visible_fraction | null或(0,1]的数 | 用户提出的近完整标准；不会自动测出完整比例 |
| fragment_policy（可选） | exclude_obvious_short / all_identifiable / ask | 排除明显短段 / 所有可辨段 / 询问；旧版未提供可保持null |

这些是给圈选代理的规则记录，不会自动改变轮廓或判断发育。即使有近完整标准，无法判定时也应询问。

纳入正反实例、原话和适用范围可以作为额外字段保存；不要从实例推导未经确认的长度阈值。用[eligibility](eligibility.md)记录逐虫统计资格。更新rules不会改变ImageJ数值缓存，但会列出因规则记录改变而过期的资格，最终统计须重新核定；逐图明确决定和已覆盖的旧规则不能混用。

确认后将两处confirmed设为true，填实际user_quote和适用scope，保存副本并导入：

```powershell
python -X utf8 scripts/workflow.py rules --run '本批目录' --file '已填写规则.json'
```

rules --run 本批目录可查看/建立模板；未确认规则不能measure。旧版批次首次用本版测量也需补此记录；已有会话中的明确确认可以直接引用，不要求用户重复确认。合成试测使用SYNTHETIC规则，不移入真实批次。
