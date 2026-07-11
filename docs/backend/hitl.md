# Human-in-the-Loop (HITL) Flows

The pipeline has one gate where it pauses for user input. It transitions job status to `awaiting_approval`.

---

## POST /analyze/{trace_id}/chat

Send a message at the active HITL gate.

**Precondition:** `status === "awaiting_approval"`

**Request**
```json
{ "message": "string" }
```

**Response 202**
```json
{ "trace_id": "...", "status": "running" }
```

**Response 404** — trace_id not found.
**Response 409** — job is not in `awaiting_approval` state.

---

## Gate: hitl_gate

**When:** The conductor emits `ask_user` or `checkpoint_message` and `autopilot=false`.

**Artifact shape:**

```typescript
{
  node: "hitl_gate",
  status: "running",
  data: {},
  messages: [
    {
      role: "assistant",
      content: string,          // question or checkpoint summary
      created_at: string,       // ISO 8601
      type: "ask_user" | "checkpoint"
    }
    // after /chat: { role: "human", content: string, created_at: string }
  ]
}
```

**Detecting the active gate:**

```typescript
function isAwaitingInput(artifacts: Artifact[]): boolean {
  const gate = artifacts.find(a => a.node === "hitl_gate");
  return gate?.status === "running" && (gate.messages?.length ?? 0) > 0;
}
```

The last `messages` entry with `role: "assistant"` is the prompt to display to the user.
