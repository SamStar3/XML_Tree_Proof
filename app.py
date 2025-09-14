from flask import Flask, render_template, request, jsonify, send_file
from xml_engine.diff import parse_tree, compute_issues
from xml_engine.utils import render_full_tree_with_injected, token_diff_html, escape_xml
from xml_engine.hardindex import (
    index_element_text_spans, index_attribute_value_spans,
    build_path_key, apply_replacements
)

import os, traceback
from collections import Counter

app = Flask(__name__)
os.makedirs("output", exist_ok=True)

STATE = {
    "left_tree": None, "right_tree": None,
    "issues": [], "idx": 0,
    "accepted": [],
    "raw_left": None, "raw_right": None,
    "left_text_spans": None, "right_text_spans": None,
    "left_attr_spans": None, "right_attr_spans": None,
}

def ser_steps(steps): return [[ln, idx] for (ln, idx) in steps]
def de_steps(obj): return tuple((ln, int(idx)) for ln, idx in obj)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/diff", methods=["POST"])
def diff_route():
    try:
        raw_left  = request.files["original"].read().decode("utf-8", errors="replace")
        raw_right = request.files["modified"].read().decode("utf-8", errors="replace")

        only_kind = request.form.get("only")
        if only_kind == "all":
            only_kind = None
        if only_kind not in {"gibberish", "duplicate", "footnote", None}:
            only_kind = None

        same_files = (STATE["raw_left"] == raw_left and STATE["raw_right"] == raw_right)
        if same_files and STATE["left_tree"] is not None and STATE["right_tree"] is not None:
            left_tree, right_tree = STATE["left_tree"], STATE["right_tree"]
        else:
            left_tree  = parse_tree(raw_left)
            right_tree = parse_tree(raw_right)

        issues = compute_issues(left_tree, right_tree, only=only_kind)

        STATE.update({
            "left_tree": left_tree, "right_tree": right_tree,
            "issues": issues, "idx": 0, "accepted": [],
            "raw_left": raw_left, "raw_right": raw_right,
            "left_text_spans": None, "right_text_spans": None,
            "left_attr_spans": None, "right_attr_spans": None,
        })

        kinds = Counter([i["kind"] for i in issues])
        return jsonify({"count": len(issues), "byKind": dict(kinds)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

def filtered_indices(issue_type):
    if issue_type == "all":
        return list(range(len(STATE["issues"])))
    return [i for i, it in enumerate(STATE["issues"]) if it["kind"] == issue_type]

@app.route("/stats")
def stats():
    kinds = Counter([i["kind"] for i in STATE["issues"]])
    total = len(STATE["issues"])
    return jsonify({"total": total, "byKind": dict(kinds)})

@app.route("/render")
def render_current():
    try:
        issue_type = request.args.get("type", "gibberish")
        idxs = filtered_indices(issue_type)
        if not idxs:
            return jsonify({"left": "", "right": "", "pos": 0, "count": 0})

        view_count  = len(idxs)
        global_idx  = idxs[min(STATE["idx"], view_count - 1)]
        d           = STATE["issues"][global_idx]

        kind   = d["kind"]
        stepsL = d.get("steps")
        stepsR = d.get("steps_right", stepsL)
        attr   = d.get("attr")

        dup_side = "none"

        if kind == "duplicate" and d.get("right_highlight"):
            left_frag   = escape_xml(d.get("old",""))
            right_frag  = d["right_highlight"]
            render_kind = "text"
            dup_side    = "right"   # RIGHT has surplus -> copy LEFT→RIGHT
        elif kind == "duplicate" and d.get("left_highlight"):
            left_frag   = d["left_highlight"]
            right_frag  = escape_xml(d.get("new",""))
            render_kind = "text"
            dup_side    = "left"    # LEFT has surplus -> copy RIGHT→LEFT
        elif kind == "duplicate" and d.get("highlighted_html"):
            left_frag   = d["highlighted_html"]
            right_frag  = d["highlighted_html"]
            render_kind = "text"
            dup_side    = "none"
        else:
            old_text = d.get("old",""); new_text = d.get("new","")
            left_frag, right_frag = token_diff_html(old_text, new_text)
            render_kind = "attr" if (kind == "footnote" and attr) else "text"
            dup_side    = "none"

        left_html  = render_full_tree_with_injected(
            STATE["left_tree"].getroot(),  stepsL, left_frag,  kind=render_kind, attr=attr
        )
        right_html = render_full_tree_with_injected(
            STATE["right_tree"].getroot(), stepsR, right_frag, kind=render_kind, attr=attr
        )

        return jsonify({
        "left": left_html, "right": right_html,
        "pos": idxs.index(global_idx)+1, "count": view_count,
        "steps": ser_steps(stepsL),
        "steps_right": ser_steps(stepsR),
        "kind": render_kind,            # "text" or "attr" for rendering
        "issue_kind": d["kind"],        # 👈 real kind: "duplicate" | "gibberish" | "footnote"
        "attr": attr or None,
        "dup_side": dup_side
    })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"left": "", "right": "", "pos": 0, "count": 0, "error": str(e)}), 500

