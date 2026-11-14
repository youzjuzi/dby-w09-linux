# Security / 安全政策

Maintenance targets the current development branch/current commit, with no supported release/tag series or response-time guarantee. Check the actual source revision and [known limitations](docs/status.md) when reporting a problem. / 支持范围为当前开发分支／当前提交，不承诺 release/tag 系列或响应时限；报告时请核对实际版本与[已知限制](docs/status.md)。

Unintended internal-storage writes, bypassed validation, unsafe archive handling and leaks through logs/generated files are security-sensitive. Submit through GitHub private vulnerability reporting (the repository's Security tab → Report a vulnerability); do not post secrets or exploit details publicly.

内部存储非预期写入、校验绕过、不安全归档处理或日志／生成文件泄漏属于安全问题。通过 GitHub 私密漏洞报告提交（仓库 Security 页签 → Report a vulnerability），不要公开秘密或利用细节。

Describe the affected commit, stable error and sanitized reproduction. Never attach a seed, credentials, stock/vendor image, device identity or private owner data. The installer writes SD cards; Recovery flashing is an owner operation; U-Boot alone has the narrowly checked `misc` command-clearing exception. Scope and evidence are documented in [architecture](docs/architecture.md).

说明受影响提交、稳定错误和脱敏复现，不附种子、凭据、原厂／厂商镜像、设备身份或私有输入。安装器写 SD 卡，Recovery 刷写由所有者执行；U-Boot 仅有受限清理 `misc` 命令的例外，范围与证据见[体系结构](docs/architecture.md)。
