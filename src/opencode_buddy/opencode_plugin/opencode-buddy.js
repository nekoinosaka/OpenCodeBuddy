import { readFileSync } from "node:fs"
import { connect } from "node:net"
import { homedir } from "node:os"
import { join } from "node:path"

const SOCKET_PATH =
  process.env.OPENCODE_BUDDY_AGENT_SOCKET || join(homedir(), ".opencode-buddy", "agent.sock")

// On Windows the background agent cannot serve AF_UNIX, so it binds a loopback
// TCP listener and records {host, port} in the endpoint file. Re-read it on
// every request because the port changes whenever the agent restarts.
function connectOptions() {
  if (process.platform !== "win32") return { path: SOCKET_PATH }
  const payload = JSON.parse(readFileSync(SOCKET_PATH, "utf8"))
  return { host: String(payload.host), port: Number(payload.port) }
}

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
  "question.replied",
  "question.rejected",
])

function request(payload, timeoutMs) {
  return new Promise((resolve) => {
    let settled = false
    let buffer = ""
    let socket
    try {
      socket = connect(connectOptions())
    } catch {
      resolve(null)
      return
    }
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

export const OpenCodeBuddyBridge = async ({ client, serverUrl, directory }) => {
  const log = async (level, message, extra) => {
    try {
      await client?.app?.log?.({
        body: { service: "opencode-buddy", level, message, extra },
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
    if (typeof client?._client?.post === "function") {
      attempts.push([
        "low.post session permissions",
        () =>
          client._client.post({
            url: "/session/{id}/permissions/{permissionID}",
            path: { id: permission.sessionID, permissionID: permission.id },
            body: { response: reply },
            ...(dir ? { query: { directory: dir } } : {}),
          }),
      ])
    }
    for (const [name, attempt] of attempts) {
      try {
        const result = await attempt()
        if (result && typeof result === "object" && result.error) {
          let detail
          try {
            detail = JSON.stringify(result.error).slice(0, 300)
          } catch {
            detail = String(result.error)
          }
          await log("warn", "permission reply rejected", { method: name, error: detail })
          continue
        }
        return name
      } catch (error) {
        await log("warn", "permission reply failed", { method: name, error: String(error) })
      }
    }
    return null
  }

  const answered = (result) => !(result && typeof result === "object" && result.error)

  const answerQuestion = async (requestId, answers) => {
    const dir = directory || undefined
    if (typeof client?.question?.reply === "function") {
      try {
        const result = await client.question.reply({
          requestID: requestId,
          answers,
          directory: dir,
        })
        if (answered(result)) return "question.reply"
        await log("warn", "question reply rejected", { error: String(result.error) })
      } catch (error) {
        await log("warn", "question reply failed", { error: String(error) })
      }
    }
    if (typeof client?._client?.post === "function") {
      try {
        const result = await client._client.post({
          url: "/question/{requestID}/reply",
          path: { requestID: requestId },
          body: { answers },
          ...(dir ? { query: { directory: dir } } : {}),
        })
        if (answered(result)) return "question.reply(low)"
        await log("warn", "question reply(low) rejected", { error: String(result.error ?? result) })
      } catch (error) {
        await log("warn", "question reply(low) failed", { error: String(error) })
      }
    }
    return null
  }

  const rejectQuestion = async (requestId) => {
    const dir = directory || undefined
    if (typeof client?.question?.reject === "function") {
      try {
        const result = await client.question.reject({ requestID: requestId, directory: dir })
        if (answered(result)) return "question.reject"
      } catch (error) {
        await log("warn", "question reject failed", { error: String(error) })
      }
    }
    if (typeof client?._client?.post === "function") {
      try {
        const result = await client._client.post({
          url: "/question/{requestID}/reject",
          path: { requestID: requestId },
          ...(dir ? { query: { directory: dir } } : {}),
        })
        if (answered(result)) return "question.reject(low)"
      } catch (error) {
        await log("warn", "question reject(low) failed", { error: String(error) })
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
        const response = await request({ cmd: "permission_ask", permission }, 65000)
        const method = await replyToOpenCode(permission, response?.decision, response?.directory)
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

  const questionQueue = []
  let drainingQuestions = false
  const drainQuestions = async () => {
    if (drainingQuestions) return
    drainingQuestions = true
    try {
      while (questionQueue.length > 0) {
        const pending = questionQueue.shift()
        const questions = Array.isArray(pending.questions) ? pending.questions : []
        const collected = []
        let outcome = "reply"
        for (let i = 0; i < questions.length; i += 1) {
          const info = questions[i] || {}
          const options = (Array.isArray(info.options) ? info.options : [])
            .map((option) => (option && typeof option === "object" ? option.label : option))
            .filter((label) => typeof label === "string" && label)
          const response = await request(
            {
              cmd: "question_ask",
              request_id: pending.id,
              sessionID: pending.sessionID,
              index: i,
              total: questions.length,
              header: info.header || "",
              question: info.question || "",
              options,
              multiple: Boolean(info.multiple),
            },
            65000,
          )
          if (!response || response.decision === "ask") {
            outcome = "fallback"
            break
          }
          if (response.reject) {
            outcome = "reject"
            break
          }
          collected.push(Array.isArray(response.answers) ? response.answers : [])
        }
        if (outcome === "reply" && collected.length === questions.length) {
          const method = await answerQuestion(pending.id, collected)
          await log("info", "question answered", { id: pending.id, delivered: method != null, method: method || "" })
        } else if (outcome === "reject") {
          const method = await rejectQuestion(pending.id)
          await log("info", "question rejected", { id: pending.id, delivered: method != null })
        } else {
          await log("info", "question left to terminal", { id: pending.id })
        }
      }
    } finally {
      drainingQuestions = false
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
  await log("info", "OpenCode Buddy bridge plugin initialized", { socket: SOCKET_PATH })
  try {
    await log("info", "OpenCode Buddy client surface", {
      top: Object.keys(client ?? {}).sort().join(","),
      permission: Object.keys(client?.permission ?? {}).sort().join(","),
      question: Object.keys(client?.question ?? {}).sort().join(","),
    })
  } catch {}

  return {
    event: async ({ event }) => {
      if (event?.type === "permission.asked") {
        const permission = event.properties || {}
        void log("info", "permission.asked queued", {
          id: permission.id,
          tool: permission.permission ?? permission.type ?? "",
          json: JSON.stringify(permission).slice(0, 700),
        })
        permissionQueue.push(permission)
        void drainPermissions()
        return
      }
      if (event?.type === "question.asked") {
        const request = event.properties || {}
        void log("info", "question.asked queued", { id: request.id })
        questionQueue.push(request)
        void drainQuestions()
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
