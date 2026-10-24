# 逐虫统计资格与确认范围

## 四种独立判断

- `object_type`：`worm`、`worm_fragment`或`uncertain`，由本图形态判断；不因短就当作非虫。
- `status`：`include`、`exclude`或`pending`，决定是否进入最终强度表。
- 边界认可：用户实际确认的范围，不能从数值核验、统计纳入或端点认可推导。
- 训练覆盖：所有目标是否标全；统计排除但仍可辨的虫段需要保留标注或在训练前裁出。

统计边界以本批真实指令为准。足够长的可见虫身、近完整虫可按约定统计可见部分；明显短片段按约定排除。完整正常小虫不能按绝对长度误排。先对照本批已明确的纳入和排除实例；没有证据时保持pending，不能把“可辨”扩展成“任意长度都统计”，也不能自设像素/90%阈值。

## 资格操作

```powershell
python $wf eligibility --run $runDir
# 编辑生成的statistical_eligibility_template.json，再导入；不需重测
python $wf eligibility --run $runDir --file '已填写资格.json'
python $wf finalize --run $runDir --audit
```

JSON有`schema_version:1`和`worms`列表。每行保留模板生成的`boundary_key`、`rules_key`、`image_id`、`worm_id`，填写：

```json
{
  "status": "exclude",
  "object_type": "worm_fragment",
  "visible_class": "short_fragment",
  "reason": "明显短段，按本批明确实例排除强度统计；轮廓保留",
  "evidence": "实际用户原话或已经确认的批次规则",
  "decision_source": "agent_applied_rule"
}
```

上面只展示应编辑的字段，不能代替含编号和指纹的完整行。`visible_class`可选`complete/near_complete/long_body/short_fragment/uncertain`。`decision_source=user_decision`仅用于用户对该图该虫实际做出的选择；按既定批次规则判定则用`agent_applied_rule`，不伪装为逐虫人工认可。明确图可直接应用已确认规则，不要求用户逐条确认。

导入可只提供部分行，其他行保持原状态。坐标或源图变化使对应单虫资格失效；规则记录变化使原资格失效。`eligibility_affected.json`列出受影响项，新模板将其设为pending，保留旧决定供追溯，不能静默套用。刷新模板后重新判断并填写证据；不机械复制旧状态来消除待定。

`finalize`要求所有图片ready、现测量有效、资格无待定/过期，保留原始数值；只有统计资格变化时无需重新Measure。`final_statistics/`含纳入的原生与全精度表、无强度值排除清单、全部资格、分组描述、纳入ROI/全部ROI及完整图复核。原始`imagej/`和`tables/`可能保留已排除区域的历史测量，属于技术追溯，不是最终强度统计。再次finalize时旧目录移到history。

`status`汇报资格计数与最终输出输入是否仍有效。后续修改规则或轮廓后，旧的final_statistics不会自动重写；检查状态并重新完成资格、finalize后才交付。

## 边界认可的范围

```powershell
# 用户只选择了尾端：不授予W02整条或整张图认可
python $wf review --run $runDir --images C_018 --scope feature --worms W02 --feature tail_endpoint_B --decision accept --evidence '实际用户原话'
# 用户明确认可一条虫的当前完整轮廓
python $wf review --run $runDir --images C_009 --scope worm --worms W03 --decision accept --evidence '实际用户原话'
# 仅明确认可当前整图全部轮廓时使用；兼容旧版默认frame
python $wf review --run $runDir --images C_009 --scope frame --decision accept --evidence '实际用户原话'
```

整图认可保存于`reviews.json`；单虫/端点保存于`scoped_reviews.json`，绑定当前坐标。单虫认可可以更新该ROI的BoundaryVerified，但不会自动把整图加入训练清单。后续有效的局部revise会阻止相关旧认可继续生效；新整图认可可覆盖更早的局部疑问。用户答复涉及虫数、端点或统计资格时，只记录相应事实，不能自动执行frame接受。
