# ⚠️ 本机设备说明：已改用 Launcher 管理

这台 M5Stack StickS3 **不再直接运行本仓库固件**。它现在由
[bmorcelli/Launcher](https://github.com/bmorcelli/Launcher)（2.9.1）统一管理，
OpenCodeBuddy 与 Tarot 两个固件都以「app」形式并存，由 Launcher 选择启动。

## 分区现状（8MB flash）

| 分区 | 类型 | 偏移 | 大小 | 内容 |
|---|---|---|---|---|
| `app0` | app / test | `0x010000` | 1344 KB | **Launcher** |
| `openco` | app / ota_0 | `0x170000` | 2112 KB | **OpenCodeBuddy** |
| `littlefs` | data / spiffs | `0x380000` | 1024 KB | Tarot 卡面资源（注意标签是 `littlefs`） |
| `tarot0` | app / ota_1 | `0x480000` | 1792 KB | **Tarot** |

## 🚫 不要做的事

**不要执行 `pio run -t upload` / `pio run -t erase`，也不要用 esptool 从 `0x0` 写整包。**

它们会覆盖 bootloader 与分区表，**直接擦掉 Launcher 和另一个固件**。

## ✅ 怎么切换固件

**冷启动**（电源键长按约 6 秒关机 → 短按开机）→ Launcher 菜单 → 选固件。

> 注意：用 USB 线触发的复位 / `esptool` 复位**看不到** Launcher 菜单（引导器会直接进上次的 app），必须物理冷启动。

StickS3 上的 Launcher 操作：**正面键短按 = 下一个，长按 = 确认；侧面键短按 = 上一个，长按 = 返回。**

## ✅ 怎么更新

1. 只编译 app（不要 upload）：`cd firmware && pio run` → 产出 `.pio/build/m5stack-sticks3/firmware.bin`
2. 通过 Launcher 安装：
   - 串口：`flash firmware <名字> <字节数>`，收到 `READY` 后按 2048 字节一块发送原始字节，每块等一个 `ACK`；结束会打印 `OK flashed, rebooting`
   - 或 Launcher 的 WebUI / SD 卡

## 注意

- **本项目自带的 OTA 失效**：`opencode-buddy firmware update`、签名信任、A/B 回滚都依赖本仓库固定分区表（`otadata`/`ota_0`/`ota_1` 的精确偏移与大小），在 Launcher 的分区布局下不成立，OTA 监督器保持 inert。更新请走 Launcher。
- **当前是 ASCII 角色模式**：固件要一个标签为 `spiffs` 的 LittleFS 分区来放自定义 GIF 角色，但现有数据分区标签是 `littlefs`（给 Tarot 用），所以 OpenCodeBuddy 找不到、退回内置 ASCII 小人（属于正常降级）。
- NVS 由两个固件共享，切换固件时各自的设置键名不同，一般互不干扰。
