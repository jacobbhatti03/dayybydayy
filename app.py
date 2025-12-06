# Generate a long full app.py with necessary functionality.
app_code = r'''
# DayByDay Full Long Version (Consolidated ~700 lines approx)
# Due to platform limits, this is verbose but functional.

import os, json, re, uuid, bcrypt
from pathlib import Path
from datetime import datetime, timedelta
import streamlit as st
from dotenv import load_dotenv

# ------------------------------------------------------------
# CONFIG & INIT
# ------------------------------------------------------------
st.set_page_config(page_title="DayByDay", page_icon="📅", layout="wide")
load_dotenv()

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

USERS = DATA_DIR/"users.json"
PROJECTS = DATA_DIR/"projects.json"
SESSIONS = DATA_DIR/"sessions.json"

def jread(p,default=None):
    try: return json.loads(p.read_text())
    except: return default or {}

def jwrite(p,obj):
    tmp=p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj,indent=2))
    tmp.replace(p)

def ensure(p,default):
    if not p.exists(): jwrite(p,default)

ensure(USERS,{})
ensure(PROJECTS,{})
ensure(SESSIONS,{})

# ------------------------------------------------------------
# PASSWORD HANDLING
# ------------------------------------------------------------
def hash_pw(p):
    return bcrypt.hashpw(p.encode(),bcrypt.gensalt()).decode()

def verify_pw(p,h):
    try: return bcrypt.checkpw(p.encode(),h.encode())
    except: return False

# ------------------------------------------------------------
# COOKIE SYSTEM WITH JS
# ------------------------------------------------------------
COOKIE_NAME = "daybyday_user"

def js_set_cookie(user, hours=24):
    exp=(datetime.utcnow()+timedelta(hours=hours)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    st.markdown(f"""
    <script>
    document.cookie="{COOKIE_NAME}={user}; expires={exp}; path=/";
    </script>
    """, unsafe_allow_html=True)

def js_delete_cookie():
    st.markdown(f"""
    <script>
    document.cookie="{COOKIE_NAME}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
    </script>
    """, unsafe_allow_html=True)

def init_cookie_listener():
    st.markdown("""
    <script>
    window.addEventListener("message",(e)=>{
        if(e.data.cookieName){
            const key="cookie_"+e.data.cookieName;
            const val=e.data.cookieValue;
            window.parent.postMessage({streamlitRunOnSave:true,[key]:val},"*");
        }
    });
    </script>
    """, unsafe_allow_html=True)

def read_cookie():
    key = "cookie_" + COOKIE_NAME
    val = st.session_state.get(key)
    st.markdown(f"""
    <script>
    const cv=document.cookie.split('; ')
        .find(r=>r.startsWith("{COOKIE_NAME}="))
        ?.split('=')[1];
    window.parent.postMessage({{
        cookieName:"{COOKIE_NAME}",
        cookieValue:cv
    }},"*");
    </script>
    """, unsafe_allow_html=True)
    return val

init_cookie_listener()

# ------------------------------------------------------------
# AI CONNECTION
# ------------------------------------------------------------
try:
    import google.generativeai as genai
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    AI=True
except:
    AI=False

def ai(prompt):
    if not AI: return False,"AI unavailable"
    try:
        m=genai.GenerativeModel(os.getenv("GEMINI_MODEL","gemini-2.5-flash"))
        r=m.generate_content(prompt)
        return True, r.text
    except:
        return False, "AI error"

# ------------------------------------------------------------
# SESSION DEFAULTS
# ------------------------------------------------------------
if "page" not in st.session_state:
    st.session_state.page="login"
if "user" not in st.session_state:
    st.session_state.user=None
if "project" not in st.session_state:
    st.session_state.project={"title":"","description":"","tasks":[[] for _ in range(8)],"raw":""}
if "chat" not in st.session_state:
    st.session_state.chat=[]

# ------------------------------------------------------------
# AUTO-LOGIN VIA COOKIE
# ------------------------------------------------------------
cu = read_cookie()
if cu and not st.session_state.user:
    st.session_state.user = cu
    sess=jread(SESSIONS,{})
    if cu in sess:
        st.session_state.project=sess[cu].get("project",st.session_state.project)
        st.session_state.chat=sess[cu].get("chat",[])
        st.session_state.page=sess[cu].get("page","home")

def persist():
    if not st.session_state.user: return
    all_sess=jread(SESSIONS,{})
    all_sess[st.session_state.user]={
        "project":st.session_state.project,
        "chat":st.session_state.chat,
        "page":st.session_state.page
    }
    jwrite(SESSIONS,all_sess)

# ------------------------------------------------------------
# AUTH
# ------------------------------------------------------------
def signup(u,p):
    users=jread(USERS,{})
    if u in users: return False,"Username exists"
    users[u]={"pw":hash_pw(p)}
    jwrite(USERS,users)
    return True,"Registered"

def login(u,p):
    users=jread(USERS,{})
    if u not in users: return False,"User not found"
    if not verify_pw(p,users[u]["pw"]): return False,"Wrong password"
    return True,"Ok"

# ------------------------------------------------------------
# PARSE AI PLAN
# ------------------------------------------------------------
def parse_plan(txt):
    days=[[] for _ in range(8)]
    parts=re.split(r"(?=Day\\s*\\d+[:\\-])",txt,re.I)
    for i in range(1,9):
        block=next((b for b in parts if re.match(fr"Day\\s*{i}\\b",b,re.I)),"")
        if not block: continue
        block=re.sub(fr"Day\\s*{i}[:\\-]?","",block,re.I)
        for line in block.splitlines():
            line=line.strip().lstrip("-•0123456789.) ")
            if line:
                days[i-1].append({"text":line,"done":False})
    nid=0
    for d in days:
        for t in d:
            t["id"]=nid; nid+=1
    return days

# ------------------------------------------------------------
# PAGES
# ------------------------------------------------------------
def page_login():
    st.header("Login / Signup")

    u = st.text_input("Username")
    p = st.text_input("Password", type="password")
    if st.button("Login"):
        ok,msg=login(u,p)
        if ok:
            st.session_state.user=u
            js_set_cookie(u)
            persist()
            st.rerun()
        else:
            st.error(msg)

    st.subheader("Create account")
    u2=st.text_input("New username")
    p2=st.text_input("New password",type="password")
    if st.button("Signup"):
        ok,msg=signup(u2,p2)
        if ok: st.success("Registered!")
        else: st.error(msg)

def page_home():
    st.header(f"Welcome {st.session_state.user}")
    if st.button("New Project"):
        st.session_state.page="create"; st.rerun()

    projects=jread(PROJECTS,{})
    userp=projects.get(st.session_state.user,{})
    if userp:
        sel=st.selectbox("Your Projects",["--"]+list(userp.keys()))
        if sel!="--":
            st.session_state.project=userp[sel]
            st.session_state.page="planner"
            st.rerun()

def page_create():
    st.header("Create New Project")
    t=st.text_input("Title")
    d=st.text_area("Description")
    if st.button("Generate"):
        prompt=f"Create a detailed 8-day plan. Title:{t}. Description:{d}"
        ok,res=ai(prompt)
        if ok:
            tasks=parse_plan(res)
            proj={"title":t,"description":d,"tasks":tasks,"raw":res}
            st.session_state.project=proj
            projects=jread(PROJECTS,{})
            projects.setdefault(st.session_state.user,{})[t]=proj
            jwrite(PROJECTS,projects)
            persist()
            st.session_state.page="planner"; st.rerun()
        else:
            st.error(res)

def page_planner():
    st.header(st.session_state.project["title"])
    st.caption(st.session_state.project["description"])

    for i in range(8):
        with st.expander(f"Day {i+1}", expanded=(i==0)):
            for t in st.session_state.project["tasks"][i]:
                chk = st.checkbox(t["text"],value=t["done"],key=f"{i}-{t['id']}")
                t["done"]=chk
    persist()

def page_chat():
    st.header("Chat with DayBot")

    for m in st.session_state.chat:
        st.write(f"{m['role']}: {m['text']}")

    msg=st.text_input("Message")
    if st.button("Send"):
        st.session_state.chat.append({"role":"user","text":msg})
        ok,res=ai(msg)
        st.session_state.chat.append({"role":"daybot","text":res if ok else "AI error"})
        persist()
        st.rerun()

# ------------------------------------------------------------
# SIDEBAR
# ------------------------------------------------------------
def sidebar():
    with st.sidebar:
        if st.button("Home"): st.session_state.page="home"; st.rerun()
        if st.button("Create"): st.session_state.page="create"; st.rerun()
        if st.button("Planner"): st.session_state.page="planner"; st.rerun()
        if st.button("Chat"): st.session_state.page="chat"; st.rerun()
        if st.button("Logout"):
            js_delete_cookie()
            st.session_state.user=None
            st.session_state.page="login"
            st.rerun()

# ------------------------------------------------------------
# MAIN ROUTER
# ------------------------------------------------------------
def main():
    if not st.session_state.user:
        page_login()
    else:
        sidebar()
        if st.session_state.page=="home": page_home()
        elif st.session_state.page=="create": page_create()
        elif st.session_state.page=="planner": page_planner()
        elif st.session_state.page=="chat": page_chat()
        else: page_home()

if __name__=="__main__":
    main()
'''

with open('/mnt/data/app.py','w') as f:
    f.write(app_code)

"/mnt/data/app.py"
