import json, os

ROOT = r"F:\Projects\austrian system\filess"
INT = os.path.join(ROOT, ".understand-anything", "intermediate")
TMP = os.path.join(ROOT, ".understand-anything", "tmp")

scan = json.load(open(os.path.join(INT, "scan-result.json"), encoding="utf-8"))
batches = json.load(open(os.path.join(INT, "batches.json"), encoding="utf-8"))["batches"]
import_map = scan["importMap"]

CAT_BY_PATH = {f["path"]: f["fileCategory"] for f in scan["files"]}
LANG_BY_PATH = {f["path"]: f["language"] for f in scan["files"]}


def import_map_cat(dep):
    return CAT_BY_PATH.get(dep, "code")


def is_noncode(dep):
    return import_map_cat(dep) not in ("code", "script", "markup")


CORE_KNOWLEDGE = {
    "python-booker-webhook/app.py": (
        "FastAPI application exposing POST /webhook/booker; receives turbo booking payloads and orchestrates the OCR/Gemini/Playwright booking pipeline against the BMEIA portal.",
        ["entry-point", "api-handler", "fastapi", "webhook", "orchestration"],
    ),
    "python-booker-webhook/booker_legacy.py": (
        "Playwright-based core booking engine that drives the BMEIA appointment site (navigation, form fill, captcha submit). Marked legacy — do not grow.",
        ["booking-engine", "playwright", "legacy", "browser-automation"],
    ),
    "python-booker-webhook/captcha_ocr.py": (
        "OCR client that captures the BotDetect captcha image and forwards it to the OCR service (port 8001) for solving.",
        ["captcha", "ocr", "client", "integration"],
    ),
    "python-booker-webhook/scheduler_html.py": (
        "Parses the BMEIA scheduler HTML to extract available appointment slots and free dates.",
        ["html-parsing", "scraping", "scheduler", "parsing"],
    ),
    "python-booker-webhook/scripts/run_ocr_captcha_api.py": (
        "Manual script to exercise the OCR captcha API end-to-end for debugging.",
        ["script", "ocr", "debugging"],
    ),
    "python-booker-webhook/scripts/run_turbo_payload.py": (
        "Dev script that sends a turbo booking payload (KOPENHAGEN) through the local pipeline.",
        ["script", "turbo", "dev-tool"],
    ),
    "python-booker-webhook/scripts/harvest_captcha_images.py": (
        "Harvests captcha images to build a fine-tune dataset for the solver.",
        ["script", "captcha", "dataset", "training"],
    ),
    "AGENTS.md": (
        "Agent guide documenting the Austrian appointment system architecture, captcha pipeline, commands, and hard rules.",
        ["documentation", "agent-guide", "architecture", "entry-point"],
    ),
}


def classify_type(path, category, lang):
    base = os.path.basename(path).lower()
    if category in ("code", "script", "markup"):
        return "file"
    if category == "config":
        return "config"
    if category == "docs":
        return "document"
    if category == "data":
        return "table" if path.endswith(".sql") else "schema"
    if category == "infra":
        if "dockerfile" in base or "docker-compose" in base or base.endswith(".compose.yml") or base.endswith(".compose.yaml"):
            return "service"
        if ".github/workflows" in path or base in ("jenkinsfile", ".gitlab-ci.yml") or base.startswith(".circleci"):
            return "pipeline"
        if path.endswith(".tf") or path.endswith(".tfvars"):
            return "resource"
        return "service"
    return "file"


def node_id_for(path, category, lang):
    return f"{classify_type(path, category, lang)}:{path}"


def summary_for(path, category, res):
    if path in CORE_KNOWLEDGE:
        return CORE_KNOWLEDGE[path][0]
    stem = os.path.splitext(os.path.basename(path))[0]
    funcs = res.get("functions", [])
    classes = res.get("classes", [])
    nf, nc = len(funcs), len(classes)
    if category in ("code", "script", "markup"):
        if nf or nc:
            return f"Module '{stem}' providing {nf} functions and {nc} classes within the appointment-booking pipeline."
        return f"Source file '{stem}' in the appointment-booking pipeline."
    if category == "config":
        return f"Configuration file controlling settings for the Austrian appointment booking system ({stem})."
    if category == "docs":
        return f"Documentation for the Austrian appointment booking system ({stem})."
    if category == "data":
        return f"Data/schema definition ({stem})."
    if category == "infra":
        return f"Infrastructure definition ({stem})."
    return f"File '{stem}'."


def tags_for(path, category, res, lang):
    if path in CORE_KNOWLEDGE:
        return CORE_KNOWLEDGE[path][1]
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    base = os.path.basename(path).lower()
    tags = []
    if category in ("code", "script", "markup"):
        tags.append("code")
        if "test" in base or stem.startswith("test_") or stem.endswith("_test"):
            tags.append("test")
        if "util" in stem or "helper" in stem:
            tags.append("utility")
        if stem == "app" or base == "main.py":
            tags.append("entry-point")
    elif category == "config":
        tags += ["configuration"]
        if base in ("requirements.txt", "pyproject.toml"):
            tags.append("dependencies")
    elif category == "docs":
        tags += ["documentation"]
    elif category == "data":
        tags += ["data", "schema-definition"]
    elif category == "infra":
        tags += ["infrastructure"]
        if "dockerfile" in base:
            tags += ["containerization"]
        if ".github/workflows" in path:
            tags += ["ci-cd"]
        if path.endswith(".tf"):
            tags += ["deployment"]
    tags.append(lang)
    return tags[:5] if len(tags) >= 3 else tags + ["untagged"]


