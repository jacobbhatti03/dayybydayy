# app.py
"""
DayByDay — Streamlit single-file app
- Local username/password auth (hashed with bcrypt)
- No global auto-login across devices (no shared session.json)
- Gemini AI integration
- 8-day AI-generated plan parsed into editable day cards
- Separate Chat tab for DayBot
- Full task dicts with stable IDs & "done" flag
"""

# ---------------------------
# Imports
# ---------------------------
import os
import json
from pathlib import Path
from datetime import datetime
import re
import uuid

from dotenv import load_dotenv
import streamlit as st
import bcrypt

# Gemini AI import attempt
try:
    import google.generativeai as genai
    HAS_GENAI = True
except Exception:
    genai = None
    HAS_GENAI = False

# ---------------------------
# Page config
# ---------------------------
st.set_page_config(
    page_title="DayByDay",
    page_icon="📅",
    layout="wide"
)

# ---------------------------
# Load environment
# ---------------------------
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# All app data in a dedicated folder (easy to keep private / .gitignore)
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Optional: disable signup for other people (set ALLOW_SIGNUP=false in .env)
ALLOW_SIGNUP = os.getenv("ALLOW_SIGNUP", "true").lower() == "true"

# ---------------------------
# File paths
# ---------------------------
USERS_FILE = DATA_DIR / "users.json"
PROJECTS_FILE = DATA_DIR / "projects.json"

# ---------------------------
# File helpers
# ---------------------------
def ensure_file(p: Path, default):
    if not p.exists():
        with open(p, "w", encoding="utf-8") as f:
            json.dump(default, f, indent=2, ensure_ascii=False)