@app.route("/navigate", methods=["POST"])
def navigate():
    d = request.get_json()
    if d.get("dir") == "next":
        STATE["idx"] = min(STATE["idx"] + 1, len(STATE["issues"]) - 1)
    elif d.get("dir") == "prev":
        STATE["idx"] = max(STATE["idx"] - 1, 0)
    return jsonify({"ok": True})

@app.route("/accept", methods=["POST"])
def accept():
    d = request.get_json()

    # Ensure span indexes exist
    if STATE["left_text_spans"] is None or STATE["right_text_spans"] is None:
        STATE["left_text_spans"]  = index_element_text_spans(STATE["raw_left"])
        STATE["right_text_spans"] = index_element_text_spans(STATE["raw_right"])
    if STATE["left_attr_spans"] is None or STATE["right_attr_spans"] is None:
        STATE["left_attr_spans"]  = index_attribute_value_spans(STATE["raw_left"])
        STATE["right_attr_spans"] = index_attribute_value_spans(STATE["raw_right"])

    kind      = d.get("kind", "text")
    direction = d.get("direction", "left_to_right")   # "left_to_right" or "right_to_left"
    stepsL    = de_steps(d["steps"])                  # LEFT anchor
    stepsR    = de_steps(d.get("steps_right", d["steps"]))  # RIGHT anchor (fallback)
    attr      = (d.get("attr") or "").split(":")[-1] if kind == "attr" else None

    keyL = build_path_key(stepsL)
    keyR = build_path_key(stepsR)

    # ---- compute spans and source/dest text ----
    if kind == "attr":
        l_span = STATE["left_attr_spans"].get(f"{keyL}@{attr}")
        r_span = STATE["right_attr_spans"].get(f"{keyR}@{attr}")
        if not l_span or not r_span:
            return jsonify({"ok": False, "error": "attr span missing"}), 400
        (ls, le), (rs, re) = l_span, r_span
        src = STATE["raw_left"][ls:le] if direction == "left_to_right" else STATE["raw_right"][rs:re]
        # apply to dest side (in-memory)
        if direction == "left_to_right":
            STATE["raw_right"] = apply_replacements(STATE["raw_right"], [(rs, re, src)])
        else:
            STATE["raw_left"]  = apply_replacements(STATE["raw_left"],  [(ls, le, src)])
    else:
        l_span = STATE["left_text_spans"].get(keyL)
        r_span = STATE["right_text_spans"].get(keyR)
        if not l_span or not r_span:
            return jsonify({"ok": False, "error": "text span missing"}), 400
        (ls, le), (rs, re) = l_span, r_span
        src = STATE["raw_left"][ls:le] if direction == "left_to_right" else STATE["raw_right"][rs:re]
        if direction == "left_to_right":
            STATE["raw_right"] = apply_replacements(STATE["raw_right"], [(rs, re, src)])
        else:
            STATE["raw_left"]  = apply_replacements(STATE["raw_left"],  [(ls, le, src)])

    # ---- reparse the side we just changed so /render shows it immediately ----
    try:
        if direction == "left_to_right":
            STATE["right_tree"] = parse_tree(STATE["raw_right"])
        else:
            STATE["left_tree"]  = parse_tree(STATE["raw_left"])
    except Exception as e:
        traceback.print_exc()
        return jsonify({"ok": False, "error": f"reparse failed: {e}"}), 500

    # Invalidate span caches (they depend on raw strings)
    STATE["left_text_spans"] = STATE["right_text_spans"] = None
    STATE["left_attr_spans"] = STATE["right_attr_spans"] = None

    # Keep a record (but mark already applied so /apply won’t double-apply)
    entry = {
        "kind": kind,
        "steps": stepsL,
        "steps_right": stepsR,
        "direction": direction,
        "already_applied": True
    }
    if kind == "attr":
        entry["attr"] = attr
    STATE["accepted"].append(entry)

    # Remove the matching issue so count decreases and nav works
    ridx = None
    for i, it in enumerate(STATE["issues"]):
        if it["kind"] == kind and it.get("attr") == (attr if kind == "attr" else it.get("attr")) and it["steps"] == stepsL:
            ridx = i
            break
    if ridx is not None:
        STATE["issues"].pop(ridx)
        STATE["idx"] = min(STATE["idx"], max(0, len(STATE["issues"]) - 1))

    return jsonify({"ok": True, "remaining": len(STATE["issues"])})


