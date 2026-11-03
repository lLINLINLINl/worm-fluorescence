# 操作与数据格式

命令在PowerShell中运行。`$wf`使用任务专用变量，值为技能目录中`scripts/workflow.py`的绝对路径；`$runDir`为本批输出目录。

```powershell
$wf = (Resolve-Path './scripts/workflow.py').Path
$runDir = '批次输出目录绝对路径'
python $wf init --source '原图文件夹绝对路径' --run $runDir
python $wf prepare --run $runDir --images C_001 C_002
# 先按references/analysis_rules.md记录并导入本批规则，再测量
python $wf measure --run $runDir
python $wf export --run $runDir --audit
python $wf review --run $runDir --images C_001 --decision accept --evidence '用户明确认可C_001当前轮廓'
python $wf export --run $runDir
python $wf status --run $runDir
python $wf inspect --run $runDir --images C_001
python $wf crops --run $runDir --images C_001
python $wf metadata --run $runDir
python $wf question --run $runDir --images C_001 --worms W01 --note '需要判断的具体边界'
python $wf eligibility --run $runDir
# 填好生成的资格模板后导入，再输出最终统计
python $wf eligibility --run $runDir --file '已填写资格.json'
python $wf finalize --run $runDir --audit
```

`init`要求新目录位于源文件夹之外；按源目录第一层子目录保留组名，其他层级仍归入第一层。编号为可安全写入ImageJ宏的ASCII编号，非ASCII组名使用G001等编号并在清单保留实际组名。文件自然排序，实际组别以清单为准；根目录散图不自动推断实验组。只有RGB8单帧通过检查；无支持模式时保留原图、告诉用户差异，并先验证新的测量口径。

以上示例从技能目录运行。首次环境配置和共享安装见[安装说明](../INSTALL.md)；`init/import/adopt`可加`--config 配置.json`。默认配置为环境变量WORM_FLUORESCENCE_CONFIG指定文件，或技能中的references/local_environment.json。`init`新建未知元数据模板，不把本机路径配置中的实验确认当作新批次的确认。

v0.2以来支持`inspect`、`crops`、`crop-review`、`metadata`和`set-environment`；局部坐标、覆盖审核、候选状态和元数据格式见[复核与裁剪](review_and_crops.md)。新增显示/QC模块不进入ImageJ计算缓存指纹；改变实际测量引擎或配置仍正常失效。源代码升级后仍需以实际回归结果确认缓存兼容。

`prepare`默认一次最多准备12张新预览；用`--images`限制当前需要看的图。显示倍率默认3，可用`--factor`更改。文件`inspection/*_view.png`和命令返回的尺寸定义显示坐标，不改变测量像素。

## 轮廓输入

`source_inventory.json`由脚本生成，保存来源路径、哈希、宽高和显示尺寸。`selections.json`为列表：

```json
[
  {
    "image_id": "C_001",
    "status": "ready",
    "decision_source": "按用户已确认的可见虫体规则；本图个体明确",
    "view_width": 1000,
    "view_height": 754,
    "annotation_coverage": "complete",
    "excluded": [],
    "worms": [
      {
        "worm_id": "W01",
        "note": "完整虫体，排除外围亮点",
        "points_view": [[100,100],[140,105],[150,150],[110,150]]
      }
    ]
  }
]
```

上面的坐标仅展示数据格式，不是线虫形状模板。真实轮廓来自该图足够分辨率的目视判定或已审核ROI，不能套用矩形示例。

- 也可用`points_original`直接保存原图像素坐标，此时它优先于`points_view`。读取ROI时以原图坐标为准。
- `annotation_coverage=complete`只有在整图所有可辨目标已标注后才能填写。测量排除但仍可辨的其他虫/虫段使其变成`incomplete`。
- `excluded`记录`location`、`reason`、`kind`。只有明确非虫物体才写`kind=non_worm`；虫/虫段或不确定对象写`worm_fragment`/`unknown`，整图会被挡在直接训练清单之外。
- 不能确定的图保持`pending`，不写猜测轮廓。未纳入虫的空图只有经过目视确认后才可用`worms=[]`且`empty_reviewed=true`；是否适合训练还取决于全图有没有未标虫段。

## 预览拼图坐标转换

单幅无边框预览仍可使用`points_view`。若在带标题或并排拼图上取点，使用独立辅助脚本先转原图坐标，不把画布坐标直接填进`points_view`。

输入为轮廓列表，每图含`preview_transform`，每虫仅提供`points_canvas`。例如画布2000×780、右侧图像面板起点(1000,26)、面板1000×754：

```json
{
  "canvas_size": [2000,780],
  "panel_origin": [1000,26],
  "panel_size": [1000,754]
}
```

以上只定义显示变换，不是示例ROI。面板尺寸须来自实际生成记录；不从截图猜测。`points_canvas`中每个点必须位于该面板像素中心范围内，标题栏、邻图、非有限数值或混合坐标字段均报错，不裁点补救。

