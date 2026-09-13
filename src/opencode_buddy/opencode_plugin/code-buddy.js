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
  "permission.asked",
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

export const CodeBuddyBridge = async ({ client, serverUrl, directory }) => {
  const log = async (level, message, extra) => {
    try {
      await client?.app?.log?.({
        body: { service: "code-buddy", level, message, extra },
      })
    } catch {}
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

  return {
    event: async ({ event }) => {
      if (!worthForwarding(event)) return
      await request(
        { cmd: "notify", event, serverUrl: serverUrl ? String(serverUrl) : "" },
        2000,
      )
    },
    "permission.ask": async (permission, output) => {
      const response = await request({ cmd: "permission_ask", permission }, 70000)
      const decision = response?.decision
      if (decision === "once") {
        output.status = "allow"
      } else if (decision === "deny") {
        output.status = "deny"
      } else if (decision === "always") {
        let persisted = false
        try {
          const result = await client?.postSessionByIdPermissionsByPermissionId?.({
            path: { id: permission.sessionID, permissionID: permission.id },
            body: { response: "always" },
          })
          persisted = Boolean(result)
        } catch {
          persisted = false
        }
        output.status = persisted ? "ask" : "allow"
      } else {
        output.status = "ask"
      }
    },
  }
}
