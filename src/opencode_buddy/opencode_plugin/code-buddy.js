import { connect } from "node:net"
import { homedir } from "node:os"
import { join } from "node:path"

const SOCKET_PATH =
  process.env.CODE_BUDDY_AGENT_SOCKET || join(homedir(), ".code-buddy", "agent.sock")

const FORWARDED_EVENTS = new Set([
  "session.created",
  "session.updated",
  "session.status",
  "session.idle",
  "session.error",
  "message.updated",
  "message.part.updated",
  "permission.updated",
  "permission.replied",
])

function request(payload, timeoutMs) {
  return new Promise((resolve) => {
    let settled = false
    let buffer = ""
    const socket = connect(SOCKET_PATH)
    const finish = (value) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      try {
        socket.destroy()
      } catch {}
      resolve(value)
    }
    const timer = setTimeout(() => finish(null), timeoutMs)
    socket.on("connect", () => {
      try {
        socket.write(JSON.stringify(payload) + "\n")
      } catch {
        finish(null)
      }
    })
    socket.on("data", (chunk) => {
      buffer += chunk.toString("utf8")
      const newline = buffer.indexOf("\n")
      if (newline >= 0) {
        try {
          finish(JSON.parse(buffer.slice(0, newline)))
        } catch {
          finish(null)
        }
      }
    })
    socket.on("error", () => finish(null))
    socket.on("close", () => finish(null))
  })
}

function worthForwarding(event) {
  if (!event || typeof event.type !== "string") return false
  if (!FORWARDED_EVENTS.has(event.type)) return false
  if (event.type === "message.part.updated") {
    const part = event.properties?.part
    if (!part) return false
    if (part.type === "text") {
      return Boolean(part.time?.end) || !event.properties?.delta
    }
    if (part.type === "tool") return part.state?.status === "running"
    return false
  }
  return true
}

function decisionToReply(decision) {
  if (decision === "deny") return "reject"
  if (decision === "always") return "always"
  if (decision === "once") return "once"
  return null
}

export const CodeBuddyBridge = async ({ client, serverUrl, directory }) => {
  const log = async (level, message, extra) => {
    try {
      await client?.app?.log?.({
        body: { service: "code-buddy", level, message, extra },
      })
    } catch {}
  }

  const replyToOpenCode = async (permission, decision, directoryHint) => {
    const reply = decisionToReply(decision)
    if (!reply || !permission?.id) return null
    const dir = directoryHint || directory || undefined
    const attempts = []
    if (typeof client?.postSessionIdPermissionsPermissionId === "function") {
      attempts.push([
        "postSessionIdPermissionsPermissionId",
        () =>
          client.postSessionIdPermissionsPermissionId({
            path: { id: permission.sessionID, permissionID: permission.id },
            body: { response: reply },
          }),
      ])
    }
    if (typeof client?.postSessionByIdPermissionsByPermissionId === "function") {
      attempts.push([
        "postSessionByIdPermissionsByPermissionId",
        () =>
          client.postSessionByIdPermissionsByPermissionId({
            path: { id: permission.sessionID, permissionID: permission.id },
            body: { response: reply },
          }),
      ])
    }
    if (typeof client?.permission?.respond === "function") {
      attempts.push([
        "permission.respond",
        () =>
          client.permission.respond({
            sessionID: permission.sessionID,
            permissionID: permission.id,
            response: reply,
            directory: dir,
          }),
      ])
    }
    if (typeof client?.permission?.reply === "function") {
      attempts.push([
        "permission.reply",
        () => client.permission.reply({ requestID: permission.id, reply, directory: dir }),
      ])
    }
    if (attempts.length === 0) {
      await log("warn", "no permission reply method on client", {
        permission: Object.keys(client?.permission ?? {}).sort().join(","),
      })
      return null
    }
    for (const [name, attempt] of attempts) {
      try {
        const result = await attempt()
        if (result && typeof result === "object" && result.error) {
          await log("warn", "permission reply rejected", { method: name, error: String(result.error) })
          continue
        }
        return name
      } catch (error) {
        await log("warn", "permission reply failed", { method: name, error: String(error) })
      }
    }
    return null
  }

  const permissionQueue = []
  let drainingPermissions = false

  const drainPermissions = async () => {
    if (drainingPermissions) return
    drainingPermissions = true
    try {
      while (permissionQueue.length > 0) {
        const permission = permissionQueue.shift()
        const response = await request(
          { cmd: "permission_ask", permission },
          65000,
        )
        const method = await replyToOpenCode(
          permission,
          response?.decision,
          response?.directory,
        )
        await log("info", "permission decision", {
          id: permission.id,
          decision: response?.decision ?? "ask",
          delivered: method != null,
          method: method || "",
        })
      }
    } finally {
      drainingPermissions = false
    }
  }

  await request(
    {
      cmd: "hello",
      serverUrl: serverUrl ? String(serverUrl) : "",
      directory: directory || "",
    },
    2000,
  )
  await log("info", "Code Buddy bridge plugin initialized", { socket: SOCKET_PATH })
  try {
    await log("info", "Code Buddy client surface", {
      top: Object.keys(client ?? {}).sort().join(","),
      permission: Object.keys(client?.permission ?? {}).sort().join(","),
    })
  } catch {}

  return {
    event: async ({ event }) => {
      if (event?.type === "permission.asked") {
        const permission = event.properties || {}
        void log("info", "permission.asked queued", {
          id: permission.id,
          tool: permission.type,
        })
        permissionQueue.push(permission)
        void drainPermissions()
        return
      }
      if (!worthForwarding(event)) return
      void request(
        { cmd: "notify", event, serverUrl: serverUrl ? String(serverUrl) : "" },
        2000,
      )
    },
  }
}
