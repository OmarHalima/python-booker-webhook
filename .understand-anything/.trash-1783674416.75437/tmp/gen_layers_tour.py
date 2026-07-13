import json, os

ROOT = r"F:\Projects\austrian system\filess"
INT = os.path.join(ROOT, ".understand-anything", "intermediate")

d = json.load(open(os.path.join(INT, "assembled-graph.json"), encoding="utf-8"))
nodes = d["nodes"]
edges = d["edges"]
node_ids = {n["id"] for n in nodes}

file_level = {"file", "config", "document", "service", "pipeline", "table", "schema", "resource", "endpoint"}
fl = [n for n in nodes if n["type"] in file_level]


def layer_of(n):
    p = n.get("filePath", "")
    t = n["type"]
    if p.startswith("n8n/"):
        return "n8n-orchestration"
    if "tests/" in p:
        return "tests"
    if p.startswith("python-booker-webhook/scripts/") or p.startswith("python-booker-webhook/sandboxes/"):
        return "sandbox-devtools"
    if p.startswith("python-booker-webhook/"):
        if t in ("config",):
            return "configuration"
        return "booking-engine"
    if t == "document":
        return "documentation"
    if t == "config":
        return "configuration"
    # archive, docs, .cursor, .specify, root md
    return "documentation"


LAYERS = [
    ("layer:n8n-orchestration", "n8n Workflow Orchestration",
     "n8n watcher and booker workflows that enqueue slots and POST booking payloads to the webhook."),
    ("layer:booking-engine", "Booking Engine (Playwright + FastAPI)",
     "Python webhook: FastAPI entry point plus the Playwright booking core, captcha OCR client, and scheduler HTML parsing."),
    ("layer:sandbox-devtools", "Sandboxes & Dev Tools",
     "Isolated sandbox scripts and dev/debugging utilities (OCR API, turbo payload, captcha harvest)."),
    ("layer:tests", "Tests",
     "Unit, integration, and e2e tests for the booking pipeline and HTML parsing."),
    ("layer:configuration", "Configuration & Environment",
     "Dependency manifests, environment configs, and settings for runtime behavior."),
    ("layer:documentation", "Documentation & Reference",
     "Agent guides, docs, architecture notes, and reference material."),
]

assigned = {}
layers_out = []
for lid, name, desc in LAYERS:
    ids = [n["id"] for n in fl if layer_of(n) == lid.split(":")[1]]
    for i in ids:
        assigned[i] = lid
    layers_out.append({"id": lid, "name": name, "description": desc, "nodeIds": ids})

# any unassigned?
unassigned = [n["id"] for n in fl if n["id"] not in assigned]
if unassigned:
    extra = {"id": "layer:misc", "name": "Miscellaneous", "description": "Uncategorized files.", "nodeIds": unassigned}
    layers_out.append(extra)
    for i in unassigned:
        assigned[i] = "layer:misc"

# validate every file node assigned once
assert len(assigned) == len(fl), (len(assigned), len(fl))
for n in fl:
    assert n["id"] in node_ids

json.dump(layers_out, open(os.path.join(INT, "layers.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("Layers:", [(l["id"], len(l["nodeIds"])) for l in layers_out])

# ---- Tour ----
def nid(sub):
    return f"file:{sub}"

tour = [
    {"order": 1, "title": "Project Overview",
     "description": "The Austrian appointment system: an n8n + Python pipeline that watches embassy slots, solves BotDetect captchas, and books appointments on the BMEIA portal. Start with the agent guide.",
     "nodeIds": ["document:AGENTS.md"]},
    {"order": 2, "title": "Booking Webhook Entry Point",
     "description": "FastAPI app exposing POST /webhook/booker; it receives turbo booking payloads and orchestrates the OCR/Gemini/Playwright pipeline.",
     "nodeIds": [nid("python-booker-webhook/app.py")]},
    {"order": 3, "title": "Playwright Booking Core",
     "description": "The legacy Playwright engine that drives the BMEIA site: navigation, form filling, and captcha submission.",
     "nodeIds": [nid("python-booker-webhook/booker_legacy.py")]},
    {"order": 4, "title": "Captcha & Scheduler Parsing",
     "description": "OCR client captures the BotDetect captcha image; scheduler_html parses available appointment slots from the BMEIA HTML.",
     "nodeIds": [nid("python-booker-webhook/captcha_ocr.py"), nid("python-booker-webhook/scheduler_html.py")]},
    {"order": 5, "title": "n8n Orchestration",
     "description": "n8n watcher and booker workflows that enqueue slots and trigger the webhook with booking payloads.",
     "nodeIds": [i for i in (n["id"] for n in fl) if i.startswith("file:n8n/")][:3]},
    {"order": 6, "title": "Dev Tools & Sandboxes",
     "description": "Debugging scripts and isolated sandboxes used to exercise the OCR API and harvest captcha images for fine-tuning.",
     "nodeIds": [nid("python-booker-webhook/scripts/run_ocr_captcha_api.py"), nid("python-booker-webhook/scripts/run_turbo_payload.py"), nid("python-booker-webhook/scripts/harvest_captcha_images.py")]},
    {"order": 7, "title": "Tests & Validation",
     "description": "Unit and integration tests covering slot selection, scheduler parsing, submit-error classification, and the captcha pipeline.",
     "nodeIds": [i for i in (n["id"] for n in fl) if i.startswith("file:python-booker-webhook/tests/")][:4]},
    {"order": 8, "title": "Configuration",
     "description": "Dependency manifests and environment configuration that tune runtime and captcha-solver behavior.",
     "nodeIds": [nid("python-booker-webhook/requirements.txt"), nid("python-booker-webhook/pyproject.toml")]},
]

# filter tour nodeIds to existing
for s in tour:
    s["nodeIds"] = [i for i in s["nodeIds"] if i in node_ids]

json.dump(tour, open(os.path.join(INT, "tour.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("Tour steps:", len(tour))
