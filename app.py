# app.py
"""
DayByDay — Streamlit single-file app
- Local username/password auth (hashed with bcrypt)
- Per-device login memory using browser cookies (24h)
- Per-user session persistence (project + chat + last page)
- Gemini AI integration
- 8-day AI plan generation with parsing
- Planner with task editing, adding, deleting, checkboxes
- Chat tab with contextual DayBot improvements
- No auth.json (removed for multi-device privacy)
- No session leakage between users
"""

# ---------------------------
# Imports
# ---------------------------
import os
import json
from pathlib import Path
from datetime import datetime, timedelta
import re
import uuid
import bcrypt
from dotenv import load_dotenv
import streamlit as st

# Gemini import
try:
    import google.generativeai as genai
    HAS_GENAI = True
except Exception:
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
# Load environment variables
# ---------------------------
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

ALLOW_SIGNUP = os.getenv("ALLOW_SIGNUP", "true").lower() == "true"

# Per-device cookie session length
SESSION_TTL_HOURS = 24

# ---------------------------
# File paths
# ---------------------------
USERS_FILE = DATA_DIR / "users.json"
PROJECTS_FILE = DATA_DIR / "projects.json"
SESSION_FILE = DATA_DIR / "session.json"

# ---------------------------
# Helpers for JSON files
# ---------------------------
def ensure_file(path: Path, default):
    if not path.exists():
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, indent=2, ensure_ascii=False)

def read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default or {}

def write_json(path: Path, obj):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    tmp.replace(path)

# Ensure system files exist
ensure_file(USERS_FILE, {})
ensure_file(PROJECTS_FILE, {})
ensure_file(SESSION_FILE, {})

# ---------------------------
# Bcrypt password helpers
# ---------------------------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False

# ---------------------------
# Per-user session persistence
# ---------------------------
def load_user_session(username: str):
    sessions = read_json(SESSION_FILE, {})
    return sessions.get(username, {})

def persist_session():
    user = st.session_state.get("user")
    if not user:
        return

    all_sessions = read_json(SESSION_FILE, {})
    all_sessions[user] = {
        "project": st.session_state.get("project"),
        "chat_history": st.session_state.get("chat_history", []),
        "page": st.session_state.get("page", "home"),
        "saved_at": datetime.utcnow().isoformat()
    }
    write_json(SESSION_FILE, all_sessions)

# ---------------------------
# Cookie-based login (per device)
# ---------------------------
COOKIE_NAME = "daybyday_user"

def set_cookie_user(username: str):
    expiry = datetime.utcnow() + timedelta(hours=SESSION_TTL_HOURS)
    st.set_cookie(
        COOKIE_NAME,
        username,
        expires=expiry,
        secure=False,
        httponly=False,
        samesite="Lax"
    )

def clear_cookie_user():
    st.set_cookie(COOKIE_NAME, "", expires=datetime.utcnow())

def get_cookie_user():
    return st.cookies.get(COOKIE_NAME)

# ---------------------------
# Session State Defaults
# ---------------------------
if "user" not in st.session_state:
    st.session_state.user = None
if "page" not in st.session_state:
    st.session_state.page = "login"
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

# ---------------------------
# Auto-login using cookie
# ---------------------------
cookie_user = get_cookie_user()

if cookie_user and st.session_state.user is None:
    st.session_state.user = cookie_user

    saved_session = load_user_session(cookie_user)
    if saved_session:
        st.session_state.project = saved_session.get("project", st.session_state.project)
        st.session_state.chat_history = saved_session.get("chat_history", [])
        st.session_state.page = saved_session.get("page", "home")
    else:
        st.session_state.page = "home"

# ---------------------------
# Authentication
# ---------------------------
def signup_local(username: str, password: str):
    users = read_json(USERS_FILE, {})

    if username in users:
        return False, "Username already exists."

    users[username] = {
        "password_hash": hash_password(password),
        "created_at": datetime.utcnow().isoformat(),
        "id": str(uuid.uuid4())
    }
    write_json(USERS_FILE, users)
    return True, "Account created."

def login_local(username: str, password: str):
    users = read_json(USERS_FILE, {})
    user = users.get(username)

    if not user:
        return False, "User not found."

    if not verify_password(password, user["password_hash"]):
        return False, "Incorrect password."

    return True, "Logged in."

# ---------------------------
# Project persistence helpers
# ---------------------------
def load_user_projects(username: str):
    all_projects = read_json(PROJECTS_FILE, {})
    return all_projects.get(username, {})

def save_user_project(username: str, project: dict):
    all_projects = read_json(PROJECTS_FILE, {})
    if username not in all_projects:
        all_projects[username] = {}

    project["updated_at"] = datetime.utcnow().isoformat()
    all_projects[username][project["title"]] = project

    write_json(PROJECTS_FILE, all_projects)
    persist_session()
