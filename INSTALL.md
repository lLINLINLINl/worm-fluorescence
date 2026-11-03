# 安装与首次验证

推荐Windows和Python 3.13，另需可用的ImageJ 1.x与Java，以及能查看本地图片/运行Python的AI代理。本版固定依赖按Python 3.13验证；其他系统尚未验证。

## 安装技能

下载Release中的技能ZIP并解压完整worm-fluorescence目录，安装到Codex可发现的技能目录；当前官方用户级目录为~/.agents/skills。也可让Codex安装此GitHub仓库。保持SKILL.md、scripts、references的相对结构；只复制SKILL.md不够。同名技能已安装时先保留其私人配置，避免重复安装导致混淆。

官方目录说明：[Codex Skills](https://developers.openai.com/codex/skills/)。如未识别新技能，再重启Codex。

## 配置独立Python环境

在技能目录运行（PowerShell）：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-tested.txt
.\.venv\Scripts\python.exe -X utf8 scripts/environment.py configure --fiji '实际Fiji.app目录'
.\.venv\Scripts\python.exe -X utf8 scripts/environment.py doctor
```

requirements-tested.txt记录Python 3.13的实测依赖，其中NumPy 2.3.4要求Python >=3.11，Python 3.10不能直接照用这组固定版本。其他Python版本如需尝试，应使用requirements.txt选择相容依赖，并重新跑合成与真实样本检查；不视为本版已验证兼容。没有Windows的py启动器时，明确使用Python 3.13解释器创建环境。让代理执行命令时，明确使用上述虚拟环境解释器；技能不会自动切换解释器。

Fiji不带Java时用--java指定；JAR、偏好和字体分别支持--ij-jar、--prefs、--font。只用独立ImageJ也可明确传这四个路径。首次先在ImageJ确认并保存：Area、Mean gray value、Min & max gray value，三位小数，RGB不加权；若测量目的不同，不要为了通过诊断而改成此口径。

configure不会覆盖已有配置或修改ImageJ偏好；另存用--output 新配置.json。测量会把经核对的偏好复制到独立运行目录，并核查Java实际读取的位置。批次拍摄参数与纳入规则不会继承他人的确认。

## 合成验证（不是实验数据）

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/verify_install.py --config references/local_environment.json --output '新的验证输出目录'
```

输出verification.json，任何失败均返回非零退出码。脚本只使用程序生成的矩形，不验证真实虫的圈选精度。快速规则/格式检查可运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 自己的1—3张真实图

让代理确认规则、逐图描边，检查复核页，并在本机ImageJ重新打开同一原图和ROI进行测量核对。确认一致后再扩批。原图保留，结果写入源目录之外。

完整命令与selections.json格式见[操作说明](references/workflow.md)，规则见[analysis_rules](references/analysis_rules.md)。旧版批次首次测量需补规则记录；是否重测取决于源图、轮廓、测量口径和引擎指纹。本版未修改ImageJ测量引擎，匹配的缓存可复用；更换旧引擎或偏好后，以实际指纹检查为准。

从v0.3升级到v0.4时保留私人`references/local_environment.json`、`references/local_accepted_cases.md`与可选`references/local_preferences.json`。v0.4保持v0.3的测量引擎，匹配的数值缓存可复用；新增`eligibility`/`finalize`，原始`export`全量表不自动变成已筛选的最终统计。旧批的实际资格需要依据已有指令补记，不默认纳入所有轮廓；详情见[统计资格](references/eligibility.md)。

## 分享

`python scripts/package_skill.py --output 新分享目录`按release_files.json的逐文件白名单生成发行包，构建前扫描私人绝对路径和常见凭据，构建后验证包内文件名及SHA256。新文件须经审阅后显式加入白名单；图片、CSV、ROI、权重和第三方程序不在本版白名单中。扫描不能替代人工隐私检查，不应直接分享整个本机安装目录或实验运行目录。对方仍需配置环境和试测。软件开源不代表AI推理免费，也不保证所有模型得到相同轮廓。
