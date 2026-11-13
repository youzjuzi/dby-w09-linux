# Contributing / 参与贡献

This project targets one DBY-W09 baseline. Discuss substantial changes in an issue before a pull request. Keep changes small, preserve notices/SPDX identifiers and use the licence of the modified file (GPL-2.0-only unless stated otherwise). / 本项目面向单台 DBY-W09 基线；较大改动先在 issue 讨论，补丁保持单一目的，保留声明并沿用文件许可证。

Report the device/stock firmware, actual source commits, public bundle ID/digests, expected/observed behavior and relevant stable error codes. Classify evidence using [H/R/O/U](docs/status.md): recorded device checks, user observation, offline checks or unverified. State exactly what was tested; a dry-run or ordinary image file is not a card/boot test.

报告设备／原厂版本、实际源码提交、可公开的 bundle ID／摘要、预期与实际行为及稳定错误码。使用[证据级别](docs/status.md)说明实机输出、用户观察、离线或未验证；dry-run／普通镜像文件不能替代实卡启动。

Remove SSIDs, credentials, regulatory settings, IP/MAC addresses, serials, owner paths and identifying metadata before posting. Do not upload raw seeds, vendor files, stock images, private plan/panel/memory data or complete built bundles. Include only a reviewed, sanitized excerpt. Security-sensitive problems use the [security policy](SECURITY.md).

公开前移除网络名、凭据、法规设置、地址、序列号、所有者路径和身份元数据。不要上传原始种子、厂商文件、原厂镜像、私有计划／面板／内存数据或完整 bundle；仅提交审阅后的脱敏片段。安全问题按[安全政策](SECURITY.md)处理。

Storage changes need a clear scope and must preserve the existing write gates. Use the [installation guide](docs/install.md) ([中文](docs/install.zh-CN.md)) for the current workflow; install/verify/info/update/rollback/seed/safe-mode/reinstall-system are implemented; the installer menu and user-data backup/restore remain unimplemented. Contributions do not grant rights to redistribute third-party inputs. / 存储改动须说明范围、保留既有写入门禁；当前流程见安装指南，install／verify／info／update／rollback／seed／safe-mode／reinstall-system 已实现；安装器菜单与用户数据备份恢复未做。提交代码不等于取得第三方输入的再分发权。