# ---------------------------
# Gemini AI setup
# ---------------------------
if HAS_GENAI and GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
    except Exception:
        pass

def call_gemini_text(prompt):
    if not HAS_GENAI or not GEMINI_API_KEY:
        return False, "Gemini is not configured."

    try:
        model = genai.GenerativeModel(GEMINI_MODEL)
        resp = model.generate_content(prompt)
        if resp and hasattr(resp, "text") and resp.text:
            return True, resp.text.strip()
        else:
            return False, "DayBot returned no response."
    except Exception as e:
        return False, f"DayBot error: {e}"

# ---------------------------
# Task Parsing
# ---------------------------
def parse_plan_to_tasks(plan_text: str):
    """
    Parse Day 1: ... Day 8: format into structured 8 lists of task dicts.
    """
    days = [[] for _ in range(8)]
    if not plan_text:
        return days

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

    next_id = 0
    for d in range(8):
        for t in days[d]:
            t["id"] = next_id
            next_id += 1

    return days

def normalize_tasks(raw_tasks):
    normalized = []
    for t in raw_tasks:
        if isinstance(t, str):
            normalized.append({"id": None, "text": t.strip(), "done": False})
        elif isinstance(t, dict):
            normalized.append({
                "id": t.get("id"),
                "text": t.get("text", "").strip(),
                "done": bool(t.get("done", False))
            })
    return normalized

def assign_missing_ids(tasks_by_day):
    all_ids = []
    for day in tasks_by_day:
        for t in day:
            if t.get("id") is not None:
                all_ids.append(t["id"])

    next_id = max(all_ids) + 1 if all_ids else 0

    for day in tasks_by_day:
        for t in day:
            if t.get("id") is None:
                t["id"] = next_id
                next_id += 1

    return tasks_by_day

