<div align="center">

# 📝 SmartExamGrader (答题卡智能识别批改系统)

**高精度 · 离线优先 · 多模板自适应 · 学情错题本一体化评卷系统**

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python Version](https://img.shields.io/badge/Python-3.10%2B-brightgreen.svg)](https://www.python.org/)
[![OpenCV](https://img.shields.io/badge/OpenCV-4.x-orange.svg)](https://opencv.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)]()

[English Document](README.en.md) · [更新日志](软件更新日志.md) · [报告问题](../../issues)

</div>

---

## 🌟 项目亮点

`SmartExamGrader` 是一款专为中小学、职业院校及教育机构一线教师打造的**高精度答题卡与试卷智能识别批改系统**。系统深度融合传统计算机视觉（OpenCV）与现代 OCR / 深度学习算法，旨在将教师从繁杂重复的阅卷、算分与学情统计工作中解放出来。

- 🔒 **离线优先与数据隐私**：核心识别、图像透视校正与成绩数据库均在本地计算机运行，学生隐私与成绩绝对安全。
- 🎯 **自适应模板与倾斜矫正**：支持相机拍照与扫描仪进纸，通过角点标记与定位线自动进行亚像素级四点透视校正，抗轻度折痕与旋转。
- ⚡ **客观题极速批量批改**：多线程并行处理，自适应形态学与动态阈值判定填涂，秒级完成全班客观题识别与赋分。
- ✍️ **主观题智能评卷与红笔批注**：独创红色笔迹图层分离算法，自动提取教师批注圈记与扣分数字；同时提供轻量级局域网协同阅卷 Web 界面。
- 📊 **学情诊断与错题本生成**：自动关联试卷知识点矩阵，一键导出班级成绩单、得分率分析报表及图文并茂的“学生专属错题本”。
- 📦 **企业级数据迁移与备份**：内置完整业务数据包导出/导入工具，支持跨机器搬家及从旧版本一键无损数据迁移。

---

## 🖥️ 核心功能全景

| 模块 | 功能说明 |
| :--- | :--- |
| **模板向导** | 可视化交互式定义考号区、客观题区、主观题区，支持一键保存与切换多种考卷规格 |
| **批量批阅** | 拖拽整个文件夹或批量扫描图，全自动定位、裁切、识别考号、核对客观题并计算总分 |
| **主观题协同** | 本地一键启动轻量级局域网 Web 阅卷服务，多位教师可在平板或手机浏览器端分题协同打分 |
| **知识点分析** | 自动解析学生失分薄弱知识点，输出正答率梯队分布表与雷达图数据 |
| **个性化错题本** | 高清切片提取学生错题原图与手写轨迹，自动排版为可打印的错题复习卷（Word/PDF） |
| **数据保障体系** | 支持全量业务数据包（会话、配置、成绩、模板）一键打包备份与跨电脑无损迁移恢复 |

---

## 🚀 快速上手

### 方式一：直接下载开箱即用免安装版（推荐非开发人员使用）

1. 前往 **[Releases 发行版页面](../../releases)** 下载最新发布的压缩包：`SmartExamGrader-vX.X.X-Windows-Portable.zip`。
2. 将压缩包解压到本地任意目录（例如 `D:\SmartExamGrader`，**路径尽量避免特殊字符**）。
3. 双击根目录下的 **`启动答题批改系统.bat`** 即可直接启动主界面。

### 方式二：开发者源码运行

#### 1. 环境准备
确保本机已安装 **Python 3.10 或更高版本**（推荐 3.11/3.14）以及 Git。

```bash
# 克隆本仓库
git clone https://github.com/your-username/SmartExamGrader.git
cd SmartExamGrader
```

#### 2. 安装 Python 依赖
```bash
# 安装核心运行依赖（OpenCV、Pillow、NumPy、OpenPyXL 等）
pip install -r app/requirements-main.txt

# (可选) 若需使用 PaddleOCR 文本深度识别功能：
pip install -r app/requirements-paddleocr.txt
```

#### 3. 启动程序
```bash
# Windows 用户直接双击启动脚本：
启动答题批改系统.bat

# 或在命令行中通过 Python 启动：
python answer_card_stable_bootstrap.py
```

---

## 📂 项目结构

```text
SmartExamGrader/
├── app/                                 # 核心业务源码目录
│   ├── core/                            # 底层业务组件
│   │   ├── db_session.py                # 本地 SQLite 评卷会话引擎
│   │   ├── data_backup_manager.py       # 业务数据包导出/导入备份管理器
│   │   ├── web_sync.py                  # 局域网/云端协同阅卷数据同步
│   │   └── ...
│   ├── enhanced_answer_card_gui_stats.py # 主程序图形界面 (GUI)
│   ├── marker_detector.py               # 答题卡角标与定位线高精度检测
│   ├── template_registry.py             # 模板注册与几何配置引擎
│   ├── knowledge_analysis.py            # 学情与知识点矩阵诊断
│   ├── wrongbook_generator.py           # 错题切片与专属错题本生成
│   ├── manual_grading_web_server.py     # 协同阅卷 Web 独立服务端
│   └── requirements-main.txt            # Python 核心依赖清单
├── answer_card_stable_bootstrap.py      # 自适应环境引导启动器
├── 启动答题批改系统.bat                    # 便捷启动脚本
├── 从旧版本迁移数据.bat                    # 旧版本数据一键平滑升级迁移工具
├── 选择题套模板工具_GUI_不处理答案.py       # 试卷排版辅助小工具
├── 模板试卷红横线检测工具_GUI.py            # 打印试卷定位线快速校正工具
├── LICENSE                              # 开源许可证 (Apache 2.0)
└── README.md                            # 项目使用说明文档
```

---

## 🔄 旧版本数据迁移与数据安全

如果你曾经使用过旧版本的答题卡批改程序，或需要将办公电脑上的历史成绩、试卷模板迁移到新电脑：

1. **自动迁移工具**：直接双击根目录下的 **`从旧版本迁移数据.bat`**，选择你旧版本的解压目录或历史备份 `.zip`，程序将自动进行只读安全迁移（支持多会话成绩合并与智能去重，**绝不改动旧版原始文件**）。
2. **业务数据包导出与导入**：
   - 在主界面顶部菜单点击 **`📦 导出全量业务数据包 (.zip)`**，可将所有会话成绩、模板与答案打包为一个轻量压缩包。
   - 在新电脑或新版软件中点击 **`📥 导入业务数据包 (.zip)`**，即可一键还原所有评卷数据。

---

## 🤝 参与贡献

欢迎提交 Issue 或 Pull Request！
1. Fork 本仓库。
2. 新建特性分支 (`git checkout -b feature/AmazingFeature`)。
3. 提交代码更改 (`git commit -m 'feat: Add some AmazingFeature'`)。
4. 推送到你的远程分支 (`git push origin feature/AmazingFeature`)。
5. 在 GitHub / Gitee 提交 Pull Request。

---

## 📄 开源许可证

本项目基于 [Apache 2.0 许可证](LICENSE) 开源发布。无论是个人学习、学术研究还是教育机构部署，均可自由使用与二次修改。商业衍生请保留原作者版权声明与许可证说明。

---

<div align="center">
⭐ 如果本项目对您的教学或科研工作有所帮助，请在 GitHub / Gitee 上为我们点一个 Star！
</div>