```powershell
python '技能目录/scripts/preview_coordinates.py' --inventory '本批/source_inventory.json' --input '拼图坐标.json' --output '原图坐标_新文件.json'
```

输出仅新建文件，保留变换和来源指纹供追溯；不自动覆盖selections.json。将输出轮廓叠回原始图片，核对两侧和头尾后再采用。像素中心端点映射为`(原尺寸-1)/(面板尺寸-1)`；当前只支持无旋转、无翻转的缩放与平移。

## 交付偏好与刷新范围

全精度主表`included_precise.csv`固定列序为：`Group,ImageID,SourceFile,WormID,Mean,Min,Max,Area_px,Area_ImageJ,AreaUnit,BackgroundSubtracted,Method,InclusionNote,BoundaryVerified`。CSV数值不按展示位数舍入；`included_ImageJ_Results.csv`保留原生显示精度，不能据它重新构造全精度值。无纳入项时仍保留完整表头。

可在本机`references/local_preferences.json`设置`{"precise_csv_name":"希望使用的文件名.csv"}`。finalize在默认全精度表外生成逐字节相同的命名副本，并让复核页主链接指向它；文件名只能是安全的CSV基本名。本机偏好不进入Git或分享包，不替其他用户设定偏好。Excel仅按需另行生成。

| 变更 | 所需操作 |
|---|---|
| 轮廓/源图变化 | 源图变化另建批次；轮廓变化只measure受影响图，再重新判断受影响资格、导出 |
| 仅纳入决定、质量标记或可见类型改变 | eligibility后finalize；数值不重测，改变纳入颜色的整图才重绘 |
| 仅理由文字、CSV文件名 | 资格理由通过eligibility导入；文件名改本机偏好，然后finalize；不重测，不重绘未变整图 |
| 局部框、增强显示、问题标记 | 对指定图运行inspect/question；不启动Measure，不重复生成整批视图 |

finalize仍校验测量有效性和文件指纹；完整图按源图、当前轮廓、纳入颜色、字体及显示版本缓存。只有缓存指纹与文件校验值均匹配才复用，损坏或缺失只重绘对应图；`preview_generated/preview_reused`记录实际数量。修改代码的渲染方式时须提升显示版本。不要为提速跳过来源核验、独立计算核验或有必要的看图。

## 已有批次和修改

`import --source 旧批次 --run 新目录 --images 编号`只导入来源与轮廓，不凭空导入验收状态或测量缓存，适合小规模实际验证。`adopt --run 旧格式批次`仅在每图已有实际ImageJ宏、运行日志、原生逐虫CSV和掩膜时使用；它独立核验原图、ROI、标注、掩膜、像素统计和原生行后才登记缓存，不启动新的Java测量，也不自动接受边界。

编辑已交付批次前先另存新版。可复制可变输出；对只读图像副本使用硬链接省磁盘时，不得对链接目标写入或执行图像另存覆盖。缓存不含输出目录路径，因此同样内容复制到新版后仍可命中；源、像素坐标、ROI编号、ImageJ/Java/偏好或引擎代码变化会失效。输出文件缺失或校验值变化也会失效。

计算在本批`.staging`内完成并通过独立核验后才替换当前输出；旧输出移到本批`history`，失败暂存保留供诊断。所有移动目标限制在当前批次目录内。脚本不改原始图片和ImageJ偏好，不递归清理旧交付。

当前引擎沿用本机ImageJ的Area/Mean/Min/Max、精度3位和RGB不加权设置。设置不同立即停下核对，不擅自重写偏好。每条ROI执行实际`Measure`后单独存原生行，再合并；不要依赖同一结果窗不断增加Label列后仍累计所有虫。数值独立复算只说明对当前ROI计算正确，外缘正确性由单独验收决定。

## 状态和交付

`last_run.json`有`measured_images`、`cache_hits`、待圈图；`audit_summary.json`有图片数、虫数、待处理图、已验收图和训练可用图。命令只输出紧凑状态，长列表在文件中保留，终端最多显示前25项。

局部复核报告还保留同一源图、同一虫号修订前后的面积、Mean和统计资格。仅改变资格或显示设置不改变测量缓存；旧记录无历史Mean时留空。比较含义及兼容范围见[修订影响](review_and_crops.md#圈线修订的数值影响)。

`reviews.json`保存整图用户原话和当前`boundary_key`，`scoped_reviews.json`另记单虫及端点。旧验收不覆盖后续坐标改动；重新修改后须重新展示、确认。`BoundaryVerified`代表用户接受助手轮廓，不等价于独立专家手工真值。使用当前验收图建立训练集时，不能再把它们作为独立测试集自证精度。

v0.4增加测量前`question`、分范围`review`、`eligibility`和`finalize`。完整用法见[外缘检查](boundary_checks.md)和[统计资格](eligibility.md)。旧的`export`保留所有已测轮廓用于技术追溯；`finalize`才输出按逐虫资格筛选的最终统计。旧批不会自动认定所有轮廓纳入，需依据已有实际证据补齐资格。
