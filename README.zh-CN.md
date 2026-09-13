<p align="center">
  <a href="./README.md">
    <img alt="English" src="https://img.shields.io/badge/English-EAEAEA?style=for-the-badge&labelColor=EAEAEA&color=111111" />
  </a>
  <a href="./README.zh-CN.md">
    <img alt="简体中文" src="https://img.shields.io/badge/简体中文-111111?style=for-the-badge" />
  </a>
</p>

<p align="center">
  <img src="screenshots/cover.webp" alt="Code Buddy cover" width="100%" />
</p>

<h1 align="center">Code Buddy</h1>

<p align="center">
  一个基于 StickS3 的 <a href="https://opencode.ai">OpenCode</a> 硬件伙伴，改编自
  <a href="https://github.com/anthropics/claude-desktop-buddy">Claude Desktop Buddy</a>
  和 <a href="https://github.com/CharlexH/CodeBuddy">CodeBuddy</a>。
</p>

<p align="center">
  给设备刷一次固件，在 macOS 上运行一次 <code>code-buddy</code>，之后照常使用 <code>opencode</code>，审批提示和会话状态就会转移到独立硬件上。
</p>

> 如果你想自己做硬件客户端，可以看 [firmware/REFERENCE.md](firmware/REFERENCE.md) 里的 BLE 协议和 JSON 负载定义。

## 项目包含什么

- 一个 macOS 主机桥接层，负责与 StickS3 配对、同步时间、安装原生 BLE helper，并安装 OpenCode 插件。
- 一个 OpenCode 插件，负责转发实时会话事件，并把审批请求路由到设备。
- 一套 StickS3 固件，包含状态页、审批页、设置页和离线页。
- 一套尽量不打扰日常工作的流程：先跑一次 `code-buddy`，之后直接用 `opencode`。

## 工作原理

Code Buddy 由三部分组成：

1. **设备固件**：以 `OpenCode-XXXX` 广播 Nordic UART Service，渲染宠物、状态和审批界面。
2. **`code-buddy` agent**：作为 launchd 服务常驻，独占蓝牙连接，并持续把快照推送到设备。
3. **OpenCode 插件**（`~/.config/opencode/plugins/code-buddy.js`）：运行在 OpenCode 进程内。启动时把 server URL 交给 agent，转发总线事件（`session.status`、`message.updated`、`message.part.updated`、`permission.*`），并实现 `permission.ask` hook，让设备可以批准或拒绝。

有待审批请求时，StickS3 会显示提示：**A** 批准一次，**B** 拒绝。设备不在线时，会自动回退到 OpenCode 原生界面（agent 最多等待 60 秒后返回 `ask`）。

会话历史和 token/费用统计来自 OpenCode server（`GET /session`）。插件会自动提供 server URL，因此只要 agent 能访问该 server，历史就能正常工作。也可以用 `OPENCODE_SERVER_URL` 指向一个独立的 `opencode serve` 实例。

## 快速开始

### 1. 给 StickS3 刷机

从 Releases 下载 `code-buddy-sticks3-v{version}-full.bin`，然后写入到 `0x0`。

<details>
<summary>刷写命令</summary>

兜底方式：

```bash
esptool --chip esp32s3 --port /dev/cu.usbmodem101 --baud 460800 write_flash 0x0 code-buddy-sticks3-v0.1.45-full.bin
```

开发者本地生成 release 镜像：

```bash
./scripts/build-firmware-release.sh
```

脚本会生成两种不同的文件：`*-full.bin` 是 USB 恢复/首次刷写镜像，`*-app.bin` 是 OTA 使用的应用镜像。不要把合并后的 `full.bin` 传给 OTA 命令。
</details>

### 2. 在 macOS 上安装

从源码安装：

```bash
git clone https://github.com/nekoinosaka/CodeBuddy.git
cd CodeBuddy
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/code-buddy
```

首次运行时，Code Buddy 会：

- 安装原生蓝牙 helper
- 与 `OpenCode-*` 设备配对
- 同步设备时间
- 安装 launchd agent
- 把 OpenCode 插件安装到 `~/.config/opencode/plugins/code-buddy.js`

仅主机侧的更新在协议仍兼容时不需要重新刷机；涉及屏幕、声音或 OTA 运行时的功能，需要配套版本的设备固件。

### 3. 正常使用

```bash
opencode
```

安装完成后请重启 OpenCode，让它加载插件。此后你可以保持原来的使用方式，Code Buddy 会在后台维持桥接，并把审批提示显示到 StickS3 上。

会话事件由插件转发，因此**不需要任何 shim 或 wrapper**，像平时一样运行 `opencode` 即可。

### 无线固件更新

首次通过 USB 刷入支持 OTA 的完整固件，并在设备设置中配置 Wi-Fi 后，打开 **Settings > OTA update**，然后运行：

```bash
code-buddy firmware update
```

