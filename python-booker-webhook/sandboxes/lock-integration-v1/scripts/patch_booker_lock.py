#!/usr/bin/env python3
"""Patch sandbox booker.json: global lock + SplitInBatches loop-back."""
from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "workflows" / "booker.json"
OUT = ROOT / "workflows" / "booker-lock-v1.json"

TRY_ACQUIRE_CODE = r"""
const staticData = $getWorkflowStaticData('global');
const now = Date.now();
const TTL_MS = 10 * 60 * 1000;
const holder = staticData.lockHolder || '';
const expires = Number(staticData.lockExpires || 0);

if (staticData.lockBusy && expires > now && holder !== $execution.id) {
  return [{
    json: {
      lockAcquired: false,
      skipped: true,
      reason: 'BOOKER_BUSY',
      lockHolder: holder,
      lockExpires: new Date(expires).toISOString(),
    },
  }];
}

staticData.lockBusy = true;
staticData.lockExpires = now + TTL_MS;
staticData.lockHolder = $execution.id;
staticData.lockAcquiredAt = new Date(now).toISOString();

const payload = $('Webhook1').first().json.body || $('Webhook1').first().json;
return [{
  json: {
    ...payload,
    lockAcquired: true,
    lockExpires: new Date(staticData.lockExpires).toISOString(),
    lockHolder: $execution.id,
  },
}];
""".strip()

UNLOCK_CODE = r"""
const staticData = $getWorkflowStaticData('global');
const execId = $execution.id;
if (staticData.lockHolder === execId || Number(staticData.lockExpires || 0) < Date.now()) {
  staticData.lockBusy = false;
  staticData.lockHolder = '';
  staticData.lockExpires = 0;
}
return $input.all();
""".strip()

RESPOND_BUSY_CODE = r"""
return [{
  json: {
    ok: true,
    skipped: true,
    reason: 'BOOKER_BUSY',
    message: 'Another booker execution holds the lock.',
    timestamp: new Date().toISOString(),
  },
}];
""".strip()

RESPOND_DONE_CODE = r"""
return [{
  json: {
    ok: true,
    skipped: false,
    reason: 'COMPLETED',
    message: 'Booker run finished.',
    executionId: $execution.id,
    timestamp: new Date().toISOString(),
  },
}];
""".strip()

FILTER_BLACKLIST_CODE = r"""
const payload = $('Webhook1').first().json.body || $('Webhook1').first().json;
const office = String(payload.Office || payload.office || '');
const rows = $input.all().filter((item) => {
  const j = item.json || {};
  return String(j.office || j.Office || '') === office && String(j.status || '') === 'PENDING';
});
if (!rows.length) {
  return [{
    json: {
      noSlots: true,
      reason: 'NO_SLOTS',
      message: 'No fresh or retryable blacklist slots.',
      Office: office,
    },
  }];
}
return rows.map((item) => ({ json: item.json }));
""".strip()

RESPOND_NO_SLOTS_CODE = r"""
return [{
  json: {
    ok: true,
    skipped: true,
    reason: 'NO_SLOTS',
    message: 'No slots available to book after filtering.',
    timestamp: new Date().toISOString(),
  },
}];
""".strip()


def nid() -> str:
    return str(uuid.uuid4())


