# app.py
import os
import json
import copy
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import streamlit as st

load_dotenv()
DATA_DIR = Path(os.getenv("DATA_DIR", "daybyday_data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
USERS_FILE = DATA_DIR / "users.json"
PROJECTS_FILE = DATA_DIR / "projects.json"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

try:
    import google.generativeai as genai
    if hasattr(genai, "configure") and GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
    HAS_GENAI = True
except Exception:
    genai = None
    HAS_GENAI = False

def read_json(path: Path, default):
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default

def write_json(path: Path, obj):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    tmp.replace(path)

def ensure_file(path: Path, default):
    if not path.exists():
        write_json(path, default)

ensure_file(USERS_FILE, {})
ensure_file(PROJECTS_FILE, {})

# --- login helpers kept (unused) to avoid changing other parts ---
def signup_local(username, password):
    users = read_json(USERS_FILE, {})
    if not username:
        return False, "Username required"
    if username in users:
        return False, "User exists"
    users[username] = {"password": password}
    write_json(USERS_FILE, users)
    return True, "Signed up successfully"

def login_local(username, password):
    users = read_json(USERS_FILE, {})
    if username not in users:
        return False, "User not found"
    if users[username]["password"] != password:
        return False, "Incorrect password"
    return True, "Login successful"

def load_user_projects(username):
    all_proj = read_json(PROJECTS_FILE, {})
    return all_proj.get(username, {})

def save_user_project(username, project):
    all_proj = read_json(PROJECTS_FILE, {})
    if username not in all_proj:
        all_proj[username] = {}
    all_proj[username][project["title"]] = copy.deepcopy(project)
    write_json(PROJECTS_FILE, all_proj)

def delete_user_project(username, title):
    all_proj = read_json(PROJECTS_FILE, {})
    if username in all_proj and title in all_proj[username]:
        del all_proj[username][title]
        write_json(PROJECTS_FILE, all_proj)

def call_gemini_text(prompt: str, max_output_tokens: int = 400):
    if not HAS_GENAI or not GEMINI_API_KEY:
        return False, "Gemini not configured. Add GEMINI_API_KEY in .env."
    try:
        if hasattr(genai, "models") and hasattr(genai.models, "generate_text"):
            resp = genai.models.generate_text(model="gemini-1.0", prompt=prompt, max_output_tokens=max_output_tokens)
            text = getattr(resp, "text", None)
            if text is None and isinstance(resp, dict):
                text = resp.get("content") or resp.get("output")
            return True, str(text).strip()
        elif hasattr(genai, "chat") and hasattr(genai.chat, "create"):
            resp = genai.chat.create(model="gemini-1.0", messages=[{"role": "user", "content": prompt}])
            text = getattr(resp, "text", None) or getattr(resp, "output", None)
            return True, str(text).strip()
        else:
            return False, "Unsupported Gemini SDK version"
    except Exception as e:
        return False, f"Gemini error: {e}"

# --- removed login page entirely (kept function name but routes nowhere) ---
def page_login_signup():
    # Login system removed on purpose.
    st.stop()

def page_home():
    st.header(f"Welcome, {st.session_state.user}")
    projects = load_user_projects(st.session_state.user)
    st.write("Your Projects:")
    if projects:
        for title, proj in projects.items():
            with st.expander(title):
                st.write(proj.get("description", ""))
                c1, c2 = st.columns([0.6, 0.4])
                if c1.button(f"Open {title}", key=f"open_{title}"):
                    st.session_state.project = copy.deepcopy(proj)
                    st.session_state.page = "planner"
                    st.rerun()
                if c2.button(f"Delete {title}", key=f"del_{title}"):
                    delete_user_project(st.session_state.user, title)
                    st.success(f"Deleted {title}")
                    st.rerun()
    else:
        st.info("No projects yet.")
    if st.button("Create New Project"):
        st.session_state.project = {"title": "", "description": "", "tasks": [[] for _ in range(8)], "generated_at": None}
        st.session_state.page = "create"
        st.rerun()

def page_create_project():
    st.header("Create New Project")
    title = st.text_input("Project Name", value=st.session_state.project.get("title", ""))
    desc = st.text_area("Description", value=st.session_state.project.get("description", ""), height=150)
    if st.button("Save Project"):
        if not title.strip():
            st.error("Title required")
        else:
            project = {
                "title": title.strip(),
                "description": desc.strip(),
                "tasks": [[] for _ in range(8)],
                "generated_at": datetime.utcnow().isoformat()
            }
            save_user_project(st.session_state.user, project)
            st.success("Project created!")
            st.session_state.page = "home"
            st.rerun()
    st.divider()
    st.subheader("Ask DayBot to Generate Plan (optional)")
    prompt = st.text_area("Prompt for AI", placeholder="e.g., Create an 8-day fitness challenge plan")
    if st.button("Generate with AI"):
        ok, out = call_gemini_text(prompt or "Create a simple 8-day project plan")
        if ok:
            st.text_area("AI Output", value=out, height=200)
        else:
            st.error(out)

def page_planner():
    proj = st.session_state.project
    st.header(f"Planner — {proj.get('title')}")
    st.write(proj.get("description", ""))
    tasks = proj.get("tasks", [[] for _ in range(8)])
    for i in range(8):
        st.subheader(f"Day {i + 1}")
        for j, t in enumerate(tasks[i]):
            cols = st.columns([0.9, 0.1])
            cols[0].write(f"- {t}")
            if cols[1].button("❌", key=f"del_{i}_{j}"):
                tasks[i].pop(j)
                save_user_project(st.session_state.user, proj)
                st.rerun()
        new_task = st.text_input(f"Add task for Day {i+1}", key=f"new_{i}")
        if st.button(f"Add Task to Day {i+1}", key=f"add_{i}"):
            if new_task.strip():
                tasks[i].append(new_task.strip())
                proj["tasks"] = tasks
                save_user_project(st.session_state.user, proj)
                st.rerun()
    if st.button("⬅ Back to Home"):
        st.session_state.page = "home"
        st.rerun()

def main():
    if "page" not in st.session_state:
        st.session_state.page = "home"

    # ✅ force a default user (no auth)
    if "user" not in st.session_state or not st.session_state.user:
        st.session_state.user = "guest"

    st.sidebar.title("Menu")
    st.sidebar.markdown(f"👤 {st.session_state.user}")

    if st.sidebar.button("🏠 Home"):
        st.session_state.page = "home"
        st.rerun()
    if st.sidebar.button("➕ New Project"):
        st.session_state.page = "create"
        st.rerun()

    # Logout removed since there is no login now

    if st.session_state.page == "home":
        page_home()
    elif st.session_state.page == "create":
        page_create_project()
    elif st.session_state.page == "planner":
        page_planner()
    else:
        page_home()

if __name__ == "__main__":
    main()