def read_json(p: Path, default=None):
    default = default or {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def write_json(p: Path, obj):
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    tmp.replace(p)

# Ensure files exist
ensure_file(USERS_FILE, {})
ensure_file(PROJECTS_FILE, {})

# ---------------------------
# Password helpers (bcrypt)
# ---------------------------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False

# ---------------------------
# Session state defaults (per browser session)
# ---------------------------
if "page" not in st.session_state:
    st.session_state.page = "login"
if "user" not in st.session_state:
    st.session_state.user = None
if "project" not in st.session_state:
    st.session_state.project = {
        "title": "",
        "description": "",
        "tasks": [[] for _ in range(8)],
        "generated_at": None,
        "updated_at": None,
        "raw_plan": ""
    }
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "ask_context" not in st.session_state:
    st.session_state.ask_context = None
if "show_planner" not in st.session_state:
    st.session_state.show_planner = False

# ---------------------------
# Local Auth
# ---------------------------
def signup_local(username: str, password: str):
    if not username:
        return False, "Username required."
    users = read_json(USERS_FILE, {})

    if username in users:
        return False, "Username already exists."

    password_hash = hash_password(password)
    users[username] = {
        "password_hash": password_hash,
        "created_at": datetime.utcnow().isoformat(),
        "id": str(uuid.uuid4())
    }
    write_json(USERS_FILE, users)
    return True, "Account created."

def login_local(username: str, password: str):
    users = read_json(USERS_FILE, {})
    u = users.get(username)
    if not u:
        return False, "User not found."

    # Backwards-compatible: migrate old plain-text password if present
    if "password_hash" in u:
        if not verify_password(password, u["password_hash"]):
            return False, "Incorrect password."
    else:
        # Old version had "password" stored in plain text
        if u.get("password") != password:
            return False, "Incorrect password."
        # On successful login, upgrade to hashed password
        u["password_hash"] = hash_password(password)
        u.pop("password", None)
        users[username] = u
        write_json(USERS_FILE, users)

    return True, "Logged in."

# ---------------------------
# Project persistence
# ---------------------------
def load_user_projects(username: str):
    allp = read_json(PROJECTS_FILE, {})
    return allp.get(username, {})

def save_user_project(username: str, project: dict):
    allp = read_json(PROJECTS_FILE, {})
    if username not in allp:
        allp[username] = {}
    project["updated_at"] = datetime.utcnow().isoformat()
    allp[username][project["title"]] = project
    write_json(PROJECTS_FILE, allp)

# ---------------------------
# Gemini AI helpers
# ---------------------------
if HAS_GENAI and GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
    except Exception:
        pass

def call_gemini_text(prompt, max_tokens=400):
    if not HAS_GENAI or not GEMINI_API_KEY:
        return False, "Gemini AI not configured."
    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        resp = model.generate_content(prompt)
        if resp and getattr(resp, "text", None):
            return True, resp.text.strip()
        return False, "DayBot had no response."
    except Exception:
        # In production you would log the exception somewhere private
        return False, "DayBot is currently unavailable. Please try again."

# ---------------------------
# Task parsing helpers
# ---------------------------
def parse_plan_to_tasks(plan_text: str):
    """Parse a 'Day 1: ... Day 8:' style text into 8 lists of task dicts."""
    days = [[] for _ in range(8)]
    if not plan_text:
        return days

    # Split into blocks starting at "Day X"
    blocks = re.split(r"(?=Day\s*\d+[:\-])", plan_text, flags=re.IGNORECASE)
    for i in range(1, 9):
        block = next(
            (b for b in blocks if re.match(fr"Day\s*{i}\b", b, flags=re.IGNORECASE)),
            ""
        )
        if not block:
            continue

        content = re.sub(fr"Day\s*{i}[:\-]?", "", block, flags=re.IGNORECASE).strip()
        lines = [
            line.lstrip("-•0123456789.). \t").strip()
            for line in content.splitlines()
            if line.strip()
        ]
        for text in lines:
            days[i - 1].append({"id": None, "text": text, "done": False})

    # Assign IDs
    nid = 0
    for d in range(8):
        for t in days[d]:
            t["id"] = nid
            nid += 1

    return days

def normalize_tasks(raw_tasks):
    """
    Convert a list of string tasks or mixed dicts to a list of task dicts:
    {id:int, text:str, done:bool}.
    """
    normalized = []
    for t in raw_tasks:
        if isinstance(t, str):
            normalized.append({
                "id": None,
                "text": t.strip(),
                "done": False
            })
        elif isinstance(t, dict):
            normalized.append({
                "id": t.get("id"),
                "text": t.get("text", "").strip(),
                "done": bool(t.get("done", False))
            })
    return normalized

def assign_missing_ids(tasks_by_day):
    """Ensure every task has a unique integer id."""
    all_ids = [
        t["id"]
        for day in tasks_by_day
        for t in day
        if t.get("id") is not None
    ]
    next_id = (max(all_ids) + 1) if all_ids else 0
    for day in tasks_by_day:
        for t in day:
            if t.get("id") is None:
                t["id"] = next_id
                next_id += 1
    return tasks_by_day

# ---------------------------
# AI plan generation
# ---------------------------
def generate_8day_plan(title: str, desc: str):
    """
    Generate an 8-day plan using Gemini AI, parse it into structured tasks,
    normalize tasks, save project, and switch to planner.
    """
    if not title.strip():
        return False, "Project title is required.", None

    prompt = f"""
    You are DayBot, an expert project planner.
    Create an 8-day detailed project plan for:
    Title: {title}
    Description: {desc}

    Strictly follow this format:

    Day 1:
    - Task 1
    - Task 2
    Day 2:
    - Task 1
    ...
    Day 8:
    - Task 1
    """

    ok, ai_text = call_gemini_text(prompt)
    if not ok:
        return False, f"{ai_text}", None

    parsed_days = parse_plan_to_tasks(ai_text)
    parsed_days = assign_missing_ids(parsed_days)

    project = {
        "title": title.strip(),
        "description": desc.strip(),
        "tasks": parsed_days,
        "generated_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
        "raw_plan": ai_text.strip()
    }

    st.session_state.project = project
    save_user_project(st.session_state.user, project)

    # Switch to planner page for this session only
    st.session_state.page = "planner"

    return True, "AI 8-day plan generated successfully!", project

# ---------------------------
# DayBot contextual response
# ---------------------------
def ask_daybot_contextual(context, prompt):
    combined_prompt = f"{context}\n\nUser request: {prompt}"
    return call_gemini_text(combined_prompt)

# ---------------------------
# UI helpers
# ---------------------------
ACCENT1 = "#7b2cbf"
ACCENT2 = "#1a0536"
BG = "#0b0710"
TEXT = "#e9e6ee"
MUTED = "#bdb7d9"

st.markdown(f"""
<style>
:root {{ --accent1: {ACCENT1}; --accent2: {ACCENT2}; --bg:{BG}; --text:{TEXT}; --muted:{MUTED}; }}
html, body, #root {{ background: linear-gradient(180deg,var(--accent2),var(--bg)) !important; color: var(--text) !important; }}
.header {{ background: linear-gradient(90deg,var(--accent1),var(--accent2)); padding:12px; border-radius:10px; margin-bottom:12px }}
.card {{ background: rgba(255,255,255,0.03); padding:10px; border-radius:8px; }}
.day-card {{ background: linear-gradient(135deg, rgba(123,44,191,0.06), rgba(26,5,54,0.03)); padding:8px; border-radius:8px; margin-bottom:8px }}
.small {{ color: var(--muted); font-size:13px; }}
.sidebar-spacer {{ height:200px; }}
</style>
""", unsafe_allow_html=True)

st.markdown(
    '<div class="header"><h2 style="margin:0">📅 DayByDay</h2>'
    '<div class="small">Your AI project planner — DayBot</div></div>',
    unsafe_allow_html=True
)

# ---------------------------
# Navigation helpers
# ---------------------------
def go_to(page_name):
    st.session_state.page = page_name

# ---------------------------
# Pages
# ---------------------------
def page_login_signup():
    st.markdown('<div class="card"><strong>Login or Sign Up</strong></div>', unsafe_allow_html=True)
    lcol, rcol = st.columns(2)

    # Login
    with lcol:
        st.subheader("Login")
        login_user = st.text_input("Username", key="login_user")
        login_pass = st.text_input("Password", type="password", key="login_pass")
        if st.button("Login", key="btn_login"):
            ok, msg = login_local(login_user.strip(), login_pass)
            if ok:
                st.session_state.user = login_user.strip()
                projects = load_user_projects(st.session_state.user)
                if projects:
                    last_title = max(
                        projects.items(),
                        key=lambda kv: kv[1].get("updated_at", kv[1].get("generated_at", ""))
                    )[0]
                    st.session_state.project = projects[last_title]
                go_to("home")
            else:
                st.error(msg)

    # Signup (optional)
    with rcol:
        st.subheader("Sign Up")
        if not ALLOW_SIGNUP:
            st.info("Sign up is disabled by the app owner.")
            return

        su_user = st.text_input("Choose username", key="su_user")
        su_pass = st.text_input("Choose password", type="password", key="su_pass")
        if st.button("Create account", key="btn_signup"):
            if not su_user.strip() or not su_pass:
                st.error("Enter username and password.")
            else:
                ok, msg = signup_local(su_user.strip(), su_pass)
                if ok:
                    st.session_state.user = su_user.strip()
                    st.session_state.project = {
                        "title": "",
                        "description": "",
                        "tasks": [[] for _ in range(8)],
                        "generated_at": None,
                        "updated_at": None,
                        "raw_plan": ""
                    }
                    go_to("home")
                    st.success("Account created; welcome!")
                else:
                    st.error(msg)

def page_home():
    st.markdown('<div class="card"><strong>Home — Overview</strong></div>', unsafe_allow_html=True)
    st.markdown(f"### Welcome, **{st.session_state.user}**")
    projects = load_user_projects(st.session_state.user)

    left, right = st.columns([3, 1])
    with left:
        st.markdown("**Your projects**")
        if not projects:
            st.info("No projects yet. Click Generate new project.")
        else:
            sorted_titles = sorted(
                projects.keys(),
                key=lambda t: projects[t].get("updated_at", projects[t].get("generated_at", "")),
                reverse=True
            )
            selected = st.selectbox(
                "Open a project",
                options=["-- select --"] + sorted_titles,
                key="home_proj_sel"
            )
            if selected != "-- select --":
                if st.button("Open Project", key="open_proj_btn"):
                    st.session_state.project = projects[selected]
                    go_to("planner")

    with right:
        st.markdown("**Start**")
        if st.button("Generate new project"):
            st.session_state.project = {
                "title": "",
                "description": "",
                "tasks": [[] for _ in range(8)],
                "generated_at": None,
                "updated_at": None,
                "raw_plan": ""
            }
            st.session_state.show_planner = False
            go_to("create")

# ---------------------------
# Page: Create
# ---------------------------
def page_create():
    st.header("Create New Project")
    proj = st.session_state.project
    title = st.text_input("Project Title", value=proj.get("title", ""))
    desc = st.text_area("Project Description", value=proj.get("description", ""), height=150)

    generate_ai = st.button("🚀 Generate 8-Day Plan with AI", key="gen_ai")
    if generate_ai:
        if not title.strip() or not desc.strip():
            st.warning("Enter both title and description.")
        else:
            with st.spinner("DayBot is creating your perfect 8-day plan..."):
                ok, msg, project = generate_8day_plan(title, desc)
            if ok:
                st.session_state.show_planner = True
                st.success(msg)
            else:
                st.error(msg)

    if st.session_state.show_planner:
        page_planner()
    else:
        if st.session_state.project and st.button("🗓️ Open Planner"):
            st.session_state.show_planner = True
            page_planner()

# ---------------------------
# Page: Planner
# ---------------------------
def page_planner():
    proj = st.session_state.project
    st.title(f"📅 Planner — {proj.get('title', '')}")
    st.caption(proj.get("description", ""))

    raw_tasks = proj.get("tasks", [[] for _ in range(8)])
    tasks = []
    for day_index in range(8):
        day_tasks = raw_tasks[day_index] if day_index < len(raw_tasks) else []
        day_tasks = normalize_tasks(day_tasks)
        tasks.append(day_tasks)
    tasks = assign_missing_ids(tasks)
    proj["tasks"] = tasks

    for i in range(8):
        with st.expander(f"**Day {i+1}**", expanded=(i == 0)):
            if tasks[i]:
                for j, t in enumerate(tasks[i]):
                    task_text = t.get("text", "")
                    done = t.get("done", False)
                    c1, c2 = st.columns([0.9, 0.1])
                    new_done = c1.checkbox(
                        task_text,
                        value=done,
                        key=f"done_{i}_{j}"
                    )
                    if new_done != done:
                        t["done"] = new_done
                        proj["tasks"] = tasks
                        save_user_project(st.session_state.user, proj)

                    if c2.button("❌", key=f"del_{i}_{j}"):
                        tasks[i].pop(j)
                        proj["tasks"] = tasks
                        save_user_project(st.session_state.user, proj)
                        st.toast(f"Task removed from Day {i+1}")
                        st.experimental_rerun()
            else:
                st.info("No tasks yet.")

            new_task = st.text_input(f"Add task Day {i+1}", key=f"task_input_{i}")
            if st.button(f"Add to Day {i+1}", key=f"add_btn_{i}"):
                if new_task.strip():
                    all_ids = [
                        tt.get("id", -1)
                        for dd in tasks
                        for tt in dd
                        if tt.get("id") is not None
                    ]
                    nid = (max(all_ids) + 1) if all_ids else 0
                    tasks[i].append({"id": nid, "text": new_task.strip(), "done": False})
                    proj["tasks"] = tasks
                    save_user_project(st.session_state.user, proj)
                    st.toast(f"Added to Day {i+1}")
                    st.experimental_rerun()

    st.markdown("---")
    c1, c2 = st.columns(2)
    if c1.button("💾 Save Progress"):
        save_user_project(st.session_state.user, proj)
        st.success("Progress saved.")
    if c2.button("⬅ Back to Home"):
        go_to("home")

# ---------------------------
# Page: Chat
# ---------------------------
def page_chat():
    st.markdown('<div class="card"><strong>Chat — DayBot</strong></div>', unsafe_allow_html=True)
    st.markdown(
        "<div class='small'>Ask DayBot project-related questions or improve tasks.</div>",
        unsafe_allow_html=True
    )

    if st.session_state.ask_context:
        ctx = st.session_state.ask_context
        now = datetime.utcnow().isoformat()

        prompt = (
            f"Project: {ctx.get('project_title','')}\n"
            f"Context: Day {ctx.get('day',0)+1}\n"
            f"Task: {ctx.get('task_text','')}\n"
            f"Provide 2 improved alternatives with reasons."
        )

        planning_context = (
            st.session_state.project.get("raw_plan", "")
            or st.session_state.project.get("description", "")
        )

        ok, reply = ask_daybot_contextual(planning_context, prompt)
        st.session_state.chat_history.append(
            {"role": "user", "text": f"Improve task: {ctx.get('task_text','')}", "time": now}
        )
        if ok:
            st.session_state.chat_history.append(
                {"role": "daybot", "text": reply, "time": now}
            )
        else:
            st.session_state.chat_history.append(
                {"role": "daybot", "text": "DayBot unavailable.", "time": now}
            )
        st.session_state.ask_context = None

    for msg in st.session_state.chat_history[-100:]:
        label = "You" if msg.get("role") == "user" else "DayBot"
        st.markdown(f"**{label} ({msg.get('time')[:19]}):** {msg.get('text')}")

    st.markdown("---")
    user_msg = st.text_input("Message to DayBot", key="chat_input")
    if st.button("Send", key="chat_send"):
        if not user_msg.strip():
            st.warning("Type a message first.")
        else:
            now = datetime.utcnow().isoformat()
            st.session_state.chat_history.append(
                {"role": "user", "text": user_msg.strip(), "time": now}
            )

            context_parts = [
                st.session_state.project.get("raw_plan", ""),
                st.session_state.project.get("description", "")
            ]
            for i, day in enumerate(st.session_state.project.get("tasks", []), start=1):
                context_parts.append(f"Day {i}:")
                for t in day:
                    context_parts.append(f"- {t.get('text')}")

            context = "\n".join(context_parts)
            ok, reply = ask_daybot_contextual(context, user_msg.strip())
            if ok:
                st.session_state.chat_history.append(
                    {"role": "daybot", "text": reply, "time": now}
                )
            else:
                st.session_state.chat_history.append(
                    {"role": "daybot", "text": "DayBot unavailable.", "time": now}
                )

    if st.session_state.chat_history:
        last = st.session_state.chat_history[-1]
        if last.get("role") == "daybot":
            txt = last.get("text", "")
            if (
                re.search(r"\bDay\s*1\b", txt, re.IGNORECASE) and
                re.search(r"\bDay\s*8\b", txt, re.IGNORECASE)
            ):
                if st.button("Import last DayBot reply into tasks"):
                    parsed = parse_plan_to_tasks(txt)
                    proj = st.session_state.project
                    existing_tasks = proj.get("tasks", [[] for _ in range(8)])
                    existing_tasks = assign_missing_ids(existing_tasks)
                    proj["tasks"] = existing_tasks

                    all_ids = [
                        tt.get("id")
                        for dd in proj["tasks"]
                        for tt in dd
                        if tt.get("id") is not None
                    ]
                    nid = (max(all_ids) + 1) if all_ids else 0

                    for i in range(8):
                        for t in parsed[i]:
                            text = t.get("text") if isinstance(t, dict) else str(t)
                            proj["tasks"][i].append(
                                {"id": nid, "text": text, "done": False}
                            )
                            nid += 1

                    save_user_project(st.session_state.user, proj)
                    st.success("Imported into tasks.")

# ---------------------------
# Sidebar
# ---------------------------
def render_sidebar():
    with st.sidebar:
        st.markdown(f"### {st.session_state.user or ''}")
        pages = ["home", "create", "planner", "chat"]
        labels = ["Home", "Create Project", "Planner", "Chat"]
        try:
            idx = pages.index(st.session_state.page)
        except Exception:
            idx = 0
        sel = st.radio(
            "Navigate",
            labels,
            index=idx,
            key="sidebar_nav",
            label_visibility="collapsed"
        )
        sel_page = pages[labels.index(sel)]
        if sel_page != st.session_state.page:
            go_to(sel_page)

        st.markdown("<div class='sidebar-spacer'></div>", unsafe_allow_html=True)
        st.markdown("---")
        if st.button("Logout"):
            st.session_state.user = None
            st.session_state.project = {
                "title": "",
                "description": "",
                "tasks": [[] for _ in range(8)],
                "generated_at": None,
                "updated_at": None,
                "raw_plan": ""
            }
            st.session_state.chat_history = []
            go_to("login")
            st.success("Logged out.")

# ---------------------------
# Main router
# ---------------------------
def main():
    if not st.session_state.user:
        page_login_signup()
        return
    render_sidebar()
    if st.session_state.page == "home":
        page_home()
    elif st.session_state.page == "create":
        page_create()
    elif st.session_state.page == "planner":
        page_planner()
    elif st.session_state.page == "chat":
        page_chat()
    else:
        page_home()

if __name__ == "__main__":
    main()
