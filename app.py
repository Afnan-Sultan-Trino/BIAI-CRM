"""BIAI CRM (Streamlit).   Run:  streamlit run app.py"""
import csv
import os
from pathlib import Path

import pandas as pd
import streamlit as st

from core import ai, clean, db, evaluate, rules, summary
from core.schema import STATUSES

ROOT = Path(__file__).resolve().parent
ORDER = ("groq", "gemini")
NOW = rules.demo_now()
SCREENS = ["Pipeline", "Triage", "Add enquiry", "Weekly summary"]
KIND_LABEL = {"urgent": "Urgent", "support": "Support", "job": "Job seekers", "minor": "School students", "spam": "Spam"}
EXAMPLES = {
    "Banglish": ("WhatsApp", "", "", "vai ami bootcamp niye interested. amar nam Rafiq, number 01712345678. kobe shuru hobe?"),
    "Bangla": ("Facebook Messenger", "", "", "আসসালামু আলাইকুম। আমার নাম সাদিয়া। আমি ফেলোশিপ প্রোগ্রাম সম্পর্কে জানতে চাই। আমার নম্বর ০১৮৯৮৭৬৫৪৩২।"),
    "Corporate (English)": ("Website form", "", "", "Hi, this is Tania from a pharma company in Tejgaon. We need AI training for 60 of our staff. Please call me on +880 1911-000999."),
    "Angry customer": ("WhatsApp", "Lamia", "01911000107", "আমি আপনাদের কোর্স করে খুব হতাশ। টাকা ফেরত চাই, নাহলে আজকের মধ্যে ফেসবুকে পোস্ট করব।"),
}

st.set_page_config(page_title="BIAI CRM", page_icon="📇", layout="wide")
for _k in ("GROQ_API_KEY", "GEMINI_API_KEY", "GROQ_MODEL", "GEMINI_MODEL", "TLS_WORKAROUND"):
    try:                                           # Streamlit Cloud secrets -> environment
        if _k in st.secrets:
            os.environ.setdefault(_k, str(st.secrets[_k]))
    except Exception:
        pass
st.markdown("<style>.block-container{padding-top:2rem}</style>", unsafe_allow_html=True)


# ------------------------------------------------------------------ state
def load_sample():
    conn = db.connect()
    rows = evaluate.add_context(list(csv.DictReader(open(ROOT / "data" / "sample-enquiries.csv", encoding="utf-8-sig"))))
    cache = st.session_state.cache
    offline = 0
    for r in rows:
        eid, is_new = db.ingest_row(conn, r)
        a, meta = ai.analyse(r, order=(), cache=cache)         # cache only: the 20 samples never call the live API
        offline += meta["source"] == "offline"
        if is_new:
            db.apply_analysis(conn, eid, a)
        else:
            db.update_latest(conn, eid, a)
    return conn, offline


def reset():
    for k in ("conn", "selected", "draft", "summary", "offline"):
        st.session_state.pop(k, None)


if "cache" not in st.session_state:
    st.session_state.cache = ai.load_cache(ai.CACHE_PATH)
if "conn" not in st.session_state:
    st.session_state.conn, st.session_state.offline = load_sample()
    st.session_state.selected = None
if "goto" in st.session_state:
    st.session_state.nav = st.session_state.pop("goto")
conn = st.session_state.conn


def all_cards():
    return db.get_cards(conn)


# ------------------------------------------------------------------ pieces
def dash(x):
    """A lone '-' on its own line turns the line above into a heading in Markdown. Use a real dash."""
    return x if x not in (None, "", 0) else "—"


def chips(c):
    out = []
    if c["priority"] == "critical":
        out.append(":red[● critical]")
    elif c["priority"] == "high":
        out.append(":orange[● high]")
    if c["want"] and c["want"] != "unknown":
        out.append(("🏢 " if c["want"] == "corporate" else "") + c["want"])
    if c["language"]:
        out.append(c["language"])
    if rules.is_overdue(c, NOW):
        h = rules.hours_waiting(c, NOW)
        out.append(f":red[⏰ waiting {int(h // 24)}d {int(h % 24)}h]")
    if c["message_count"] > 1:
        out.append(f":violet[🔁 asked {c['message_count']}×]")
    if not c["kind"]:
        out.append(":gray[not analysed]")
    if "Offline guess" in (c["summary"] or ""):
        out.append(":orange[offline guess]")
    return "  ·  ".join(out)


def card(c, scope):
    with st.container(border=True):
        st.markdown(f"**{c['name'] or 'Unknown'}**" + (f" · {c['company']}" if c["company"] else ""))
        st.caption(f"#{c['src_ids']} · {c['channel']}")
        line = chips(c)
        if line:
            st.markdown(line)
        if c["summary"]:
            st.caption(c["summary"])
        quick = scope == "p" and c["status"] == "New"
        b = st.columns(2) if quick else st.columns(1)
        if b[0].button("Open", key=f"open_{scope}_{c['id']}"):
            st.session_state.selected = c["id"]
            st.rerun()
        if quick and b[1].button("Contacted", key=f"quick_{scope}_{c['id']}", help="Mark as contacted (restarts the 48h clock)"):
            db.set_status(conn, c["id"], "Contacted", NOW)
            st.rerun()