def main() -> None:
    wf = json.loads(SRC.read_text(encoding="utf-8"))
    wf = copy.deepcopy(wf)
    wf["name"] = "booker-lock-sandbox-v1"
    wf.pop("id", None)
    wf.pop("versionId", None)
    wf["active"] = False

    nodes = wf["nodes"]
    conn = wf["connections"]

    # Webhook: responseNode for busy respond path
    for n in nodes:
        if n.get("name") == "Webhook1":
            n.setdefault("parameters", {})
            n["parameters"]["path"] = "booker-lock-sandbox"
            n["parameters"]["responseMode"] = "responseNode"
            n["parameters"]["options"] = {}
            n["webhookId"] = nid()
        if n.get("name") == "HTTP Request":
            url = (ROOT / ".env.n8n.local").read_text(encoding="utf-8") if (ROOT / ".env.n8n.local").exists() else ""
            public = ""
            for line in url.splitlines():
                if line.startswith("BOOKER_PUBLIC_URL="):
                    public = line.split("=", 1)[1].strip()
            if public:
                n["parameters"]["url"] = f"{public.rstrip('/')}/webhook/booker"
        if n.get("name") == "Filter_slots":
            conds = n.get("parameters", {}).get("conditions", {}).get("conditions", [])
            for c in conds:
                if c.get("leftValue") == "={{ $json.office }}":
                    c["rightValue"] = "={{ $('Webhook1').first().json.body.Office || $('Webhook1').first().json.Office }}"
        if n.get("name") == "Get Next PENDING Student":
            n["alwaysOutputData"] = True

    try_acquire = {
        "parameters": {"jsCode": TRY_ACQUIRE_CODE},
        "id": nid(),
        "name": "Try Acquire Lock",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [-6200, 208],
    }
    if_lock = {
        "parameters": {
            "conditions": {
                "options": {"caseSensitive": True, "typeValidation": "strict", "version": 2},
                "conditions": [
                    {
                        "id": nid(),
                        "leftValue": "={{ $json.lockAcquired }}",
                        "rightValue": True,
                        "operator": {"type": "boolean", "operation": "equals"},
                    }
                ],
                "combinator": "and",
            },
            "options": {},
        },
        "id": nid(),
        "name": "IF Lock Acquired",
        "type": "n8n-nodes-base.if",
        "typeVersion": 2.2,
        "position": [-5984, 208],
    }
    respond_busy_body = {
        "parameters": {"jsCode": RESPOND_BUSY_CODE},
        "id": nid(),
        "name": "Build Busy Response",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [-5984, 400],
    }
    respond_webhook = {
        "parameters": {
            "respondWith": "json",
            "responseBody": "={{ $json }}",
            "options": {"responseCode": 200},
        },
        "id": nid(),
        "name": "Respond Webhook",
        "type": "n8n-nodes-base.respondToWebhook",
        "typeVersion": 1.1,
        "position": [-5760, 256],
    }
    unlock = {
        "parameters": {"jsCode": UNLOCK_CODE},
        "id": nid(),
        "name": "Unlock Lock",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [-4080, 112],
    }
    respond_done = {
        "parameters": {"jsCode": RESPOND_DONE_CODE},
        "id": nid(),
        "name": "Build Done Response",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [-3856, 112],
    }
    filter_blacklist = {
        "parameters": {"jsCode": FILTER_BLACKLIST_CODE},
        "id": nid(),
        "name": "Filter Blacklist Slots",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [-4976, 448],
    }
    if_has_slots = {
        "parameters": {
            "conditions": {
                "options": {"caseSensitive": True, "typeValidation": "strict", "version": 2},
                "conditions": [
                    {
                        "id": nid(),
                        "leftValue": "={{ $json.noSlots }}",
                        "rightValue": True,
                        "operator": {"type": "boolean", "operation": "notEquals"},
                    }
                ],
                "combinator": "and",
            },
            "options": {},
        },
        "id": nid(),
        "name": "IF Has Blacklist Slots",
        "type": "n8n-nodes-base.if",
        "typeVersion": 2.2,
        "position": [-4752, 448],
    }
    respond_no_slots_body = {
        "parameters": {"jsCode": RESPOND_NO_SLOTS_CODE},
        "id": nid(),
        "name": "Build No Slots Response",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [-4528, 544],
    }
    unlock_early = {
        "parameters": {"jsCode": UNLOCK_CODE},
        "id": nid(),
        "name": "Unlock Lock Early",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [-4304, 544],
    }

    nodes.extend([
        try_acquire,
        if_lock,
        respond_busy_body,
        respond_webhook,
        unlock,
        respond_done,
        filter_blacklist,
        if_has_slots,
        respond_no_slots_body,
        unlock_early,
    ])

    # Webhook -> Try Acquire (replace direct link to Get All Blacklisted Slots)
    conn["Webhook1"] = {"main": [[{"node": "Try Acquire Lock", "type": "main", "index": 0}]]}
    conn["Try Acquire Lock"] = {
        "main": [[{"node": "IF Lock Acquired", "type": "main", "index": 0}]]
    }
    conn["IF Lock Acquired"] = {
        "main": [
            [{"node": "Get All Blacklisted Slots", "type": "main", "index": 0}],
            [{"node": "Build Busy Response", "type": "main", "index": 0}],
        ]
    }
    conn["Build Busy Response"] = {
        "main": [[{"node": "Respond Webhook", "type": "main", "index": 0}]]
    }
    conn["One Slot at a Time"] = {
        "main": [
            [{"node": "Unlock Lock", "type": "main", "index": 0}],
            [{"node": "Get Next PENDING Student", "type": "main", "index": 0}],
        ]
    }
    conn["Unlock Lock"] = {
        "main": [[{"node": "Telegram: All Done", "type": "main", "index": 0}]]
    }
    conn["Telegram: All Done"] = {
        "main": [[{"node": "Build Done Response", "type": "main", "index": 0}]]
    }
    conn["Build Done Response"] = {
        "main": [[{"node": "Respond Webhook", "type": "main", "index": 0}]]
    }

    loop_targets = [
        "Telegram: Booked!",
        "Telegram: CAPTCHA Failed",
        "Telegram: Slot Taken",
        "Telegram: already booked",
        "Update Student → PENDING",
        "Telegram: No Students Left",
    ]
    for name in loop_targets:
        conn[name] = {
            "main": [[{"node": "One Slot at a Time", "type": "main", "index": 0}]]
        }

    # Blacklist retry path: always emit at least one item; unlock when nothing to book.
    conn["Get_All_Blacklisted_Slots"] = {
        "main": [[{"node": "Filter Blacklist Slots", "type": "main", "index": 0}]]
    }
    conn["Filter Blacklist Slots"] = {
        "main": [[{"node": "IF Has Blacklist Slots", "type": "main", "index": 0}]]
    }
    conn["IF Has Blacklist Slots"] = {
        "main": [
            [{"node": "Rebuild_Payload", "type": "main", "index": 0}],
            [{"node": "Build No Slots Response", "type": "main", "index": 0}],
        ]
    }
    conn["Build No Slots Response"] = {
        "main": [[{"node": "Unlock Lock Early", "type": "main", "index": 0}]]
    }
    conn["Unlock Lock Early"] = {
        "main": [[{"node": "Respond Webhook", "type": "main", "index": 0}]]
    }

    wf["settings"] = {
        **(wf.get("settings") or {}),
        "executionOrder": "v1",
    }
    wf["settings"].pop("binaryMode", None)

    OUT.write_text(json.dumps(wf, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
