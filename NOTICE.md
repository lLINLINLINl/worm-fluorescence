# 来源与第三方依赖

本项目由lLINLINLINl在实际线虫荧光处理过程中，使用AI辅助编写和修订。规则与软件实现均需独立复核，不把AI生成等同于科学正确。

仓库从独立技能目录整理，未带入原研究工作区的Git历史、实验图、结果或权重。早期曾使用和比较[cyssayyes-source/worm_deep](https://github.com/cyssayyes-source/worm_deep)；本技能不导入其模块、不调用其脚本或权重，常规运行无须安装它，也不声称拥有其代码或模型。

运行依赖由使用者单独安装，保留各自许可；本项目MIT许可不替代第三方许可：

- [ImageJ](https://imagej.net/ij/)及Java：实际ROI测量，不随技能打包。
- [NumPy](https://numpy.org/)：像素复算。
- [Pillow](https://python-pillow.github.io/)：读图与预览。
- [OpenCV](https://opencv.org/)：掩膜预览。
- [roifile](https://github.com/cgohlke/roifile)：ImageJ ROI读写。
- [Shapely](https://shapely.readthedocs.io/)：多边形有效性检查。

YOLO与Labelme在此指输出格式，未打包其训练器。合成图由示例程序生成，不来自真实实验；AI模型、服务和额度不包含在本项目中。