def detail(eid):
    c = next((x for x in all_cards() if x["id"] == eid), None)
    if c is None:
        return
    with st.container(border=True):
        head = st.columns([8, 1])
        head[0].subheader(f"{c['name'] or 'Unknown'}" + (f" · {c['role']}, {c['company']}" if c["company"] else ""))
        if head[1].button("Close", key="close_detail"):
            st.session_state.selected = None
            st.rerun()
        left, right = st.columns(2)
        with left:
            st.markdown(chips(c) or " ")
            st.markdown(f"**Phone:** `{c['phone'] or 'not given'}`  ·  **Wants:** {dash(c['want'])}  ·  **Type:** {dash(c['kind'])}"
                        + (f"  ·  **People:** {c['headcount']}" if c["headcount"] else ""))
            st.markdown("**Messages**")
            for m in db.get_messages(conn, eid):
                st.markdown(f"> {m['text']}")
                st.caption(f"{m['channel']} · {m['received_at']}")
            st.caption(f"AI note: {dash(c['summary'])}")
        with right:
            st.markdown("**Suggested reply**  (copy → send → mark contacted)")
            st.code(c["reply"] or "(no reply: this is spam)", language=None, wrap_lines=True)
            new = st.selectbox("Status", STATUSES, index=STATUSES.index(c["status"]), key=f"status_{eid}")
            b = st.columns(2)
            if b[0].button("Save status", key=f"save_{eid}"):
                db.set_status(conn, eid, new, NOW)
                st.rerun()
            if b[1].button("✅ Mark contacted", key=f"contacted_{eid}", type="primary"):
                db.set_status(conn, eid, "Contacted", NOW)
                st.rerun()


# ------------------------------------------------------------------ screens
def screen_pipeline():
    cs = all_cards()
    stats = rules.summarize(cs, NOW)
    m = st.columns(5)
    m[0].metric("Enquiries", len(cs))
    m[1].metric("In pipeline", sum(1 for c in cs if c["queue"] == "pipeline"))
    m[2].metric("Need follow-up (48h+)", len(stats["overdue"]))
    m[3].metric("High-value leads", len(stats["high_value"]))
    m[4].metric("Urgent (see Triage)", len(stats["urgent"]))
    if st.session_state.selected:
        detail(st.session_state.selected)
    show = st.radio("Show", ["All", "High value", "Need follow-up"], horizontal=True, key="pipe_filter")
    keep = {"All": lambda c: True, "High value": lambda c: c["id"] in stats["high_value"],
            "Need follow-up": lambda c: c["id"] in stats["overdue"]}[show]
    pipe = [c for c in cs if c["queue"] == "pipeline" and keep(c)]
    cols = st.columns(len(STATUSES))
    for col, status in zip(cols, STATUSES):
        items = sorted((c for c in pipe if c["status"] == status),
                       key=lambda c: (c["id"] not in stats["overdue"], c["id"] not in stats["high_value"], c["first_message_at"]))
        with col:
            st.markdown(f"##### {status} ({len(items)})")
            for c in items:
                card(c, "p")


def screen_triage():
    st.caption("Not sales: kept out of the pipeline so the sales team only sees real prospects.")
    if st.session_state.selected:
        detail(st.session_state.selected)
    cs = all_cards()
    for kind, label in KIND_LABEL.items():
        items = [c for c in cs if c["queue"] == "triage" and c["kind"] == kind]
        if not items:
            continue
        with st.expander(f"{label} ({len(items)})", expanded=kind in ("urgent", "support")):
            cols = st.columns(3)
            for i, c in enumerate(items):
                with cols[i % 3]:
                    card(c, "t")


def save_draft():
    """Runs as a button callback, i.e. BEFORE the form widgets are built, so it may clear them."""
    d = st.session_state.get("draft")
    if not d:
        return
    row = {**d["row"], "id": db.next_src_id(conn)}
    eid, is_new = db.ingest_row(conn, row)
    if is_new:
        db.apply_analysis(conn, eid, d["a"])
    else:
        db.update_latest(conn, eid, d["a"])
    st.session_state.update(selected=eid, goto="Pipeline", add_name="", add_phone="", add_msg="")
    st.session_state.pop("draft", None)


