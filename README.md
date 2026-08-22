# pvztools

Plants vs. Zombies（PvZ1 及各类改版）自动化框架 / PvZ1 automation framework.

> **状态：0.0.1 为占名占坑版本，尚无可用 API。** 正式版本发布前请勿依赖本包。

## 目标

- **内存层**：附加游戏进程，通过特征码（AOB）扫描自适应定位基址（阳光、刷新倒计时、游戏时钟等），兼容原版多版本与各类改版。
- **轨道 DSL**：解析社区轨道语言（如 `P|I-P|P|I-P 601|1150|601|1150`、`IP-P|IP-P 1738|1738`），支持上下半场、过渡符、波长与起手/收尾特判。
- **执行器**：以游戏内部时钟（cs）为基准调度操作，而非墙钟延时。
- **阵容生成**：按场地、卡槽与节奏（ch4/ch5/ch6/P4…）生成并运行无尽阵容。
- **修改器**：可选、默认关闭的内存修改功能。

## 与现有工具的关系

本项目受 AsmVsZombies (AvZ)、lmintlcx/pvz、pvztoolkit 等启发，目标是提供一个 `pip install` 即用的 Python 框架。本包**不包含任何游戏文件**。

## 安装

```bash
pip install pvztools
```

仅支持 Windows。

## 许可证

MIT
