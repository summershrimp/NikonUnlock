# Nikon Z6 / Z7 / Z6 II / Z7 II 解锁工具

[English](README.md)

> 当前本地分支新增 Z6 II/Z7 II C 1.70 的 macOS 实验传输层，见
> [MACOS.md](MACOS.md)。该路径尚未经相机实机验证；下文为原版 Windows 说明。

Windows 独立工具包，为一代 Nikon Z6/Z7 提供 **HDMI RAW 解锁**和**完整多语言列表解锁**，并为 Z6 II/Z7 II 提供 **HDMI RAW 实验解锁脚本**。两个功能分别使用独立脚本，**不提供同时解锁的命令或参数**。无需刷写固件镜像。二代多语言解锁暂未收录：项目目前只有固件分析记录，尚无对应的可执行解锁工具。

## 兼容范围

| 机型 | 固件 | 验证情况 |
| --- | --- | --- |
| Z6 | C 3.80 | 原项目已在一台实机分别验证 RAW、多语言操作及正常关机再开机后的持久化 |
| Z7 | C 3.80 | 实验支持：固件分析确认 KEY 校验表相同，但完整写入流程及持久化尚未经实机验证；需要 `--experimental-z7` |
| Z6 II / Z7 II | C 1.70 | 仅 HDMI RAW，使用 `unlock_hdmi_raw_ii.py`；已核对固件常量并完成离线模拟，尚未经二代实机、重启持久化和外录验证 |

请按相机代际选择脚本：各脚本会拒绝另一代相机、其他固件版本及其他机型。KEY 文件相同并不等于所有配置行为相同。本发布版的打包和命令拆分已做离线检查，整理发布包期间未写入相机；外录设备兼容性及录制画质不在本次验证范围内。

## 文件说明

- `unlock_hdmi_raw.py`：一代，仅解锁 HDMI RAW。
- `unlock_languages.py`：一代，仅开放完整语言列表，保留现有区域字段。
- `unlock_hdmi_raw_ii.py`：Z6 II / Z7 II C 1.70 专用，仅实验性解锁 HDMI RAW。
- `unlock_core.py`、`unlock_cli.py`、`wpd_transport.py`：共用实现。
- `keys/HDMIRAW.KEY`、`keys/SRF_FUNC.KEY`：相机功能授权二进制文件，各为 32 字节，适用于已分析的一代 Z6/Z7 固件。这些文件不是软件开源许可证，也不是尼康官方付费升级凭证。
- `keys/z6ii_z7ii/HDMIARAW.KEY`、`keys/z6ii_z7ii/SRF_FUNC.KEY`：二代专用，各为 32 字节。**两份文件内容均与一代不同，不可混用。**
- `requirements.txt`、`test_*.py`、`SHA256SUMS.txt`：依赖版本、离线测试和发布文件校验值。

## 准备

使用 Windows，**二代脚本要求 Python 3.11 或更高版本**（一代脚本也支持 Python 3.10）。在本文件夹打开终端：

```powershell
python -m pip install -r requirements.txt
```

一代解锁 HDMI RAW 时，将 **`keys/HDMIRAW.KEY` 和 `keys/SRF_FUNC.KEY`** 复制到相机存储卡根目录。Z6 II/Z7 II 则只使用 **`keys/z6ii_z7ii/`** 内的两份文件。保持大写文件名及二进制内容不变，不要只复制外层文件夹，也不要改为 `.txt`。仅放入文件不会自动激活 RAW。一代多语言单独解锁不会调用依赖 KEY 的 RAW 命令，不需要这两个文件。

插卡，确保电池电量充足，相机开机后通过 USB 连接电脑，关闭占用相机的软件。工具使用 Windows WPD，无需替换 USB 驱动。执行结束前保持 USB 及供电连接。

## Z6 / Z7：单独解锁 HDMI RAW

先离线预览，再实际执行：

```powershell
python unlock_hdmi_raw.py unlock
python unlock_hdmi_raw.py unlock --execute
```

断开 USB，正常关机再开机，检查 HDMI RAW 菜单，重新连接 USB 后验证：

```powershell
python unlock_hdmi_raw.py verify
```

此验证仅检查 RAW，不要求多语言已经开放。验证时仍需将两个 KEY 文件保留在卡上。

## Z6 / Z7：单独解锁完整语言列表

```powershell
python unlock_languages.py unlock
python unlock_languages.py unlock --execute
```

断开 USB，正常关机再开机，检查语言菜单，重新连接 USB 后验证：

```powershell
python unlock_languages.py verify
```

此操作不会激活 RAW。它开放固件已有的完整语言列表（含简体中文），不安装翻译，也不会自动切换当前菜单语言。

Z7 为实验支持，每条访问相机的命令需追加 `--experimental-z7`，例如：

```powershell
python unlock_languages.py unlock --execute --experimental-z7
```

## Z6 II / Z7 II：实验性单独解锁 HDMI RAW