def screen_add():
    st.markdown("Paste a message from WhatsApp, Facebook, the website or a call note. The AI fills the details and suggests a reply in the sender's language.")
    st.caption("Examples:")
    ex = st.columns(len(EXAMPLES))
    for col, (label, (ch, nm, ph, tx)) in zip(ex, EXAMPLES.items()):
        def fill(ch=ch, nm=nm, ph=ph, tx=tx):
            st.session_state.update(add_channel=ch, add_name=nm, add_phone=ph, add_msg=tx)
        col.button(label, key=f"ex_{label}", on_click=fill)
    a1, a2, a3 = st.columns(3)
    channel = a1.selectbox("Came from", ["WhatsApp", "Facebook Messenger", "Facebook comment", "Website form", "Phone call note"], key="add_channel")
    name = a2.text_input("Sender name (optional)", key="add_name")
    phone = a3.text_input("Phone (optional)", key="add_phone")
    msg = st.text_area("Message", height=120, key="add_msg", placeholder="Paste the enquiry here (Bangla, Banglish or English)")
    if st.button("Analyse with AI", type="primary", disabled=not msg.strip()):
        row = {"id": "new", "channel": channel, "received at": str(NOW), "sender name": name, "phone": phone, "message": msg.strip()}
        p = clean.normalize_phone(phone) or (clean.find_phones_in_text(msg) or [None])[0]
        row["earlier_messages"] = db.count_messages_by_phone(conn, p)
        with st.spinner("Reading the message..."):
            a, meta = ai.analyse(row, order=ORDER, cache=st.session_state.cache, clients=st.session_state.get("ai_clients"))
        st.session_state.draft = {"row": row, "a": a, "meta": meta}
    d = st.session_state.get("draft")
    if not d:
        return
    a, meta, row = d["a"], d["meta"], d["row"]
    if meta["source"] == "offline":
        st.warning("AI could not be reached, so this is a rough offline guess. Check every field before saving.")
    else:
        st.caption(f"AI: {meta['provider']} · {meta['model']} · {meta.get('seconds', 0)}s ({meta['source']})")
    with st.container(border=True):
        f = st.columns(4)
        f[0].markdown(f"**Type**  \n{dash(a['kind'])}")
        f[1].markdown(f"**Wants**  \n{dash(a['want'])}")
        f[2].markdown(f"**Language**  \n{dash(a['lang'])}")
        f[3].markdown(f"**Priority**  \n{rules.priority(a['kind'], a['want'], a['headcount'])}")
        g = st.columns(4)
        g[0].markdown(f"**Name**  \n{dash(a['name'])}")
        g[1].markdown(f"**Company / role**  \n{dash(a['company'])} · {dash(a['role'])}")
        g[2].markdown(f"**Phone**  \n{a['phone'] or clean.normalize_phone(row['phone']) or '(none given)'}")
        g[3].markdown(f"**People**  \n{dash(a['headcount'])}")
        typed = (row["phone"] or "").strip()
        if typed and not clean.normalize_phone(typed):
            st.warning(f"“{typed}” is not a valid Bangladeshi mobile number (01XXXXXXXXX), so it will not be saved.")
        st.caption(f"Note for staff: {a['summary']}")
        st.markdown("**Suggested reply**")
        st.code(a["reply"] or "(no reply: this is spam)", language=None, wrap_lines=True)
    st.button("Save to CRM", type="primary", on_click=save_draft)


def screen_summary():
    cs = all_cards()
    stats = rules.summarize(cs, NOW)
    left, right = st.columns(2)
    with left:
        st.markdown("##### Pipeline")
        st.bar_chart(pd.Series(stats["by_status"]).reindex(STATUSES, fill_value=0))
    with right:
        st.markdown("##### Not sales")
        st.dataframe(pd.DataFrame({"count": {k: v for k, v in stats["by_kind"].items() if k not in ("lead", "faq")}}))
        st.markdown("##### Waiting 48h or more")
        waiting = [{"name": c["name"], "waiting_hours": round(rules.hours_waiting(c, NOW)), "status": c["status"]}
                   for c in cs if rules.is_overdue(c, NOW)]
        if waiting:
            st.dataframe(pd.DataFrame(waiting), hide_index=True)
        else:
            st.caption("Nobody is overdue.")
    st.divider()
    facts = summary.facts_from(cs, NOW)
    if st.button("📝 Write the weekly summary with AI", type="primary"):
        with st.spinner("Writing..."):
            st.session_state.summary = summary.write_summary(facts, order=ORDER, clients=st.session_state.get("ai_clients"))
    if st.session_state.get("summary"):
        text, meta, bad = st.session_state.summary
        st.markdown(text)
        if meta["source"] == "offline":
            st.caption("AI unreachable: this summary was written by plain code.")
        else:
            st.caption(f"Sentences by AI ({meta['provider']}), every number counted by code.")
        if bad:
            st.warning(f"Check these numbers, they are not in the data: {bad}")
    with st.expander("The numbers the summary is allowed to use"):
        st.json(facts)


# ------------------------------------------------------------------ layout
st.title("📇 BIAI CRM")
with st.sidebar:
    st.radio("Go to", SCREENS, key="nav")
    st.divider()
    st.caption(f"Demo clock: {NOW:%d %b %Y, %H:%M}")
    for p in ORDER:
        prov = ai.get_provider(p)
        st.caption(f"AI {p}: {'key found' if ai.has_key(prov) else 'no key'} · {prov.model}")
    st.caption(f"Saved AI results: {len(st.session_state.cache)}")
    if st.session_state.offline:
        st.warning(f"{st.session_state.offline} sample enquiries have no saved AI result. Run: python run_eval.py --providers groq --save-cache")
    st.button("Reset demo data", on_click=reset)

{"Pipeline": screen_pipeline, "Triage": screen_triage, "Add enquiry": screen_add, "Weekly summary": screen_summary}[st.session_state.get("nav", "Pipeline")]()