def complexity_for(res):
    ne = res.get("nonEmptyLines", 0)
    return "simple" if ne < 50 else ("moderate" if ne <= 200 else "complex")


def sig_func(fn):
    return (fn.get("endLine", 0) - fn.get("startLine", 0) + 1) >= 10


def sig_class(cl):
    if len(cl.get("methods", [])) >= 2:
        return True
    return (cl.get("endLine", 0) - cl.get("startLine", 0) + 1) >= 20


total_nodes = total_edges = 0

for b in batches:
    bi = b["batchIndex"]
    files = b["files"]
    batch_import = b["batchImportData"]
    res_data = json.load(open(os.path.join(TMP, f"ua-file-extract-results-{bi}.json"), encoding="utf-8"))
    results = {r["path"]: r for r in res_data.get("results", [])}

    nodes, edges, seen_ids = [], [], set()

    for f in files:
        path, lang, cat = f["path"], f["language"], f["fileCategory"]
        res = results.get(path, {})
        nid = node_id_for(path, cat, lang)
        if nid in seen_ids:
            continue
        seen_ids.add(nid)
        nodes.append({
            "id": nid, "type": classify_type(path, cat, lang), "name": os.path.basename(path),
            "filePath": path, "summary": summary_for(path, cat, res),
            "tags": tags_for(path, cat, res, lang), "complexity": complexity_for(res),
        })

        for fn in res.get("functions", []):
            if not sig_func(fn):
                continue
            fid = f"function:{path}:{fn['name']}"
            if fid in seen_ids:
                continue
            seen_ids.add(fid)
            nodes.append({
                "id": fid, "type": "function", "name": fn["name"], "filePath": path,
                "lineRange": [fn.get("startLine"), fn.get("endLine")],
                "summary": f"Function '{fn['name']}' in {os.path.basename(path)}.",
                "tags": ["function"],
                "complexity": "simple" if (fn.get("endLine", 0) - fn.get("startLine", 0)) < 30 else "moderate",
            })
            edges.append({"source": nid, "target": fid, "type": "contains", "direction": "forward", "weight": 1.0})

        for cl in res.get("classes", []):
            if not sig_class(cl):
                continue
            cid = f"class:{path}:{cl['name']}"
            if cid in seen_ids:
                continue
            seen_ids.add(cid)
            nodes.append({
                "id": cid, "type": "class", "name": cl["name"], "filePath": path,
                "lineRange": [cl.get("startLine"), cl.get("endLine")],
                "summary": f"Class '{cl['name']}' in {os.path.basename(path)}.",
                "tags": ["class"], "complexity": "moderate",
            })
            edges.append({"source": nid, "target": cid, "type": "contains", "direction": "forward", "weight": 1.0})

        if cat in ("code", "script"):
            for dep in batch_import.get(path, []):
                if dep == path:
                    continue
                edges.append({"source": nid, "target": node_id_for(dep, import_map_cat(dep), LANG_BY_PATH.get(dep, lang)),
                              "type": "imports", "direction": "forward", "weight": 0.7})

        for ex in res.get("exports", []):
            ename = ex.get("name")
            if ename and f"function:{path}:{ename}" in seen_ids:
                edges.append({"source": nid, "target": f"function:{path}:{ename}", "type": "exports", "direction": "forward", "weight": 0.8})

    for n in nodes:
        if n["type"] == "document" and n["filePath"] == "AGENTS.md":
            for tgt in ["python-booker-webhook/app.py", "python-booker-webhook/booker_legacy.py",
                        "python-booker-webhook/captcha_ocr.py", "python-booker-webhook/scheduler_html.py"]:
                edges.append({"source": n["id"], "target": f"file:{tgt}", "type": "documents", "direction": "forward", "weight": 0.5})
        if n["type"] == "config" and os.path.basename(n["filePath"]) in ("requirements.txt", "pyproject.toml", "requirements-dev.txt"):
            edges.append({"source": n["id"], "target": "file:python-booker-webhook/app.py", "type": "configures", "direction": "forward", "weight": 0.6})

    for f in files:
        path = f["path"]
        if "test" in path.lower() or path.startswith("python-booker-webhook/tests"):
            for dep in import_map.get(path, []):
                if "tests" not in dep:
                    edges.append({"source": f"file:{dep}", "target": f"file:{path}", "type": "tested_by", "direction": "forward", "weight": 0.5})

    json.dump({"nodes": nodes, "edges": edges}, open(os.path.join(INT, f"batch-{bi}.json"), "w", encoding="utf-8"), ensure_ascii=False)
    total_nodes += len(nodes)
    total_edges += len(edges)

print("Wrote 27 batch fragments. nodes(incl cross-batch dup)=", total_nodes, "edges=", total_edges)