要求 **C 1.70**、Python 3.11+ 和二代专用 KEY。文件名为 **HDMIARAW.KEY**（比一代多一个 `A`），`SRF_FUNC.KEY` 内容也不同。将 `keys/z6ii_z7ii/` 内两份文件复制到相机存储卡根目录。固件使用内部 `A:\` 路径，不是电脑盘符；双卡槽映射尚未经实机确认。若报 `0x200F`，请先解决卡上文件位置问题。

```powershell
python unlock_hdmi_raw_ii.py unlock
python unlock_hdmi_raw_ii.py status
python unlock_hdmi_raw_ii.py unlock --execute
```

`unlock` 不加 `--execute` 时只显示离线计划。`status` 仅读取机型信息和普通模式授权状态，不需要 KEY，也不能证明两个 RAW 状态或外录已经正常。解锁时会核对机型及固件、备份配置，并在任何授权写入前验证卡上两份 KEY；随后激活 RAW、检查状态及可见配置、提交一次、读回、退出服务模式并检查普通模式授权。不会发送语言、区域修改或重置命令。

成功后断开 USB，正常关机再开机，检查 HDMI RAW 菜单，重新连接 USB 并执行：

```powershell
python unlock_hdmi_raw_ii.py verify --after-reboot
```

保留卡上两份 KEY。`--after-reboot` 仅记录你已手动重启的声明，不会自动证明发生了断电或外录成功。`verify`、`snapshot` 会进入/退出服务模式，但不会发送授权或提交命令。此二代专用实验脚本不使用 `--experimental-z7` 参数。

其他命令与选项：

```powershell
python unlock_hdmi_raw_ii.py list-devices
python unlock_hdmi_raw_ii.py snapshot
python unlock_hdmi_raw_ii.py prepare-keys
```

`prepare-keys` 可补齐本地二代 KEY，遇到内容不同的已有文件会拒绝覆盖。`--keys-dir "文件夹"` 指定本地 KEY 目录，本地校验不能替代复制文件到相机存储卡。连接多台 Nikon 时使用 `--device "完整设备ID"`；`--backup-dir "路径/新文件夹"` 可指定新的输出目录。

默认输出为 `backups/z6ii_z7ii/<UTC时间>/`，包括 `snapshot.json`、`item8_masked.bin`、请求响应日志 `transactions.jsonl` 和记录授权/提交尝试、确认及错误的 `result.json`。出现额外配置变化时会保存 `unexpected_*.bin/json`。二代读回屏蔽的是**偏移 4、5**，并非完整可恢复镜像，不能整块写回。日志含相机身份信息。

失败时脚本尝试退出服务模式，不自动重试或回滚写入。可能已留下部分 RAM 修改，提交响应丢失也不等于提交没有发生；请保留日志并检查失败阶段后再决定是否重试。固件证据测试保留在原项目，本发布包的离线测试不依赖专有固件模块。

## Z6 / Z7：备份与恢复

每次需要保存数据的相机操作都会在 `backups/` 下建立新目录。可通过 `--backup-dir "路径/新文件夹"` 指定位置，不覆盖已有目录。备份含相机序列号和设备 ID，对外分享日志前请移除身份信息。

脚本先备份，再暂存所选功能的修改，比较配置和区域字段，通过后执行 F030 保存、读回检查并退出服务模式。命令成功不等于已经证明重启持久化。遇到错误请保留备份和错误输出，先检查原因再决定是否重新执行。

`item8_masked.bin` **不是完整可恢复镜像**：相机读回时屏蔽偏移 4、18，不能整块写回。仅恢复同一机身、同一固件的原语言开关：

```powershell
python unlock_languages.py unlock --restore-language-from "backups/原备份目录/snapshot.json"
python unlock_languages.py unlock --execute --restore-language-from "backups/原备份目录/snapshot.json"
python unlock_languages.py snapshot
```

恢复受限语言列表后，`verify` 会正确提示完整列表未开启；请用 `snapshot` 检查恢复后的开关值。本工具不提供 RAW 关闭命令。

列出已连接设备：

```powershell
python unlock_hdmi_raw.py list-devices
```

连接多个 Nikon 设备时，在所选脚本命令后追加 `--device "完整设备ID"`。`snapshot` 会进入再退出服务模式并保存读回；`verify` 同样会进入再退出。两者均不发送解锁或保存命令，但服务模式切换可能重新加载临时状态。

## 排错与离线测试

`0x200F`：找不到 KEY；`0x2013`：文件读取失败；`0xA001`：KEY 校验或握手失败；`0x2019`：设备忙。请检查存储卡位置、文件名与大小、USB 连接及占用相机的软件。外层 PTP 响应和 Nikon 内层响应都成功才算通过。

```powershell
python -m unittest discover -v
```

测试使用模拟传输，不连接相机。本工具为独立非官方项目，会修改相机持久化配置，请保留生成的备份。
