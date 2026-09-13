# OpenCode Buddy 中文使用文档

面向已经拥有一台 M5Stack StickS3、希望在 OpenCode 里用上实体宠物 / 审批设备的用户。

> OpenCode Buddy 改编自 [Claude Desktop Buddy](https://github.com/anthropics/claude-desktop-buddy)
> 与 [CodeBuddy](https://github.com/CharlexH/CodeBuddy)，把主机侧从 Codex 换成了 OpenCode。

---

## 1. 它是什么

OpenCode Buddy 由三部分组成：

| 组件 | 位置 | 作用 |
| --- | --- | --- |
| 设备固件 | StickS3（ESP32-S3） | 广播 `OpenCode-XXXX`，显示宠物、状态、审批、设置 |
| `opencode-buddy` agent | Mac 后台（launchd） | 独占蓝牙连接，接收 OpenCode 事件并推送快照到设备 |
| OpenCode 插件 | `~/.config/opencode/plugins/opencode-buddy.js` | 在 OpenCode 进程内转发事件、接管 `permission.ask` |

数据流：`OpenCode` →（插件，unix socket）→ `opencode-buddy agent` →（BLE NUS）→ `StickS3`。
审批方向相反：设备按 A/B → agent → 插件 → OpenCode。

---

## 2. 前置要求

- macOS（目前仅支持 macOS）
- Python 3.9+
- 一台已通过 USB 连接的 M5Stack StickS3
- 仅当需要**从源码编译固件**时才需要 PlatformIO
  （`~/Library/Python/3.9/bin/pio` 或 `pip install platformio`）

---

## 3. 首次安装

### 3.1 烧录固件（USB）

```bash
git clone https://github.com/nekoinosaka/OpenCodeBuddy.git
cd OpenCodeBuddy

# 生成 OTA 信任材料（编译固件的 pre-script 需要）
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
PYTHONPATH=src .venv/bin/python scripts/generate-ota-trust.py

# 编译并烧录
cd firmware
~/Library/Python/3.9/bin/pio run -t upload
cd ..
```

> **重要**：ESP32-S3 使用内置 USB-JTAG 烧录，已在 `firmware/platformio.ini` 里设置
> `upload_protocol = esp-builtin`，因此**不依赖串口**。如果 `pio run -t upload`
> 报 `No serial data received`，先确认这行还在。
>
> 如果 macOS 识别不到 CDC 串口（`/dev/cu.usbmodem*` 不存在），这是正常的——
> 用 `esp-builtin` 这条链路不受影响。

烧录成功后，设备会以 `OpenCode-XXXX` 广播（可在蓝牙列表里看到）。

### 3.2 安装主机侧

```bash
# 仍在仓库根目录
.venv/bin/opencode-buddy repair
```

`repair` 会依次完成：

1. 安装原生蓝牙 helper 到 `~/.opencode-buddy/helper/`
2. 安装 OpenCode 插件到 `~/.config/opencode/plugins/opencode-buddy.js`
3. 配对 `OpenCode-*` 设备并同步时间
4. 安装并加载 launchd 服务 `com.opencodebuddy.agent`

看到 `OpenCode Buddy is ready.` 即成功。

### 3.3 重启 OpenCode

插件在 OpenCode **启动时**加载，所以安装完必须重启 OpenCode：

```bash
# 退出所有 opencode 进程后重新运行
opencode
```

之后照常使用即可，不需要任何 shim / wrapper。

---

## 4. 日常使用

- 平时正常用 `opencode`。
- 设备进入 `busy` 表示有会话在跑；回到 `idle` 表示空闲。
- 有工具调用需要审批时：
  - 设备屏幕显示审批提示（`attention` 状态，LED 闪烁）
  - 按 **A 批准一次**，**长按 A 始终允许（always，让 OpenCode 记住规则）**，按 **B 拒绝**
  - 设备不在线时，60 秒后自动回退到 OpenCode 原生界面（不会卡死）
- OpenCode 的 `question` 工具（多选项提问）也会投到设备：
  - **B** 切换选项（当前项以 `>` 高亮），**A** 确认，**长按 B** 拒绝/取消
  - 多题会依次弹出
- 完成一次 turn 后设备会响一声（可去设置关闭）。

### 按键

| 按键 | 常规界面 | 宠物界面 | 信息界面 | 审批界面 | 提问界面 |
| --- | --- | --- | --- | --- | --- |
| A（正面） | 下一页 | 下一页 | 下一页 | **批准** | **确认** |
| B（右侧） | 滚动记录 | 下一页 | 下一页 | **拒绝** | **下一项** |
| 长按 A | 菜单 | 菜单 | 菜单 | **始终允许** | 菜单 |
| 长按 B | — | — | — | — | **拒绝** |
| Power（左，短按） | 熄屏/亮屏 | | | | |
| Power（左，约 6s） | 强制关机 | | | | |
| 摇一摇 | dizzy | | | | — |
| 正面朝下 | nap（回能） | | | | |

### 会话状态与宠物状态

| 状态 | 触发 |
| --- | --- |
| `sleep` | 未连接 |
| `idle` | 已连接、无紧急事件 |
| `busy` | 有会话在运行 |
| `attention` | 有待审批 |
| `celebrate` | 每 50K tokens 升级 / 周五彩蛋 |
| `dizzy` | 摇动设备 |
| `heart` | 5 秒内完成批准 |

---

## 5. 命令参考

公开命令：

```bash
opencode-buddy              # 未初始化则执行安装，已初始化则打印状态
opencode-buddy doctor       # 诊断（加 --json 输出机器可读结果）
opencode-buddy repair       # 修复 / 补全安装
opencode-buddy uninstall    # 卸载（--yes 跳过确认）
opencode-buddy firmware update [--firmware <app.bin>]   # 无线固件更新
```

进阶（隐藏）命令：

```bash
opencode-buddy pair                     # 只配对并同步时间
opencode-buddy status                   # 打印 agent 实时状态（JSON）
opencode-buddy sessions                 # 打印当前会话列表（JSON）
opencode-buddy install-opencode-plugin  # 只安装/更新 OpenCode 插件
opencode-buddy service-install|service-uninstall|service-status
opencode-buddy agent                    # 前台运行后台 agent（调试用）
```

---

## 6. 配置与环境变量

| 变量 | 作用 |
| --- | --- |
| `OPENCODE_SERVER_URL` | 指定 OpenCode server 地址（默认 `http://127.0.0.1:4096`）。插件会自动把 TUI 的 server URL 交给 agent；独立 `opencode serve` 时可用它覆盖 |
| `OPENCODE_SERVER_PASSWORD` / `OPENCODE_SERVER_USERNAME` | 当 server 开启 Basic Auth 时使用 |
| `OPENCODE_BUDDY_AGENT_SOCKET` | 覆盖 agent 的 unix socket 路径（默认 `~/.opencode-buddy/agent.sock`） |
| `OPENCODE_BUDDY_BLE_BACKEND` | `native`（默认，macOS 原生 helper）或 `bleak`（跨平台回退，需装 `[ble]` extra） |
| `OPENCODE_BUDDY_BLE_HELPER_APP` | 覆盖 helper `.app` 路径 |
| `OPENCODE_BUDDY_BLE_HELPER_DEBUG_WINDOW` | 设为 `1` 打开 helper 事件日志窗口，排查蓝牙问题 |

数据目录：`~/.opencode-buddy/`

```
state.json          # 配对信息、快照、统计
agent.sock          # 插件 ←→ agent 的 unix socket
logs/               # launchd 日志
helper/             # 原生 BLE helper
firmware/           # OTA 用的 app 镜像
ota/                # OTA 信任材料（私钥 + 公钥）
```

---

## 7. 审批机制说明

- OpenCode 的 `permission.ask` 有以下结果：`allow`（本次允许）、`deny`（拒绝）、`ask`（交给原生界面）。
- 设备当前只能表达两种：**A = 本次允许**，**B = 拒绝**。
- 若沿用 OpenCode 原生的 "always（记住该规则）"，需要走 server REST 接口
  `POST /session/:id/permissions/:permissionID`（body `{response:"always"}`）；
  主机侧已支持该通路，但需要一个能表达 "always" 的设备手势，当前固件还没有。
- 插件等待设备决策最长 60 秒；超时或设备离线则回退 `ask`，OpenCode 原生界面照常弹出。

---

## 8. 会话状态与用量

- 设备上的会话状态、token 计数、最近记录来自**插件的实时事件**（`session.*` / `message.*`）。
- 可选的只读会话 watcher 能额外读取 `GET /session`，但**仅当**用 `OPENCODE_SERVER_URL`
  指向一个独立的 `opencode serve` 实例时才有效；OpenCode TUI 自带的 server 不通过 HTTP
  对外可达，所以默认不会启用。
- OpenCode 没有账号级 rate-limit API，所以**没有** 5 小时 / 7 天额度表；
  设备端会省略该字段。

---

## 9. 无线固件更新（OTA）

前提：设备已刷入支持 OTA 的完整固件，且已在设备设置里配置 Wi-Fi。

```bash
opencode-buddy firmware update
```

- agent 会用 `~/.opencode-buddy/ota` 下已固定的信任材料签一份一次性清单，
  通过短时本地 HTTPS 提供 app-only 镜像，并等待设备 A 键确认。
- 提交启动分区前按 B 或 Ctrl-C 可取消。
- 开发构建可显式指定镜像：`opencode-buddy firmware update --firmware firmware/.pio/build/m5stack-sticks3/firmware.bin`。

> 仓库里自带的 `src/opencode_buddy/firmware/opencode-buddy-sticks3-app.bin` 是上游遗留的
> 预编译镜像。改过固件源码后，若要继续用 OTA，请重新编译并覆盖它。

---

## 10. 故障排查

**设备扫描不到 / 没有 `/dev/cu.usbmodem`**
- 用 `esp-builtin`（见 3.1）烧录，不依赖串口。
- 确认设备已上电、屏幕亮；换数据线、直插 Mac 而非 hub。
- 用 `OPENCODE_BUDDY_BLE_HELPER_DEBUG_WINDOW=1` 打开 helper 日志窗口观察扫描事件。

**`opencode-buddy doctor` 报 agent 反复退出**
- 看 `~/.opencode-buddy/logs/com.opencodebuddy.agent.stderr.log`。
- 常见原因：Python 环境里没有 `opencode_buddy`。执行 `.venv/bin/pip install -e '.[dev]'`
  后 `opencode-buddy repair`。

**审批不弹到设备**
- 确认已安装插件并**重启过 OpenCode**。
- `opencode-buddy doctor` 检查 `OpenCode plugin` 与 `Agent: running`。
- 设备离线时会自动回退原生界面，这是预期行为。

**蓝牙权限**
- 首次连接 macOS 会弹蓝牙权限，必须允许；该弹窗不能跳过。

**卸载**

```bash
opencode-buddy uninstall
```

会移除 launchd 服务、插件文件与 `~/.opencode-buddy`。

---

## 11. 开发与测试

```bash
.venv/bin/pytest -q            # 主机侧测试
cd firmware && pio run         # 固件编译
cd firmware && pio test -e native   # 固件逻辑测试（如已配置 native 环境）
```

---

## 12. 来源与许可

- 改编自 [Claude Desktop Buddy](https://github.com/anthropics/claude-desktop-buddy)（Anthropic）
  与 [CodeBuddy](https://github.com/CharlexH/CodeBuddy)。
- 本仓库为其 OpenCode 适配 fork。