# ---------------------------
# AI Plan Generation
# ---------------------------
def generate_8day_plan(title: str, desc: str):
    if not title.strip():
        return False, "Project title is required.", None

    prompt = f"""
You are DayBot, an expert project planner.
Create an 8-day detailed project plan.

Title: {title}
Description: {desc}

Strict format:

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
        return False, ai_text, None

    parsed = parse_plan_to_tasks(ai_text)
    parsed = assign_missing_ids(parsed)

    project = {
        "title": title.strip(),
        "description": desc.strip(),
        "tasks": parsed,
        "generated_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
        "raw_plan": ai_text.strip()
    }

    st.session_state.project = project
    save_user_project(st.session_state.user, project)

    st.session_state.page = "planner"
    persist_session()

    return True, "8-day plan generated!", project

# ---------------------------
# DayBot contextual improvement
# ---------------------------
def ask_daybot_contextual(context, prompt):
    combined = f"{context}\n\nUser request: {prompt}"
    return call_gemini_text(combined)

# ---------------------------
# CSS Styling
# ---------------------------
ACCENT1 = "#7b2cbf"
ACCENT2 = "#1a0536"
BG = "#0b0710"
TEXT = "#e9e6ee"
MUTED = "#bdb7d9"

st.markdown(f"""
<style>
:root {{
  --accent1: {ACCENT1};
  --accent2: {ACCENT2};
  --bg: {BG};
  --text: {TEXT};
  --muted: {MUTED};
}}
html, body, #root {{
    background: linear-gradient(180deg, var(--accent2), var(--bg)) !important;
    color: var(--text) !important;
}}
.header {{
    background: linear-gradient(90deg, var(--accent1), var(--accent2));
    padding: 12px;
    border-radius: 10px;
    margin-bottom: 12px;
}}
.card {{
    background: rgba(255,255,255,0.03);
    padding: 10px;
    border-radius: 8px;
}}
.day-card {{
    background: linear-gradient(135deg, rgba(123,44,191,0.06), rgba(26,5,54,0.03));
    padding: 8px;
    border-radius: 8px;
    margin-bottom: 8px;
}}
.small {{
    color: var(--muted);
    font-size: 13px;
}}
.sidebar-spacer {{
    height: 200px;
}}
</style>
""", unsafe_allow_html=True)

st.markdown(
    '<div class="header"><h2 style="margin:0">📅 DayByDay</h2>'
    '<div class="small">Your AI project planner — DayBot</div></div>',
    unsafe_allow_html=True
)

# ---------------------------
# Navigation helper
# ---------------------------
def go_to(page_name):
    st.session_state.page = page_name
    persist_session()

# ---------------------------
# LOGIN & SIGNUP PAGE
# ---------------------------
def page_login_signup():
    st.markdown('<div class="card"><strong>Login or Sign Up</strong></div>', unsafe_allow_html=True)

    col1, col2 = st.columns(2)

    # -------- LOGIN --------
    with col1:
        st.subheader("Login")
        user = st.text_input("Username", key="login_user")
        pw   = st.text_input("Password", type="password", key="login_pass")

        if st.button("Login", key="btn_login"):
            ok, msg = login_local(user.strip(), pw)
            if ok:
                st.session_state.user = user.strip()

                loaded = load_user_session(st.session_state.user)
                if loaded:
                    st.session_state.project = loaded.get("project", st.session_state.project)
                    st.session_state.chat_history = loaded.get("chat_history", [])
                    st.session_state.page = loaded.get("page", "home")
                else:
                    st.session_state.page = "home"

                set_cookie_user(st.session_state.user)
                persist_session()
                st.rerun()
            else:
                st.error(msg)

    # -------- SIGNUP --------
    with col2:
        st.subheader("Sign Up")
        if not ALLOW_SIGNUP:
            st.info("Signup is disabled by the app owner.")
            return

        newu = st.text_input("Choose username", key="su_user")
        newp = st.text_input("Choose password", type="password", key="su_pass")

        if st.button("Create account", key="btn_signup"):
            if not newu.strip() or not newp:
                st.error("Enter username and password.")
            else:
                ok, msg = signup_local(newu.strip(), newp)
                if ok:
                    st.session_state.user = newu.strip()
                    st.session_state.project = {
                        "title": "",
                        "description": "",
                        "tasks": [[] for _ in range(8)],
                        "generated_at": None,
                        "updated_at": None,
                        "raw_plan": ""
                    }
                    st.session_state.chat_history = []
                    st.session_state.page = "home"
                    set_cookie_user(st.session_state.user)
                    persist_session()
                    st.success("Account created!")
                    st.rerun()
                else:
                    st.error(msg)
# ---------------------------
# HOME PAGE
# ---------------------------
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
                    persist_session()
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
            persist_session()
            go_to("create")

# ---------------------------
# CREATE PAGE
# ---------------------------
def page_create():
    st.header("Create New Project")
    proj = st.session_state.project

    title = st.text_input("Project Title", value=proj.get("title", ""))
    desc = st.text_area("Project Description", value=proj.get("description", ""), height=150)

    if st.button("🚀 Generate 8-Day Plan with AI"):
        if not title.strip() or not desc.strip():
            st.warning("Enter both title and description.")
        else:
            with st.spinner("DayBot is creating your perfect 8-day plan..."):
                ok, msg, project = generate_8day_plan(title, desc)
            if ok:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)

    if st.button("🗓️ Open Planner"):
        go_to("planner")

# ---------------------------
# PLANNER PAGE
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
        with st.expander(f"Day {i+1}", expanded=(i == 0)):
            if tasks[i]:
                for j, t in enumerate(tasks[i]):
                    task_text = t["text"]
                    done = t["done"]

                    c1, c2 = st.columns([0.9, 0.1])
                    new_done = c1.checkbox(task_text, value=done, key=f"done_{i}_{j}")

                    if new_done != done:
                        t["done"] = new_done
                        save_user_project(st.session_state.user, proj)

                    if c2.button("❌", key=f"delete_{i}_{j}"):
                        tasks[i].pop(j)
                        proj["tasks"] = tasks
                        save_user_project(st.session_state.user, proj)
                        st.toast(f"Task removed from Day {i+1}")
                        st.rerun()

            else:
                st.info("No tasks yet.")

            new_task = st.text_input(f"Add task to Day {i+1}", key=f"newtask_{i}")
            if st.button(f"Add to Day {i+1}", key=f"addbtn_{i}"):
                if new_task.strip():
                    all_ids = [
                        tt["id"] for dd in tasks for tt in dd if tt.get("id") is not None
                    ]
                    nid = (max(all_ids) + 1) if all_ids else 0

                    tasks[i].append({"id": nid, "text": new_task.strip(), "done": False})
                    proj["tasks"] = tasks
                    save_user_project(st.session_state.user, proj)
                    st.toast(f"Added to Day {i+1}")
                    st.rerun()

    st.markdown("---")
    c1, c2 = st.columns(2)
    if c1.button("💾 Save Progress"):
        save_user_project(st.session_state.user, proj)
        st.success("Progress saved.")
    if c2.button("⬅ Back to Home"):
        go_to("home")

# ---------------------------
# CHAT PAGE
# ---------------------------
def page_chat():
    st.markdown('<div class="card"><strong>Chat — DayBot</strong></div>', unsafe_allow_html=True)
    st.markdown("<div class='small'>Ask DayBot to improve tasks or clarify your project.</div>", unsafe_allow_html=True)

    # Auto-improvement mode
    if st.session_state.ask_context:
        ctx = st.session_state.ask_context
        st.session_state.ask_context = None

        now = datetime.utcnow().isoformat()
        prompt = (
            f"Project: {ctx['project_title']}\n"
            f"Context: Day {ctx['day']+1}\n"
            f"Task: {ctx['task_text']}\n"
            "Provide improved alternatives with justification."
        )

        planning_context = (
            st.session_state.project.get("raw_plan") or
            st.session_state.project.get("description")
        )

        ok, reply = ask_daybot_contextual(planning_context, prompt)

        st.session_state.chat_history.append({"role": "user", "text": ctx["task_text"], "time": now})
        st.session_state.chat_history.append({
            "role": "daybot",
            "text": reply if ok else "DayBot unavailable.",
            "time": now
        })
        persist_session()

    # Show chat history
    for msg in st.session_state.chat_history[-200:]:
        sender = "You" if msg["role"] == "user" else "DayBot"
        st.markdown(f"**{sender} ({msg['time'][:19]}):** {msg['text']}")

    # Input box
    st.markdown("---")
    txt = st.text_input("Message DayBot", key="chat_input")
    if st.button("Send", key="chat_send"):
        if txt.strip():
            now = datetime.utcnow().isoformat()
            st.session_state.chat_history.append(
                {"role": "user", "text": txt.strip(), "time": now}
            )

            context_parts = [
                st.session_state.project.get("raw_plan", ""),
                st.session_state.project.get("description", "")
            ]
            for i, day in enumerate(st.session_state.project.get("tasks", []), start=1):
                context_parts.append(f"Day {i}:")
                for t in day:
                    context_parts.append(f"- {t['text']}")

            fullctx = "\n".join(context_parts)
            ok, reply = ask_daybot_contextual(fullctx, txt.strip())

            st.session_state.chat_history.append({
                "role": "daybot",
                "text": reply if ok else "DayBot unavailable.",
                "time": now
            })
            persist_session()
            st.rerun()
        else:
            st.warning("Enter a message.")

    # Auto-import improved plan if detected
    if st.session_state.chat_history:
        last = st.session_state.chat_history[-1]
        if last["role"] == "daybot":
            text = last["text"]
            if (
                re.search(r"\bDay\s*1\b", text, re.IGNORECASE)
                and re.search(r"\bDay\s*8\b", text, re.IGNORECASE)
            ):
                if st.button("Import Last Plan into Tasks"):
                    parsed = parse_plan_to_tasks(text)
                    proj = st.session_state.project
                    existing = proj["tasks"]
                    existing = assign_missing_ids(existing)

                    all_ids = [
                        t["id"] for day in existing for t in day if t.get("id") is not None
                    ]
                    nid = (max(all_ids) + 1) if all_ids else 0

                    for i in range(8):
                        for t in parsed[i]:
                            txt2 = t["text"]
                            existing[i].append({"id": nid, "text": txt2, "done": False})
                            nid += 1

                    proj["tasks"] = existing
                    save_user_project(st.session_state.user, proj)
                    persist_session()
                    st.success("Imported plan!")
                    st.rerun()
# ---------------------------
# SIDEBAR
# ---------------------------
def render_sidebar():
    with st.sidebar:
        st.markdown(f"### {st.session_state.user or ''}")

        pages = ["home", "create", "planner", "chat"]
        labels = ["Home", "Create Project", "Planner", "Chat"]

        try:
            idx = pages.index(st.session_state.page)
        except:
            idx = 0

        sel = st.radio(
            "Navigate",
            labels,
            index=idx,
            key="sidebar_nav",
            label_visibility="collapsed"
        )

        selected_page = pages[labels.index(sel)]
        if selected_page != st.session_state.page:
            go_to(selected_page)

        st.markdown("<div class='sidebar-spacer'></div>", unsafe_allow_html=True)
        st.markdown("---")

        # LOGOUT BUTTON
        if st.button("Logout"):
            persist_session()
            clear_auth()

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
            st.session_state.page = "login"

            st.success("Logged out.")
            st.rerun()

# ---------------------------
# MAIN ROUTER
# ---------------------------
def main():
    # If no user logged in → show login page
    if not st.session_state.user:
        page_login_signup()
        return

    # Sidebar always visible after login
    render_sidebar()

    # Page Routing
    page = st.session_state.page

    if page == "home":
        page_home()
    elif page == "create":
        page_create()
    elif page == "planner":
        page_planner()
    elif page == "chat":
        page_chat()
    else:
        page_home()

# ---------------------------
# ENTRY POINT
# ---------------------------
if __name__ == "__main__":
    main()
