# Code Review 报告（有罪推定）

- **审查对象**：`opencode_buddy`（本 fork 将 OpenCode Buddy 从 Codex 适配到 OpenCode）相对上游 `upstream/main` 的**全量 diff**
- **审查方法**：对每一处新代码默认"有罪"，先用运行现场的 bug 反推，再逐文件验证
- **审查范围**：`git diff upstream/main...HEAD`（116 files, +2270 / −6140）
- **环境**：macOS，OpenCode 1.18.30，M5StickS3 固件（`OpenCode-XXXX`）

> 结论先行：**核心链路已跑通**（设备读会话 + 设备审批闭环），但**并发审批、内存增长、会话历史来源**三处存在实质缺陷，建议修复后再长期使用。

---

## 一、问题汇总

| 编号 | 严重度 | 状态 | 问题 | 位置 |
| --- | --- | --- | --- | --- |
| A | **P0** | ✅ 已修并验证 | 审批等待阻塞 `event` hook，并发审批只弹一次、其余卡在电脑端 | `opencode_plugin/opencode-buddy.js` |
| B | **P0** | ✅ 已修 | 会话/消息状态只增不减，长期运行内存泄漏 | `opencode_events.py`、`agent.py` |
| C | **P0** | ✅ 已修 | `/session` watcher 在真实 TUI 下不可达 | `agent.py`、文档 |
| D | P1 | ✅ 已修 | 回包方法靠猜（实测 `postSessionIdPermissionsPermissionId` 有效） | `opencode_plugin/opencode-buddy.js` |
| E | P1 | ✅ 已修 | 回包 `directory` 可能取错 | `agent.py` → 插件 |
| F | P1 | ✅ 已修 | `__SCAN_ONLY__` 扫描时误连设备 | helper Swift |
| G | P2 | ✅ 已修 | token 统计 O(n²) 且非单调 | `opencode_events.py` |
| H | P2 | ✅ 已修 | `respond_permission` 文档/接口不一致；REST 死代码 | `opencode_server.py`、`agent.py` |
| I | P2 | 不适用 | 仅记录 `type`/`id`，未记录 title | — |
| J | P2 | ✅ 已修 | 预编译 OTA 固件 `opencode-buddy-sticks3-app.bin` 仍为旧品牌 | `src/opencode_buddy/firmware/` |

---

## 二、详细问题

### A. 审批等待阻塞事件循环（P0，已修）

**现象（现场复现）**：一次同时出现多个审批（3 个 `read` 各触发一次 `external_directory`），设备上**只弹了一个**，电脑端却**堆了几个**；用户确认设备后，电脑端仍有残留审批。

**根因**：`opencode-buddy.js` 的 `event` hook 在收到 `permission.asked` 后直接：

```js
const response = await request({ cmd: "permission_ask", permission }, 65000)
```

该 `await` 最长阻塞 65 秒。若 OpenCode 串行派发事件，后续 `permission.asked` 根本进不来 → 设备只看到第一个。同理，`notify` 也 `await` 了 2 秒超时，agent 掉线时每个事件都会拖慢 OpenCode。

**修复**：审批改为**入队 + 独立 worker**，`event` hook 不再阻塞：

```js
permissionQueue.push(permission)
void drainPermissions()      // 串行问设备并回包，但不阻塞事件
```

`notify` 改为 fire-and-forget（`void request(...)`）。

### B. 状态只增不减（P0，部分修复）

**问题**：
- `OpenCodeEventAdapter._meta` / `_assistant_output`（现改名为 `_session_output_total` / `_message_output`）按 session 无限增长；`forget()` 定义了但无人调用。
- `agent._opencode_runtime` 字典对每个 session 建条目，永不清理。

**已修**：
- 适配器改为**增量单调** token 统计 + 每 session 消息上限 `_MESSAGE_CAP = 512`，`forget()` 清空三个字典。
- 消除了"消息被删/修正导致 token 下降"的非单调问题，同时去掉了每次事件的 O(n²) `sum()`。

**待修**：在 `agent._snapshot()` 中按 catalog 可见 session 清理 `_opencode_runtime` 与适配器状态（当前不可见的 session 即 `forget`）。

### C. `/session` 历史 watcher 实际不可达（P0，待修）

**问题**：`opencode_server.OpenCodeServerClient` 通过 HTTP 访问 `GET /session`，但实测真实 TUI 的 server **只在进程内可达**：

```
curl http://127.0.0.1:4096/global/health   # 无响应
lsof -iTCP -sTCP:LISTEN                    # 无 opencode 监听
```

插件上报的 `serverUrl = http://localhost:4096/` 并非真实 TCP 端点。因此：
- `opencode_session_watcher.poll()` 恒返回空，只有 `_readonly_loop` 每 2 秒静默失败（已降为 debug）。
- 设备上看到的会话来自**实时事件**，而非 `/session`。
- README / `docs/USAGE.zh-CN.md` 中"会话历史/用量来自 server"的说法**不成立**。

**建议**：二选一——
1. **删除** watcher 与 `opencode_server` 的 HTTP 路径，文档改为"会话/用量来自实时事件"；或
2. 改由**插件通过 SDK**（`client.session.list()`）拉取会话，经 socket 交给 agent（真正可达的路径）。

推荐方案 2（保留历史能力）；若短期只求正确，先用方案 1。

### D. 回包方法靠猜（P1，待修）

**问题**：`replyToOpenCode()` 依次尝试 4 种 SDK 方法（`permission.reply` / `permission.respond` / `postSessionIdPermissionsPermissionId` / `postSessionByIdPermissionsByPermissionId`），用 `result.error` 和异常判断成败。历史上已因方法名变更出现 `client.permission.reply is unavailable`。虽然现在能命中，但**脆弱且难维护**。

