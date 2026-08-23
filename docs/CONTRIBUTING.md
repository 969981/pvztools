# 贡献指南 / Contributing

## 中文

- **环境**：Windows，Python >= 3.9，仅标准库（不要引入第三方运行时依赖）。src 布局：代码在 `src/pvztools/`。
- **跑测试**：

  ```bash
  cd pvztools
  PYTHONPATH=src PYTHONIOENCODING=utf-8 python -m pytest tests -q
  ```

  测试不需要游戏运行（用假会话 / 假进程）。涉及真机的验证请在 PR 里写明构建（如 Steam GOTY 1096）与日期。
- **地址 / 函数**：新增地址表条目要注明来源（re-plants 源码、反编译、社区表）与验证方式；调用约定（`this` 在 EAX/ECX/EDX/ESI）必须从反编译序言和调用方推导，不能猜。
- **安全**：任何写内存 / 补丁 / 注入功能默认关闭，必须经 `Safety` 门禁与 `--yes` 确认；只读路径不得有副作用。
- **名称与文案**：植物 / 僵尸名走 `pvztools.names`（官方中英名 + 社区昵称）；面向用户的提示用 `pvztools.i18n._()`，在 `MESSAGES` 里同时补 `zh` / `en`。
- **风格**：类型注解、短 docstring、`__all__`；不要重排无关代码；commit / PR 描述写清"改了什么、为什么、怎么验证"。

## English

- **Environment**: Windows, Python >= 3.9, stdlib only (no third-party runtime dependencies). src layout: code lives in `src/pvztools/`.
- **Run tests**:

  ```bash
  cd pvztools
  PYTHONPATH=src PYTHONIOENCODING=utf-8 python -m pytest tests -q
  ```

  Tests do not need the game running (fake sessions / processes). For live verification, state the build (e.g. Steam GOTY 1096) and the date in the PR.
- **Addresses / functions**: every new address-table entry needs a source (re-plants, decompilation, community tables) and how it was verified; calling conventions (`this` in EAX/ECX/EDX/ESI) must be derived from the decompiled prologue and callers, never guessed.
- **Safety**: memory writes / patches / injection are off by default and go through the `Safety` gates plus `--yes`; read-only paths must have no side effects.
- **Names and messages**: plant / zombie names go through `pvztools.names` (official zh / en + community nicknames); user-facing strings use `pvztools.i18n._()` with both `zh` and `en` entries in `MESSAGES`.
- **Style**: type hints, short docstrings, `__all__`; do not reshuffle unrelated code; describe what / why / how-verified in commits and PRs.
