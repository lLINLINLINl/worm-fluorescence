# 验证记录：v0.3.0-beta.1

本记录验证软件行为与数值一致性，不是线虫分割准确率报告。

## 已完成（2026-10-22）

- Windows、Python 3.13.5、Java 8、ImageJ 1.54p。
- 新建Python虚拟环境，include-system-site-packages=false，从PyPI安装requirements-tested.txt；pip check通过。
- 独立复制ImageJ JAR，使用全新合成偏好配置；实际Java加载私有偏好目录，原偏好不改。
- 8项快速测试通过：规则未确认/不合法、原图坐标、无效/越界多边形、16位图拒绝、源图变更、同源数据划分。
- 3张合成图、3个ROI实际ImageJ测量及独立像素复核通过，包括重合和空图。
- 固定期望结果匹配；重复运行0重测/3缓存命中，单图变化1重测/2命中。
- 轮廓变化撤销原认可、裁断标签阻止训练、撤回裁剪认可撤出可用清单、轮廓触底而掩膜不占末行的回归检查通过。
- 5张私人真实图回归：全精度值一致，3份原生汇总CSV逐字节一致，原图与旧结果未改；不公开这些图片、ROI或实验测量值。

完整的软件验证摘要见[verification_summary.json](verification_summary.json)。依赖精确版本见[requirements-tested.txt](../requirements-tested.txt)。

## 合成输出示例（不是实验数据）

| 合成ROI | Area_px | Mean |
| --- | ---: | ---: |
| DEMO_001_W01 | 2400 | 50.34625 |
| DEMO_001_W02 | 1700 | 75.0 |
| DEMO_002_W01 | 330 | 42.0 |

矩形只用于软件检查，不能当作新虫的轮廓模板。ImageJ栅格ROI与绘图函数的端点包含方式不同，验证固定值显式考虑该差异。

## 自己复现

按[安装说明](../INSTALL.md)运行unittest与scripts/verify_install.py。GitHub [自动验证](https://github.com/lLINLINLINl/worm-fluorescence/actions)在Windows运行同一套测试与真实ImageJ合成测量；是否通过以相应提交的运行状态为准。

## 验证的边界

本地干净环境指新虚拟环境和隔离ImageJ配置，仍使用同一台Windows与现有Java，不声称另一位操作者已完成安装。尚未验证macOS/Linux、所有Python版本或不同AI模型的一致视觉表现。浏览器按钮未做自动交互验证，已检查链接/脚本和PNG。没有独立人工真值集的准确率、显著性或额度节省百分比承诺。
