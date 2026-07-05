# Human-in-the-Loop (HITL) Flows

The pipeline has two gates where it pauses for user input. Both transition job status to `awaiting_approval`.

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

## Gate 1: investigation_planner

**When:** After discovery completes. The LLM proposes a set of investigation hypotheses and skill assignments.

**Artifact shape:** The `investigation_planner` artifact will have a `messages` array containing the proposal.

```typescript
// investigation_planner artifact (inside job.artifacts)
{
  node: "investigation_planner",
  status: "running",
  data: {
    plan: {
      rationale: string;
      hypotheses: Hypothesis[];
      dep_filter: string[] | null;
    }
  },
  messages: [
    {
      role: "assistant";
      content: string;          // markdown-formatted plan proposal
      created_at: string;       // ISO 8601
    }
    // after first /chat, a human message is appended:
    // { role: "human", content: string, created_at: string, action: "approve"|"change"|"cancel" }
  ]
}

interface Hypothesis {
  id: string;                    // "h1", "h2", ...
  dep_name: string;
  statement: string;             // falsifiable risk claim
  risk_theme: string;            // "vulnerability"|"supply_chain"|"maintainer"|"license"|"reachability"|"blast_radius"
  rationale: string;
  skills: string[];              // skill IDs assigned
  status: "open"|"supported"|"refuted"|"inconclusive";
  confidence: number | null;
}
```

**Accepted responses:**
- **Approve:** `"Looks good, proceed"` — intent classified as `approve`, pipeline continues.
- **Change:** `"Focus only on direct dependencies"` — intent classified as `change`, LLM re-plans and presents a new proposal (stays `awaiting_approval`).
- **Cancel:** `"Cancel the analysis"` — intent classified as `cancel`, job transitions to `cancelled`.

Intent is classified by the LLM; no magic keywords are required.

---

## Gate 2: finding_reviewer

**When:** After evidence correlation, if there are risk findings at or above the configured severity threshold (default: all).

**Artifact shape:** The `finding_reviewer` artifact will have a `messages` array containing the findings summary.

```typescript
// finding_reviewer artifact (inside job.artifacts)
{
  node: "finding_reviewer",
  status: "running",
  data: {
    risk_findings: RiskFinding[];    // findings requiring acknowledgement
  },
  messages: [
    {
      role: "assistant";
      content: string;               // markdown-formatted findings list
      created_at: string;
    }
    // after /chat:
    // { role: "human", content: string, created_at: string, action: "approve" }
  ]
}
```

**Accepted responses:** Any message is accepted as acknowledgement (`action: "approve"`). The pipeline proceeds to `report_builder`.

---

## Detecting the active gate

When `status === "awaiting_approval"`, check `artifacts` to determine which gate is active:

```typescript
function getActiveGate(artifacts: Artifact[]): "investigation_planner" | "finding_reviewer" | null {
  const gates = ["investigation_planner", "finding_reviewer"] as const;
  for (const node of gates) {
    const artifact = artifacts.find(a => a.node === node);
    if (artifact?.status === "running" && artifact.messages?.length > 0) {
      return node;
    }
  }
  return null;
}
```

The active gate's last `messages` entry with `role: "assistant"` is the prompt to display to the user.
