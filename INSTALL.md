# 安装与首次验证

需要Windows、Python 3.10或以上、可用的ImageJ 1.x与Java，以及能查看本地图片/运行Python的AI代理。本次实测版本见[验证记录](docs/VALIDATION.md)。其他系统尚未验证。

## 安装技能

下载Release中的技能ZIP并解压完整worm-fluorescence目录，安装到Codex可发现的技能目录；当前官方用户级目录为~/.agents/skills。也可让Codex安装此GitHub仓库。保持SKILL.md、scripts、references的相对结构；只复制SKILL.md不够。同名技能已安装时先保留其私人配置，避免重复安装导致混淆。

官方目录说明：[Codex Skills](https://developers.openai.com/codex/skills/)。如未识别新技能，再重启Codex。

## 配置独立Python环境

在技能目录运行（PowerShell）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-tested.txt
.\.venv\Scripts\python.exe -X utf8 scripts/environment.py configure --fiji '实际Fiji.app目录'
.\.venv\Scripts\python.exe -X utf8 scripts/environment.py doctor
```

requirements-tested.txt记录本次实测依赖；其他Python版本可用requirements.txt允许的范围安装，但仍需跑合成与真实样本检查。让代理执行命令时，明确使用上述Python解释器；技能不会自动切换解释器。

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

完整命令与selections.json格式见[操作说明](references/workflow.md)，规则见[analysis_rules](references/analysis_rules.md)。旧版批次首次测量需补规则记录；本版隔离偏好修正改变了引擎指纹，旧数值缓存会失效并重测，不会静默沿用旧引擎结果。

## 分享

scripts/package_skill.py --output 新分享目录生成脱离个人配置的ZIP，不含实验图、结果、权重、密钥或第三方程序。对方仍需配置环境和试测。软件开源不代表AI推理免费，也不保证所有模型得到相同轮廓。