开发固件可以显式指定 app-only 镜像：

```bash
code-buddy firmware update --firmware firmware/.pio/build/m5stack-sticks3/firmware.bin
```

主机代理会作为唯一的蓝牙所有者，为一次性不可变清单签名，通过短时本地 HTTPS 提供 app-only 镜像，并等待设备 A 键确认。提交启动分区前按 B 或 Ctrl-C 可以取消。

正常使用时，原生 BLE helper 会作为 macOS 后台 agent 运行，所以重连过程不应该再打开 helper 窗口或抢走焦点。macOS 首次蓝牙权限确认仍可能出现，这是系统权限弹窗，不能跳过。如果需要调试 helper 事件，可以用 `CODE_BUDDY_BLE_HELPER_DEBUG_WINDOW=1` 打开事件日志窗口。

## 按键说明

|                         | 常规界面             | 宠物界面    | 信息界面    | 审批界面    |
| ----------------------- | -------------------- | ----------- | ----------- | ----------- |
| **A**（正面）           | 下一个页面           | 下一个页面  | 下一个页面  | **批准**    |
| **B**（右侧）           | 滚动 transcript      | 下一页      | 下一页      | **拒绝**    |
| **长按 A**              | 菜单                 | 菜单        | 菜单        | 菜单        |
| **Power**（左侧，短按） | 熄屏 / 亮屏          |             |             |             |
| **Power**（左侧，约 6s）| 强制关机             |             |             |             |
| **摇一摇**              | dizzy                |             |             | —           |
| **正面朝下**            | nap（恢复能量）      |             |             |             |

屏幕在 30 秒无操作后会自动熄灭；如果有待处理审批，会保持常亮。按任意键都可以唤醒。

## Buddy 状态

| 状态        | 触发条件                  | 表现                        |
| ----------- | ------------------------- | --------------------------- |
| `sleep`     | bridge 未连接             | 闭眼，慢呼吸                |
| `idle`      | 已连接，但没有紧急事件    | 眨眼，左右看                |
| `busy`      | 会话正在活跃运行          | 出汗，忙碌                  |
| `attention` | 有待审批请求              | 警觉，**LED 闪烁**          |
| `celebrate` | 升级（每 50K tokens）、周五时钟彩蛋 | 撒花，跳动                  |
| `dizzy`     | 你摇了设备                | 蚊香眼，摇晃                |
| `heart`     | 在 5 秒内完成批准         | 飘心                        |

当 StickS3 处于 USB 供电、RTC 保留着有效时间、且没有运行中或待审批会话时，会进入充电时钟状态。设备只需成功同步过一次时间；正常重启后即使 Mac 暂时离线，也会继续使用 RTC 时间进入时钟。RTC 丢失供电并回到 2000-01-01 时仍会等待下一次可信同步。周五 15:00 到午夜之间，宠物会偶尔庆祝：每 12 秒循环里大约庆祝 4 秒。

<details>
<summary><strong>角色和自定义素材包</strong></summary>

固件内置了十八个 ASCII 宠物，每个宠物都包含七种动画：`sleep`、`idle`、`busy`、`attention`、`celebrate`、`dizzy` 和 `heart`。

在设备上进入 `menu -> next pet` 可以轮换角色。选择会保存在设备存储里，重启后仍会保留。

如果你想换成自定义 GIF 角色，可以准备一个包含 `manifest.json` 和对应七种状态 GIF 的角色包。GIF 建议宽度为 96px：

```json
{
  "name": "bufo",
  "colors": {
    "body": "#6B8E23",
    "bg": "#000000",
    "text": "#FFFFFF",
    "textDim": "#808080",
    "ink": "#000000"
  },
  "states": {
    "sleep": "sleep.gif",
    "idle": ["idle_0.gif", "idle_1.gif", "idle_2.gif"],
    "busy": "busy.gif",
    "attention": "attention.gif",
    "celebrate": "celebrate.gif",
    "dizzy": "dizzy.gif",
    "heart": "heart.gif"
  }
}
```

说明：

- `idle` 可以是一张 GIF，也可以是一组 GIF 数组。
- 高度控制在约 140px 以内会比较适合 StickS3 屏幕。
- 可以参考 [firmware/characters/bufo/](firmware/characters/bufo/) 的现成示例。
- 资源处理和刷写工具在 [firmware/tools/prep_character.py](firmware/tools/prep_character.py) 和 [firmware/tools/flash_character.py](firmware/tools/flash_character.py)。
</details>

## 恢复命令

```bash
code-buddy doctor
code-buddy repair
code-buddy uninstall
```

`doctor` 会告诉你哪里出了问题、为什么会这样，以及下一步应该怎么做。

<details>
<summary><strong>从源码运行</strong></summary>

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/code-buddy
```

验证命令：

- 主机侧测试：`.venv/bin/pytest -q`
- 固件构建：`cd firmware && pio run`
</details>