@app.route("/reject", methods=["POST"])
def reject():
    return jsonify({"ok": True})

@app.route("/apply", methods=["POST"])
def apply():
    # Only apply items not already applied by /accept
    to_apply = [it for it in STATE["accepted"] if not it.get("already_applied")]
    applied_left = applied_right = 0

    if to_apply:
        # build indexes lazily
        if STATE["left_text_spans"] is None or STATE["right_text_spans"] is None:
            STATE["left_text_spans"]  = index_element_text_spans(STATE["raw_left"])
            STATE["right_text_spans"] = index_element_text_spans(STATE["raw_right"])
        if STATE["left_attr_spans"] is None or STATE["right_attr_spans"] is None:
            STATE["left_attr_spans"]  = index_attribute_value_spans(STATE["raw_left"])
            STATE["right_attr_spans"] = index_attribute_value_spans(STATE["raw_right"])

        reps_left, reps_right = [], []
        for item in to_apply:
            stepsL = item["steps"]
            stepsR = item.get("steps_right", stepsL)
            keyL = build_path_key(stepsL)
            keyR = build_path_key(stepsR)
            direction = item.get("direction", "left_to_right")

            if item.get("kind") == "attr":
                attr = (item.get("attr") or "")
                l_span = STATE["left_attr_spans"].get(f"{keyL}@{attr}")
                r_span = STATE["right_attr_spans"].get(f"{keyR}@{attr}")
                if not l_span or not r_span:
                    continue
                (ls, le), (rs, re) = l_span, r_span
                if direction == "left_to_right":
                    reps_right.append((rs, re, STATE["raw_left"][ls:le]));  applied_right += 1
                else:
                    reps_left.append((ls, le, STATE["raw_right"][rs:re]));  applied_left  += 1
            else:
                l_span = STATE["left_text_spans"].get(keyL)
                r_span = STATE["right_text_spans"].get(keyR)
                if not l_span or not r_span:
                    continue
                (ls, le), (rs, re) = l_span, r_span
                if direction == "left_to_right":
                    reps_right.append((rs, re, STATE["raw_left"][ls:le]));  applied_right += 1
                else:
                    reps_left.append((ls, le, STATE["raw_right"][rs:re]));  applied_left  += 1

        if reps_left:
            STATE["raw_left"]  = apply_replacements(STATE["raw_left"],  reps_left)
        if reps_right:
            STATE["raw_right"] = apply_replacements(STATE["raw_right"], reps_right)

        # mark those as applied so we don't re-apply next time
        for it in to_apply:
            it["already_applied"] = True

        # reparse after batch apply
        STATE["left_tree"]  = parse_tree(STATE["raw_left"])
        STATE["right_tree"] = parse_tree(STATE["raw_right"])
        STATE["left_text_spans"] = STATE["right_text_spans"] = None
        STATE["left_attr_spans"] = STATE["right_attr_spans"] = None

    # ✅ Always write what we currently have to disk (even if 0 newly applied)
    outL = os.path.join("output", "final_left.xml")
    outR = os.path.join("output", "final_right.xml")
    with open(outL, "wb") as f: f.write(STATE["raw_left"].encode("utf-8", errors="replace"))
    with open(outR, "wb") as f: f.write(STATE["raw_right"].encode("utf-8", errors="replace"))

    resp = {
        "applied_left": applied_left,
        "applied_right": applied_right,
        "download_left": "/download/left",
        "download_right": "/download/right"
    }
    if applied_left == 0 and applied_right == 0:
        resp["note"] = "already_applied_only"  # 👈 hint for UI
    return jsonify(resp)



@app.route("/download/left")
def download_left():
    return send_file(os.path.join("output", "final_left.xml"), as_attachment=True)

@app.route("/download/right")
def download_right():
    return send_file(os.path.join("output", "final_right.xml"), as_attachment=True)

if __name__ == "__main__":
    app.run(debug=True)