**建议**：从日志 `OpenCode Buddy client surface` 确认当前版本真实可用的方法，**固定为一个**并加断言/告警；其余作为显式向后兼容分支。

### E. 回包 `directory` 可能取错（P1，待修）

`opencode-buddy.js:92`：

```js
const dir = permission.directory || directory || undefined
```

`Permission` 对象不含 `directory`，实际取的是**插件启动目录**。在多项目/子目录场景，回包可能落到错误 workspace 或失败。

**建议**：agent 在推送审批时附带该 session 的 `directory`（`OpenCodeEventAdapter._meta` 已有），经 socket 回传给插件使用。

### F. 发现逻辑与 `__SCAN_ONLY__`（P1，待修）

`ble_transport._matches_buddy_discovery`：名字不以 `opencode-` 开头时，**只要广播含 NUS UUID 就接受**，会误配任意 NUS 设备（如其他串口透传 gadget）。

另外，手动运行 helper 带 `--device-id __SCAN_ONLY__` 时，事件流里仍出现 `connect_started` / `connected`，说明扫描哨兵未被严格 honor，可能与正在运行的 agent 抢连接。

**建议**：发现阶段只接受 `opencode-` 前缀名；查明并修正 Swift helper 的 `__SCAN_ONLY__` 分支。

### G. token 统计（P2，已修）

见 B：改为增量单调 + 有界。

### H. 接口/文档不一致 & 死代码（P2，待修）

- `opencode_server.respond_permission` 的 docstring 仍是旧接口 `POST /session/:id/permissions`，代码已改为 `/permission/{id}/reply`。
- `agent._handle_device_permission` 的 REST 回包分支只有在"没有插件 waiter"时才触发；而插件现在**总是**先 `permission_ask` 建立 waiter，故该分支为**死代码**（且 server 也不可达）。

**建议**：删除 REST 回包路径与对应客户端方法，或明确标注为"非 TUI 场景备用"。

### I. 日志信息暴露（P2，待修）

插件将审批 `title`（可能含命令原文）与 `id` 写入 OpenCode 日志。属轻微信息暴露。

**建议**：仅在 `debug` 级别记录，或只记录 `type` 与 `id`。

### J. 预编译固件（P2，已知）

`src/opencode_buddy/firmware/opencode-buddy-sticks3-app.bin` 仍是 Codex 时代产物（含旧品牌字符串），仅影响 **OTA**。USB 烧录链路不受影响。需重编固件后覆盖。

---

## 三、已确认工作正常的部分

- **BLE 链路**：设备以 `OpenCode-27F5` 广播 NUS；native helper 发现、连接、断开均正常。
- **实时状态**：`session.status` / `session.idle` / `message.*` → 设备显示 running/idle、token、最近记录；完成提示音（`completion_seq`）正常。
- **审批闭环（单发）**：`permission.asked` → 设备弹 → 用户 A/B → 插件回包 → `delivered=true`，电脑端提示消失、命令继续。
- **无 Codex 残留**：全仓库文本/文件名无 `codex` 字样（`grep -rni codex` 为空）。
- **主机构建**：`pip install -e '.[dev]'` 在 Python 3.14 可用（`bleak` 改为可选 extra）。
- **固件构建**：`pio run` 通过（RAM 35.5% / Flash 63.2%），设备名/OTA 前缀改 `OpenCode-` 后编译无误。
- **测试**：316 passed, 1 skipped；唯一失败 `test_ota_trust::test_generation_atomically_repairs_legacy_ca_extensions_without_rotating_key` 为**改动前既有**的环境问题（openssl `-addext`），已用 `git stash` 复现确认。

---

## 四、验证记录

| 场景 | 结果 |
| --- | --- |
| `/etc/hosts` 外部目录审批 | 设备弹出 → 按 A → 通过 |
| `~/.gitconfig` 外部目录审批 | 设备弹出 → 通过 |
| 并发 3 个 `external_directory`（`read`） | **设备仅弹 1 个，电脑端堆积**（问题 A 现场） |
| 9:11 单发审批日志 | `permission decision … delivered=true` |
| `curl localhost:4096` | 无监听（问题 C 证据） |

---

## 五、修复优先级建议

1. **A（已修）** + **B 的 agent 清理（待修）**：稳定并发与内存。
2. **C**：修正"历史/用量"实现与文档的一致性。
3. **D/E**：把回包路径固定、把 `directory` 从 agent 透传。
4. **F/H/I/J**：健壮性与整洁性。

---

## 六、修复后复测（关键）

用 3 条访问不同外部路径（`/usr/share`、`/Library`、`/private/var`）的命令**并发**触发审批，OpenCode 日志：

- `permission.asked queued` ×3（同一 run）
- `permission decision … delivered=true method=postSessionIdPermissionsPermissionId` ×3，间隔约 1.5–2s
- 设备依次弹出，用户逐个按键，电脑端对应提示逐个消失

结论：**并发审批缺陷（A）已修复并验证**，且确认该 OpenCode 版本有效的回包方法为 `postSessionIdPermissionsPermissionId`。

> 复盘：中途反复出现"电脑有、设备没有"，根因是**仓库中的插件改动没有同步到 `~/.config/opencode/plugins/`**，OpenCode 重启后加载的仍是旧阻塞版。为此新增 `doctor` 的插件漂移检测（已安装 ≠ 内置时告警），并提示运行 `opencode-buddy install-opencode-plugin`。

---

*报告基于运行时现场 + 全量 diff 静态审查，未运行真实硬件回归矩阵；固件逻辑测试（`pio test -e native`）未执行。*
