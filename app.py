
# DayByDay Streamlit App (Full Functional Version)
# Clean, maintainable, cookie-based login, planner, chat, AI

import os, json, re, uuid, bcrypt
from pathlib import Path
from datetime import datetime, timedelta
import streamlit as st
from dotenv import load_dotenv

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
    tmp=p.with_suffix(".tmp"); tmp.write_text(json.dumps(obj,indent=2)); tmp.replace(p)

for f in [USERS,PROJECTS,SESSIONS]:
    if not f.exists(): jwrite(f,{})

def hash_pw(p): return bcrypt.hashpw(p.encode(),bcrypt.gensalt()).decode()
def verify_pw(p,h): 
    try: return bcrypt.checkpw(p.encode(),h.encode())
    except: return False

COOKIE="daybyday_user"

def js(msg):
    st.markdown(msg, unsafe_allow_html=True)

def set_cookie(u,hours=24):
    exp=(datetime.utcnow()+timedelta(hours=hours)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    js(f"<script>document.cookie='{COOKIE}={u}; expires={exp}; path=/';</script>")

def clear_cookie():
    js(f"<script>document.cookie='{COOKIE}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';</script>")

def init_cookie_listener():
    js("""
<script>
window.addEventListener("message",(e)=>{
    if(e.data.cookieName){
        const k="cookie_"+e.data.cookieName;
        const v=e.data.cookieValue;
        window.parent.postMessage({streamlitRunOnSave:true,[k]:v},"*");
    }
});
</script>
""")

def read_cookie():
    key="cookie_"+COOKIE
    val=st.session_state.get(key)
    js(f"""
<script>
const v=document.cookie.split("; ").find(r=>r.startsWith("{COOKIE}="))?.split("=")[1];
window.parent.postMessage({{cookieName:"{COOKIE}",cookieValue:v}},"*");
</script>
""")
    return val

init_cookie_listener()

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
        return True,r.text
    except:
        return False,"AI error"

if "page" not in st.session_state: st.session_state.page="login"
if "user" not in st.session_state: st.session_state.user=None
if "project" not in st.session_state:
    st.session_state.project={"title":"","description":"","tasks":[[] for _ in range(8)],"raw":""}
if "chat" not in st.session_state: st.session_state.chat=[]

cu=read_cookie()
if cu and not st.session_state.user:
    sess=jread(SESSIONS,{})
    st.session_state.user=cu
    if cu in sess:
        st.session_state.project=sess[cu].get("project",st.session_state.project)
        st.session_state.chat=sess[cu].get("chat",[])
        st.session_state.page=sess[cu].get("page","home")

def persist():
    u=st.session_state.user
    if not u: return
    all=jread(SESSIONS,{})
    all[u]={"project":st.session_state.project,"chat":st.session_state.chat,"page":st.session_state.page}
    jwrite(SESSIONS,all)

def signup(u,p):
    users=jread(USERS,{})
    if u in users: return False,"Username exists"
    users[u]={"pw":hash_pw(p)}
    jwrite(USERS,users)
    return True,"OK"

def login(u,p):
    users=jread(USERS,{})
    if u not in users: return False,"User not found"
    if not verify_pw(p,users[u]["pw"]): return False,"Wrong password"
    return True,"OK"

def parse_plan(txt):
    days=[[] for _ in range(8)]
    parts=re.split(r"(?=Day\s*\d+[:\-])",txt,re.I)
    for i in range(1,9):
        block=next((b for b in parts if re.match(fr"Day\s*{i}\b",b,re.I)),"")
        if not block: continue
        block=re.sub(fr"Day\s*{i}[:\-]?","",block,re.I)
        for line in block.splitlines():
            clean=line.strip().lstrip("-•0123456789.) ").strip()
            if clean: days[i-1].append({"text":clean,"done":False})
    nid=0
    for d in days:
        for t in d:
            t["id"]=nid; nid+=1
    return days

def page_login():
    st.title("Login")
    u=st.text_input("Username")
    p=st.text_input("Password",type="password")
    if st.button("Login"):
        ok,msg=login(u,p)
        if ok:
            st.session_state.user=u
            set_cookie(u)
            persist()
            st.rerun()
        else: st.error(msg)

    st.subheader("Signup")
    u2=st.text_input("New username")
    p2=st.text_input("New password",type="password")
    if st.button("Create account"):
        ok,msg=signup(u2,p2)
        if ok: st.success("Created")
        else: st.error(msg)

def page_home():
    st.title(f"Welcome {st.session_state.user}")
    if st.button("New Project"):
        st.session_state.page="create"; st.rerun()

    projects=jread(PROJECTS,{})
    userp=projects.get(st.session_state.user,{})
    if userp:
        sel=st.selectbox("Projects",["--"]+list(userp.keys()))
        if sel!="--":
            st.session_state.project=userp[sel]
            st.session_state.page="planner"; st.rerun()

def page_create():
    st.title("Create Project")
    t=st.text_input("Title")
    d=st.text_area("Description")
    if st.button("Generate 8-Day Plan"):
        prompt=f"Create an 8-day plan. Title:{t} Description:{d}"
        ok,txt=ai(prompt)
        if ok:
            tasks=parse_plan(txt)
            proj={"title":t,"description":d,"tasks":tasks,"raw":txt}
            st.session_state.project=proj
            projects=jread(PROJECTS,{})
            projects.setdefault(st.session_state.user,{})[t]=proj
            jwrite(PROJECTS,projects)
            persist()
            st.session_state.page="planner"; st.rerun()
        else: st.error(txt)

def page_planner():
    proj=st.session_state.project
    st.title(proj["title"])
    st.caption(proj["description"])
    for i in range(8):
        with st.expander(f"Day {i+1}",expanded=(i==0)):
            for t in proj["tasks"][i]:
                t["done"]=st.checkbox(t["text"],value=t["done"],key=f"{i}-{t['id']}")
    persist()

def page_chat():
    st.title("Chat")
    for m in st.session_state.chat:
        st.write(f"{m['role']}: {m['text']}")
    msg=st.text_input("Message")
    if st.button("Send"):
        st.session_state.chat.append({"role":"user","text":msg})
        ok,rep=ai(msg)
        st.session_state.chat.append({"role":"daybot","text": rep if ok else "AI error"})
        persist()
        st.rerun()

def sidebar():
    with st.sidebar:
        if st.button("Home"): st.session_state.page="home"; st.rerun()
        if st.button("Create"): st.session_state.page="create"; st.rerun()
        if st.button("Planner"): st.session_state.page="planner"; st.rerun()
        if st.button("Chat"): st.session_state.page="chat"; st.rerun()
        if st.button("Logout"):
            clear_cookie()
            st.session_state.user=None
            st.session_state.page="login"
            st.rerun()

def main():
    if not st.session_state.user:
        page_login()
        return
    sidebar()
    p=st.session_state.page
    if p=="home": page_home()
    elif p=="create": page_create()
    elif p=="planner": page_planner()
    elif p=="chat": page_chat()
    else: page_home()

if __name__=="__main__":
    main()
