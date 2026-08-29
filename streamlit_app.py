"""AgentDesk - Agentic RAG 控制台（Streamlit）

顶尖视觉重设计版：深色 AI 控制台主题 + Design Token + 玻璃拟态卡片 + 微交互。
进程内直接调用 app.graph.run_query；无 OPENAI_API_KEY 也能跑（离线 fallback）。
部署：Streamlit Community Cloud，入口 = agentdesk/streamlit_app.py。
"""
from __future__ import annotations

import html
import hashlib
import os
import time
from dataclasses import asdict, is_dataclass

os.chdir(os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import streamlit as st
import streamlit.components.v1 as components


def _load_secrets_into_env() -> None:
    keys = ["OPENAI_API_KEY", "OPENAI_BASE_URL", "CHAT_MODEL", "EMBEDDING_MODEL",
            "TOP_K", "MAX_ITERATIONS", "DELETE_PASSWORD"]
    for k in keys:
        try:
            if k in st.secrets and str(st.secrets[k]).strip():
                os.environ.setdefault(k, str(st.secrets[k]))
        except Exception:
            pass


_load_secrets_into_env()

from app.config import settings  # noqa: E402
from app.rag.indexer import build_index, INDEX_PATH  # noqa: E402
from app.graph.build_graph import run_query  # noqa: E402

st.set_page_config(page_title="AgentDesk · Agentic RAG 控制台",
                   page_icon="🧠", layout="wide", initial_sidebar_state="expanded")

# 强制侧边栏展开：Streamlit 会把折叠状态记在浏览器里，导致部分用户看不到侧边栏。
# 加载后若检测到侧边栏处于折叠（宽度≈0），自动点击展开按钮。
# ============================ 访问控制（登录 + 额度） ============================
_DEMO_PASSWORD_HASH = os.environ.get("DEMO_PASSWORD", "")
_DEMO_SESSION_QUOTA = int(os.environ.get("DEMO_SESSION_QUOTA", "30"))
_DEMO_DAILY_QUOTA = int(os.environ.get("DEMO_DAILY_QUOTA", "100"))
_DEMO_GUEST_UPLOADS = int(os.environ.get("DEMO_GUEST_UPLOADS", "3"))
_DEMO_GUEST_QUERIES = int(os.environ.get("DEMO_GUEST_QUERIES", "10"))

if not _DEMO_PASSWORD_HASH:
    st.error("演示站未配置访问密码（缺少 DEMO_PASSWORD 环境变量），请联系管理员。")
    st.stop()

if not st.session_state.get("_authed"):
    st.markdown(
        "<div style='max-width:420px;margin:12vh auto;text-align:center'>"
        "<div style='font-size:1.4rem;font-weight:800;margin-bottom:8px'>AgentDesk · 演示控制台</div>"
        "<div style='color:#94a3b8;font-size:.85rem;margin-bottom:24px'>请输入访问密码后查看演示</div></div>",
        unsafe_allow_html=True,
    )
    with st.form("login_form"):
        _pw = st.text_input("访问密码", type="password", placeholder="请输入访问密码")
        _sub = st.form_submit_button("登录", type="primary", use_container_width=True)
    if _sub:
        if hashlib.sha256(_pw.encode("utf-8")).hexdigest() == _DEMO_PASSWORD_HASH:
            st.session_state["_authed"] = True
            st.session_state["_mode"] = "member"
            st.session_state["_visitor_id"] = hashlib.sha256(os.urandom(16)).hexdigest()[:16]
            st.rerun()
        else:
            st.error("密码错误，请重试。")
    st.markdown("<div style='text-align:center;color:#64748b;font-size:.8rem;margin:12px 0 8px'>— 或 —</div>", unsafe_allow_html=True)
    _gc1, _gc2 = st.columns([3, 4])
    with _gc1:
        if st.button("访客模式进入", use_container_width=True):
            st.session_state["_authed"] = True
            st.session_state["_mode"] = "guest"
            st.session_state["_visitor_id"] = hashlib.sha256(os.urandom(16)).hexdigest()[:16]
            st.rerun()
    with _gc2:
        st.markdown(
            f"<div style='color:#94a3b8;font-size:.78rem;padding-top:8px'>访客无需密码 · 上传知识库 {_DEMO_GUEST_UPLOADS} 次 · 提问 {_DEMO_GUEST_QUERIES} 次</div>",
            unsafe_allow_html=True,
        )
    st.stop()

try:
    import redis as _redis_mod
except Exception:
    _redis_mod = None


def _quota_redis():
    if _redis_mod is None:
        return None
    _url = os.environ.get("REDIS_URL") or getattr(settings, "redis_url", "") or ""
    if not _url:
        return None
    try:
        return _redis_mod.Redis.from_url(_url, decode_responses=True, socket_timeout=2)
    except Exception:
        return None


def _quota_usage():
    """返回 (本会话剩余, 今日全站剩余)；Redis 不可用时返回 (None, None) 表示不限额。"""
    _r = _quota_redis()
    if _r is None:
        return (None, None)
    _vid = st.session_state.get("_visitor_id") or "anon"
    _day = time.strftime("%Y-%m-%d")
    try:
        _s = int(_r.get(f"demoq:s:{_vid}") or 0)
        _d = int(_r.get(f"demoq:d:{_day}") or 0)
        return (max(_DEMO_SESSION_QUOTA - _s, 0), max(_DEMO_DAILY_QUOTA - _d, 0))
    except Exception:
        return (None, None)


def _quota_consume() -> bool:
    """真实调用成功后扣减额度。"""
    _r = _quota_redis()
    if _r is None:
        return True
    _vid = st.session_state.get("_visitor_id") or "anon"
    _day = time.strftime("%Y-%m-%d")
    try:
        _s = _r.incr(f"demoq:s:{_vid}")
        _d = _r.incr(f"demoq:d:{_day}")
        _r.expire(f"demoq:s:{_vid}", 7 * 86400)
        _r.expire(f"demoq:d:{_day}", 2 * 86400)
        return _s <= _DEMO_SESSION_QUOTA and _d <= _DEMO_DAILY_QUOTA
    except Exception:
        return True


def _guest_usage():
    """返回 (上传/重建剩余, 提问剩余)；Redis 不可用时返回 (None, None)。"""
    _r = _quota_redis()
    if _r is None:
        return (None, None)
    _vid = st.session_state.get("_visitor_id") or "anon"
    try:
        _u = int(_r.get(f"demoq:gu:{_vid}") or 0)
        _q = int(_r.get(f"demoq:gq:{_vid}") or 0)
        return (max(_DEMO_GUEST_UPLOADS - _u, 0), max(_DEMO_GUEST_QUERIES - _q, 0))
    except Exception:
        return (None, None)


def _guest_consume_upload() -> bool:
    _r = _quota_redis()
    if _r is None:
        return True
    _vid = st.session_state.get("_visitor_id") or "anon"
    try:
        _u = _r.incr(f"demoq:gu:{_vid}")
        _r.expire(f"demoq:gu:{_vid}", 7 * 86400)
        return _u <= _DEMO_GUEST_UPLOADS
    except Exception:
        return True


def _guest_consume_query() -> bool:
    _r = _quota_redis()
    if _r is None:
        return True
    _vid = st.session_state.get("_visitor_id") or "anon"
    try:
        _q = _r.incr(f"demoq:gq:{_vid}")
        _r.expire(f"demoq:gq:{_vid}", 7 * 86400)
        return _q <= _DEMO_GUEST_QUERIES
    except Exception:
        return True


components.html(
    """
    <script>
    (function () {
      // 首次进入自动展开一次；之后把控制权交给用户
      try {
        if (!sessionStorage.getItem('ad_sb_init')) {
          sessionStorage.setItem('ad_sb_init', '1');
          var t0 = 0;
          var timer0 = setInterval(function () {
            var sb0 = parent.document.querySelector('section[data-testid="stSidebar"]');
            var btn0 = parent.document.querySelector('[data-testid="stSidebarCollapseButton"] button');
            if (!sb0 || !btn0) {
              if (++t0 > 40) { clearInterval(timer0); }
              return;
            }
            if (sb0.getBoundingClientRect().width < 50) {
              btn0.click();
            } else {
              clearInterval(timer0);
            }
          }, 400);
        }
      } catch (e) {}
      // 侧边栏收起时，显示浮动展开按钮
      var timer = setInterval(function () {
        var sb = parent.document.querySelector('section[data-testid="stSidebar"]');
        var fab = parent.document.getElementById('ad-sb-fab');
        if (!sb) { return; }
        if (!fab) {
          fab = parent.document.createElement('button');
          fab.id = 'ad-sb-fab';
          fab.title = '展开侧边栏';
          fab.innerHTML = '&#9776;';
          fab.style.cssText = 'position:fixed;top:64px;left:8px;z-index:1001;width:36px;height:36px;border-radius:10px;border:1px solid #334155;background:#0f172a;color:#e2e8f0;font-size:16px;line-height:1;cursor:pointer;display:none;box-shadow:0 2px 10px rgba(0,0,0,.4);';
          fab.addEventListener('click', function () {
            var ctrl = parent.document.querySelector('[data-testid="stSidebarCollapseButton"] button');
            if (ctrl) { ctrl.click(); }
          });
          parent.document.body.appendChild(fab);
        }
        fab.style.display = (sb.getBoundingClientRect().width < 50) ? 'block' : 'none';
      }, 500);
    })();
    </script>
    """,
    height=0,
)

# ============================ Design System (CSS) ============================
st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;600&display=swap');

      :root{
        --bg:#0a0e1a; --bg2:#0f1424; --surface:rgba(255,255,255,.045);
        --surface-2:rgba(255,255,255,.07); --stroke:rgba(255,255,255,.10);
        --stroke-2:rgba(255,255,255,.16);
        --ink:#eef2ff; --muted:#9aa6c4; --faint:#6b7596;
        --brand:#7c5cff; --brand2:#22d3ee; --accent:#f472b6;
        --ok:#34d399; --warn:#fbbf24; --bad:#fb7185;
        --r-s:10px; --r-m:16px; --r-l:22px;
        --grad:linear-gradient(120deg,#7c5cff 0%,#5b8cff 45%,#22d3ee 100%);
      }

      /* —— 背景：深空 + 双径向光晕 + 细网格 —— */
      .stApp{
        background:
          radial-gradient(1100px 620px at 12% -8%, rgba(124,92,255,.22), transparent 60%),
          radial-gradient(900px 560px at 105% 8%, rgba(34,211,238,.16), transparent 55%),
          linear-gradient(180deg,#0a0e1a 0%, #0b1020 60%, #0a0e1a 100%);
        color:var(--ink);
        font-family:'Inter',system-ui,-apple-system,'Segoe UI',sans-serif;
      }
      .block-container{padding-top:2.0rem; padding-bottom:3rem; max-width:1240px;}
      .stApp:before{
        content:""; position:fixed; inset:0; pointer-events:none; opacity:.5; z-index:0;
        background-image:linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),
          linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px);
        background-size:46px 46px; mask-image:radial-gradient(circle at 50% 0%,#000,transparent 75%);
      }
      h1,h2,h3,h4,p,span,div,label{font-family:'Inter',sans-serif;}
      a{color:var(--brand2);}
      ::selection{background:rgba(124,92,255,.35);}

      /* —— Hero —— */
      .hero{position:relative; overflow:hidden; border:1px solid var(--stroke);
        border-radius:var(--r-l); padding:30px 32px; margin-bottom:20px;
        background:linear-gradient(135deg, rgba(124,92,255,.20), rgba(34,211,238,.10) 55%, rgba(255,255,255,.02));
        box-shadow:0 30px 80px -40px rgba(91,140,255,.55), inset 0 1px 0 rgba(255,255,255,.08);}
      .hero:after{content:""; position:absolute; width:340px; height:340px; right:-90px; top:-150px;
        background:conic-gradient(from 120deg,#7c5cff,#22d3ee,#f472b6,#7c5cff); filter:blur(60px);
        opacity:.30; border-radius:50%; animation:spin 18s linear infinite;}
      @keyframes spin{to{transform:rotate(360deg);}}
      .hero h1{margin:0; font-size:2.0rem; font-weight:800; letter-spacing:-.5px;
        background:linear-gradient(90deg,#fff,#cdd6ff 60%,#9fe9ff); -webkit-background-clip:text;
        background-clip:text; -webkit-text-fill-color:transparent;}
      .hero p{margin:.5rem 0 0; color:#c5cdf0; font-size:.96rem; max-width:760px; line-height:1.5;}
      .hero .chips{margin-top:16px; display:flex; gap:8px; flex-wrap:wrap;}
      .chip{display:inline-flex; align-items:center; gap:7px; padding:6px 13px; border-radius:999px;
        font-size:.78rem; font-weight:600; border:1px solid var(--stroke-2);
        background:var(--surface-2); color:#d7dcf5; backdrop-filter:blur(8px);}
      .dot{width:8px;height:8px;border-radius:50%;box-shadow:0 0 12px currentColor;}

      /* —— 区块标题 —— */
      .eyebrow{display:flex; align-items:center; gap:9px; margin:6px 0 12px;
        font-size:.74rem; font-weight:700; letter-spacing:.16em; text-transform:uppercase; color:var(--faint);}
      .eyebrow:before{content:""; width:18px; height:2px; border-radius:2px; background:var(--grad);}

      /* —— 通用卡片 —— */
      .card{position:relative; border:1px solid var(--stroke); border-radius:var(--r-m);
        background:var(--surface); backdrop-filter:blur(10px); padding:16px 18px; margin-bottom:14px;
        box-shadow:0 18px 40px -30px rgba(0,0,0,.8), inset 0 1px 0 rgba(255,255,255,.05);
        transition:transform .18s ease, border-color .18s ease, box-shadow .18s ease;}
      .card:hover{transform:translateY(-2px); border-color:var(--stroke-2);
        box-shadow:0 26px 60px -34px rgba(91,140,255,.5), inset 0 1px 0 rgba(255,255,255,.07);}

      /* —— KPI —— */
      .kpi{border:1px solid var(--stroke); border-radius:var(--r-m); padding:16px 18px; height:100%;
        background:linear-gradient(180deg,var(--surface-2),var(--surface)); position:relative; overflow:hidden;}
      .kpi .k-ico{font-size:1.05rem; opacity:.95;}
      .kpi .k-lab{color:var(--muted); font-size:.76rem; font-weight:600; letter-spacing:.04em; margin-top:6px;}
      .kpi .k-val{font-size:1.95rem; font-weight:800; letter-spacing:-.5px; line-height:1.1; margin-top:2px;
        font-variant-numeric:tabular-nums;}
      .kpi .k-sub{font-size:.74rem; color:var(--faint); margin-top:3px;}
      .kpi:after{content:""; position:absolute; left:0; bottom:0; height:3px; width:100%; background:var(--grad); opacity:.85;}

      /* —— Faithfulness 环形仪表 —— */
      .gauge-wrap{display:flex; align-items:center; gap:16px;}
      .gauge{--p:0; width:92px; height:92px; border-radius:50%; flex:0 0 auto; position:relative;
        background:conic-gradient(var(--gc,#34d399) calc(var(--p)*1%), rgba(255,255,255,.08) 0);
        display:grid; place-items:center; box-shadow:0 0 0 1px var(--stroke) inset;}
      .gauge:before{content:""; position:absolute; inset:9px; border-radius:50%; background:#0c1122;
        box-shadow:inset 0 1px 0 rgba(255,255,255,.06);}
      .gauge b{position:relative; font-size:1.25rem; font-weight:800; font-variant-numeric:tabular-nums;}

      /* —— 引用 / pill —— */
      .pill{display:inline-flex; align-items:center; gap:6px; padding:5px 11px; border-radius:999px;
        font-size:.74rem; font-weight:600; margin:0 6px 6px 0; border:1px solid var(--stroke-2);
        background:rgba(124,92,255,.14); color:#d9d2ff; font-family:'JetBrains Mono',monospace;}
      .pill.tool{background:rgba(52,211,153,.14); color:#b8f5dd;}
      .pill.bad{background:rgba(251,113,133,.14); color:#ffc6cf;}

      /* —— 证据卡 —— */
      .ev{border:1px solid var(--stroke); border-radius:var(--r-m); padding:14px 16px; margin-bottom:12px;
        background:var(--surface); transition:transform .16s ease,border-color .16s ease;}
      .ev:hover{transform:translateX(3px); border-color:var(--stroke-2);}
      .ev-top{display:flex; align-items:center; justify-content:space-between; gap:10px;}
      .ev-id{font-family:'JetBrains Mono',monospace; font-size:.8rem; font-weight:600; color:#a9b6ff;}
      .ev-sc{font-family:'JetBrains Mono',monospace; font-size:.74rem; color:var(--muted);}
      .ev-rank{width:22px;height:22px;border-radius:7px;display:grid;place-items:center;font-size:.72rem;
        font-weight:700; color:#0a0e1a; background:var(--grad); flex:0 0 auto;}
      .bar{height:6px; border-radius:6px; background:rgba(255,255,255,.07); overflow:hidden; margin:10px 0 8px;}
      .bar>span{display:block; height:100%; border-radius:6px; background:var(--grad);
        box-shadow:0 0 14px rgba(124,92,255,.6); animation:grow .6s cubic-bezier(.2,.8,.2,1);}
      @keyframes grow{from{width:0;}}
      .ev-txt{color:#c3cbe6; font-size:.84rem; line-height:1.55;}
      .ans{color:#e9edff; font-size:.95rem; line-height:1.7; white-space:pre-wrap;}

      /* —— 流程时间线 —— */
      .tl{position:relative; margin-left:6px; padding-left:22px;}
      .tl:before{content:""; position:absolute; left:5px; top:6px; bottom:6px; width:2px;
        background:linear-gradient(180deg,#7c5cff,#22d3ee);}
      .node{position:relative; padding:0 0 16px 4px;}
      .node:before{content:""; position:absolute; left:-22px; top:3px; width:13px; height:13px; border-radius:50%;
        background:#0a0e1a; border:2px solid #7c5cff; box-shadow:0 0 0 4px rgba(124,92,255,.12);}
      .node.done:before{background:var(--grad); border-color:transparent;}
      .node .n-t{font-size:.86rem; font-weight:700; color:#e7ebff;}
      .node .n-d{font-size:.78rem; color:var(--muted); margin-top:3px; line-height:1.5;}

      /* —— Streamlit 控件覆写 —— */
      section[data-testid="stSidebar"]{background:linear-gradient(180deg,#0b0f1e,#0a0e1a);
        border-right:1px solid var(--stroke);}
      section[data-testid="stSidebar"] .block-container{padding-top:1.4rem;}
      .stTextInput input{background:var(--surface-2)!important; color:var(--ink)!important;
        border:1px solid var(--stroke-2)!important; border-radius:14px!important; height:52px; font-size:.95rem;
        padding:0 16px!important;}
      .stTextInput input::placeholder{color:var(--faint)!important;}
      .stTextInput input:focus{border-color:var(--brand)!important;
        box-shadow:0 0 0 3px rgba(124,92,255,.25)!important;}
      .stTextInput label{color:var(--muted)!important; font-weight:600!important;}
      .stButton>button{border-radius:13px; border:1px solid var(--stroke-2); font-weight:600;
        background:var(--surface-2); color:#dfe4ff; transition:all .16s ease;}
      .stButton>button:hover{border-color:var(--brand); color:#fff; transform:translateY(-1px);
        background:rgba(124,92,255,.16);}
      .stButton>button[kind="primary"]{background:var(--grad); border:none; color:#fff; height:50px;
        font-weight:700; letter-spacing:.02em; box-shadow:0 14px 34px -14px rgba(124,92,255,.85);}
      .stButton>button[kind="primary"]:hover{filter:brightness(1.08); transform:translateY(-1px);}
      div[data-testid="stExpander"]{border:1px solid var(--stroke)!important; border-radius:14px!important;
        background:var(--surface)!important; overflow:hidden;}
      div[data-testid="stExpander"] summary{color:#cdd5f5!important; font-weight:600!important;}
      hr{border-color:var(--stroke)!important;}
      #MainMenu,header[data-testid="stHeader"],footer{visibility:hidden;}
      .stApp > div{z-index:1;}
    </style>
    """,
    unsafe_allow_html=True,
)

# ============================ 启动：建索引（自举 + 缓存） ============================
_META_PATH = os.path.join(os.path.dirname(INDEX_PATH), "index_meta.json")


def _emb_signature() -> dict:
    # 索引指纹：embedding 维度由「是否真实模型 + 模型名」决定，变了就必须重建
    # 同时记录 data/docs 下的文件清单（名称+大小+修改时间），新增/修改文档也会触发重建
    files = []
    try:
        for name in sorted(os.listdir(os.path.join("data", "docs"))):
            p = os.path.join("data", "docs", name)
            if os.path.isfile(p):
                files.append((name, os.path.getsize(p), int(os.path.getmtime(p))))
    except Exception:
        pass
    return {"use_llm": bool(settings.use_llm),
            "model": settings.embedding_model if settings.use_llm else "offline-hash",
            "files": files}


def _read_meta():
    try:
        import json
        with open(_META_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _rebuild_index() -> int:
    import json
    # 清掉旧维度的 embedding 缓存与检索器单例，确保按当前维度重建（离线 256 ↔ 真实 1024）
    try:
        from app.rag.cache import cache as _c
        getattr(_c, "_mem", {}).clear()
    except Exception:
        pass
    try:
        import app.graph.nodes as _n
        _n._retriever = None
    except Exception:
        pass
    n = len(build_index())
    try:
        os.makedirs(os.path.dirname(_META_PATH), exist_ok=True)
        with open(_META_PATH, "w", encoding="utf-8") as f:
            json.dump(_emb_signature(), f)
    except Exception:
        pass
    return n


@st.cache_resource(show_spinner="冷启动：正在构建知识库索引…")
def ensure_index() -> int:
    docs_dir = os.path.join("data", "docs")
    if not os.path.exists(os.path.join(docs_dir, "plan_AC-100.md")):
        try:
            os.makedirs("eval", exist_ok=True)
            from scripts.gen_corpus import gen
            gen()
        except Exception:
            pass
    # 无索引，或 embedding 指纹变了（离线↔真实模型切换导致维度不匹配）→ 重建
    if not os.path.exists(INDEX_PATH) or _read_meta() != _emb_signature():
        return _rebuild_index()
    try:
        import json
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return len(data) if isinstance(data, list) else len(data.get("chunks", []))
    except Exception:
        return -1


n_chunks = ensure_index()

_toast_msg = st.session_state.pop("_toast", None)
if _toast_msg:
    st.toast(_toast_msg)

NODE_LABELS = {
    "memory_retrieve": ("Memory · Retrieve", "加载短期上下文 + 召回长期记忆"),
    "planner": ("Planner", "查询改写 · multi-query"),
    "retrieval": ("Retrieval", "向量 + BM25 → RRF → Rerank"),
    "tool": ("Tool", "MCP 工具路由"),
    "writer": ("Writer", "带证据生成 · 标注引用"),
    "critic": ("Critic", "faithfulness 反思判定"),
    "memory_write": ("Memory · Write", "抽取演化写入 + 追加短期记忆"),
    "summarize": ("Memory · Summarize", "滚动摘要压缩旧轮次"),
}
# 记忆类型 → (图标, 中文, 颜色)。主区记忆卡片 / 侧栏演化审计共用。
MEM_KIND = {
    "preference": ("⭐", "偏好", "#7c5cff"),
    "fact": ("📌", "事实", "#22d3ee"),
    "event": ("🕒", "事件", "#f472b6"),
}
SAMPLES = [
    "公司A和公司B 2025年营收分别是多少？",
    "知识库里有多少个文档？",
    "(210-205)/205*100",
    "AC-104 这个需求计划讲了什么？",
    "公司的报销政策是怎样的？",
]


def esc(x) -> str:
    return html.escape(str(x))


# ============================ 文档管理（上传清单） ============================
_UPLOADS_META = os.path.join("data", "uploads_meta.json")


def _load_uploads_meta() -> dict:
    import json
    try:
        with open(_UPLOADS_META, "r", encoding="utf-8") as _f:
            _data = json.load(_f)
        return _data if isinstance(_data, dict) else {}
    except Exception:
        return {}


def _save_uploads_meta(meta: dict) -> None:
    import json
    try:
        os.makedirs(os.path.dirname(_UPLOADS_META), exist_ok=True)
        with open(_UPLOADS_META, "w", encoding="utf-8") as _f:
            json.dump(meta, _f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _fmt_size(n: int) -> str:
    n = float(n)
    for _unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or _unit == "GB":
            return f"{int(n)} B" if _unit == "B" else f"{n:.1f} {_unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


# ============================ 侧边栏 ============================
with st.sidebar:
    _is_guest = st.session_state.get("_mode") == "guest"
    _s_left, _d_left = _quota_usage()
    if _is_guest:
        _gu_left, _gq_left = _guest_usage()
        if _d_left is not None:
            st.markdown(
                f"<div class='card' style='margin-bottom:12px'><b style='font-size:.9rem'>访客模式</b>"
                f"<div style='color:var(--faint);font-size:.76rem;margin-top:6px'>上传/重建剩余 {_gu_left} 次 · 提问剩余 {_gq_left} 次 · 全站今日 {_d_left} 次</div></div>",
                unsafe_allow_html=True,
            )
    elif _d_left is not None:
        st.markdown(
            f"<div class='card' style='margin-bottom:12px'><b style='font-size:.9rem'>今日剩余额度</b>"
            f"<div style='color:var(--faint);font-size:.76rem;margin-top:6px'>全站 {_d_left} 次 · 本会话 {_s_left} 次</div></div>",
            unsafe_allow_html=True,
        )
    if st.button("退出登录", use_container_width=True):
        st.session_state["_authed"] = False
        st.rerun()
    st.markdown("<div class='eyebrow'>Console</div>", unsafe_allow_html=True)
    st.markdown("<div style='font-size:1.15rem;font-weight:800;margin:-4px 0 2px'>AgentDesk</div>"
                "<div style='color:var(--muted);font-size:.8rem'>Agentic RAG · 多智能体</div>",
                unsafe_allow_html=True)
    st.markdown("<hr style='margin:14px 0'>", unsafe_allow_html=True)

    live = settings.use_llm
    st.markdown(
        f"<div class='card' style='margin-bottom:12px'>"
        f"<div style='display:flex;align-items:center;gap:9px'>"
        f"<span class='dot' style='color:{'#34d399' if live else '#fbbf24'}'></span>"
        f"<b style='font-size:.9rem'>{'真实大模型' if live else '离线 Fallback'}</b></div>"
        f"<div style='color:var(--faint);font-size:.76rem;margin-top:6px;line-height:1.5'>"
        f"{'已接入 LLM/Embedding API' if live else '哈希向量 + 拼接答案，无需任何 key'}</div></div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:8px'>"
        f"<div class='card' style='margin:0;padding:12px 14px'><div style='color:var(--faint);font-size:.7rem'>向量后端</div>"
        f"<div style='font-weight:700;margin-top:3px'>{esc(settings.vector_backend)}</div></div>"
        f"<div class='card' style='margin:0;padding:12px 14px'><div style='color:var(--faint);font-size:.7rem'>Top-K</div>"
        f"<div style='font-weight:700;margin-top:3px'>{esc(settings.top_k)}</div></div>"
        f"<div class='card' style='margin:0;padding:12px 14px'><div style='color:var(--faint);font-size:.7rem'>反思上限</div>"
        f"<div style='font-weight:700;margin-top:3px'>{esc(settings.max_iterations)}</div></div>"
        f"<div class='card' style='margin:0;padding:12px 14px'><div style='color:var(--faint);font-size:.7rem'>KB chunks</div>"
        f"<div style='font-weight:700;margin-top:3px'>{esc(n_chunks)}</div></div></div>",
        unsafe_allow_html=True,
    )
    st.markdown("<div class='eyebrow' style='margin-top:18px'>记忆身份</div>", unsafe_allow_html=True)
    import uuid as _uuid
    if "mem_sid" not in st.session_state:
        st.session_state["mem_sid"] = _uuid.uuid4().hex
    st.text_input("user_id", value=st.session_state.get("mem_uid", "alice"),
                  key="mem_uid", label_visibility="collapsed", placeholder="user_id（记忆按此隔离）")
    _sid = st.session_state["mem_sid"]
    sc1, sc2 = st.columns([3, 2])
    with sc1:
        st.markdown(f"<div style='color:var(--faint);font-size:.72rem;padding-top:8px'>"
                    f"会话 {esc(_sid[:8])}…</div>", unsafe_allow_html=True)
    with sc2:
        if st.button("新会话", use_container_width=True, key="new_sess"):
            st.session_state["mem_sid"] = _uuid.uuid4().hex
            st.rerun()

    st.markdown("<div class='eyebrow' style='margin-top:18px'>知识库 · 上传文档</div>",
                unsafe_allow_html=True)
    # 上传成功后更换控件 key，强制 Streamlit 重建空控件（仅删 session 键无法清掉浏览器端已选文件）
    _up_key = f"kb_uploader_{st.session_state.get('_up_key', 0)}"
    _up = st.file_uploader(
        "上传文档（txt / md / pdf / docx / xlsx / csv / pptx / html / json / rtf / xml / xls / odt / epub）",
        type=["txt", "md", "pdf", "docx", "xlsx", "csv", "pptx",
              "html", "json", "rtf", "xml", "xls", "odt", "epub"],
        accept_multiple_files=True,
        label_visibility="collapsed",
        key=_up_key,
    )
    if _up:
        saved = []
        _meta = _load_uploads_meta()
        _files_meta = _meta.setdefault("files", {})
        _guest_blocked = _is_guest and (_gu_left is not None and _gu_left <= 0)
        if _guest_blocked:
            st.error("访客上传额度已用完（最多 3 次）。获取密码可继续使用完整功能。")
        for _f in (_up if not _guest_blocked else []):
            _name = os.path.basename(_f.name)
            _dest = os.path.join("data", "docs", _name)
            if _name not in _files_meta and os.path.exists(_dest):
                st.error(f"系统内置文档不可覆盖：{_name}（请重命名后再上传）")
                continue
            try:
                with open(_dest, "wb") as _fh:
                    _fh.write(_f.getbuffer())
                _files_meta[_name] = {
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "size": os.path.getsize(_dest),
                }
                saved.append(_name)
            except Exception as _e:
                st.error(f"保存 {_name} 失败：{_e}")
        _save_uploads_meta(_meta)
        if saved:
            if _is_guest:
                _guest_consume_upload()
            ensure_index.clear()
            st.session_state["_up_key"] = st.session_state.get("_up_key", 0) + 1
            st.rerun()
    if st.button("重建索引", use_container_width=True, key="rebuild_idx"):
        _guest_blocked2 = _is_guest and (_gu_left is not None and _gu_left <= 0)
        if _guest_blocked2:
            st.error("访客上传/重建额度已用完（最多 3 次）。获取密码可继续使用完整功能。")
        else:
            if _is_guest:
                _guest_consume_upload()
            ensure_index.clear()
            _rebuild_index()
        st.session_state["_toast"] = "索引重建完成"
        st.rerun()

    st.markdown("<div class='eyebrow' style='margin-top:18px'>知识库 · 文档管理</div>",
                unsafe_allow_html=True)
    _meta = _load_uploads_meta()
    _files_meta = _meta.get("files", {})
    _doc_rows = []
    try:
        for _name in sorted(os.listdir(os.path.join("data", "docs"))):
            _p = os.path.join("data", "docs", _name)
            if not os.path.isfile(_p):
                continue
            _info = _files_meta.get(_name)
            _doc_rows.append({
                "文件名": _name,
                "大小": _fmt_size(os.path.getsize(_p)),
                "来源": "网页上传" if _info else "系统内置",
                "上传时间": _info["time"] if _info else "—",
            })
    except Exception:
        pass
    if _doc_rows:
        st.dataframe(_doc_rows, hide_index=True, use_container_width=True, height=220)
    else:
        st.caption("知识库为空")
    _deletable = [r["文件名"] for r in _doc_rows if r["来源"] == "网页上传"]
    if _deletable:
        with st.expander("删除网页上传的文档", expanded=False):
            _del_name = st.selectbox("选择要删除的文档", _deletable, key="del_doc")
            _del_key = f"del_pw_{st.session_state.get('_del_key', 0)}"
            _del_pw = st.text_input("删除密码（管理员）", type="password", key=_del_key,
                                    placeholder="输入密码后可删除")
            if st.button("确认删除", use_container_width=True, key="del_btn"):
                _expected = os.environ.get("DELETE_PASSWORD") or _DEMO_PASSWORD_HASH
                if not _expected:
                    st.error("未配置删除密码（DELETE_PASSWORD），请联系管理员。")
                elif hashlib.sha256(_del_pw.encode("utf-8")).hexdigest() != _expected:
                    st.error("删除密码错误，请重试。")
                else:
                    try:
                        os.remove(os.path.join("data", "docs", _del_name))
                        _files_meta.pop(_del_name, None)
                        _save_uploads_meta(_meta)
                        ensure_index.clear()
                        try:
                            _rebuild_index()
                        except Exception as _e:
                            st.warning(f"文件已删除，但索引重建失败（{_e}）；embedding 可用后会自动重建。")
                        st.success(f"已删除：{_del_name}")
                        st.session_state["_toast"] = f"已删除 {_del_name}"
                        st.session_state["_del_key"] = st.session_state.get("_del_key", 0) + 1
                        st.rerun()
                    except Exception as _e:
                        st.error(f"删除失败：{_e}")
    else:
        st.caption("暂无网页上传的文档；系统内置文档不可删除")

    st.markdown("<div class='eyebrow' style='margin-top:18px'>试一试</div>", unsafe_allow_html=True)
    for q in SAMPLES:
        if st.button(q, use_container_width=True, key=f"s_{q}"):
            st.session_state["qbox"] = q
            st.session_state["_autorun"] = True
    with st.expander("架构 / 流程"):
        st.markdown(
            "**编排（LangGraph）**：planner → retrieval → tool → writer → critic；"
            "critic 不达标且未超轮数则回 retrieval 重试。\n\n"
            "**检索**：多查询改写 → 向量 + BM25 → RRF 融合 → Rerank。\n\n"
            "**工具层**：MCP 风格 registry（AST 白名单计算器 / kb_stats）。\n\n"
            "**兜底**：langgraph 不可用时顺序等价执行。"
        )

# ============================ Hero ============================
st.markdown(
    f"""
    <div class="hero">
      <h1>Agentic RAG 控制台</h1>
      <p>LangGraph 编排的多智能体检索增强系统 · 把<b>查询改写 → 混合检索 → 工具调用 → 带证据生成 → 反思重试</b>的全过程实时可视化。</p>
      <div class="chips">
        <span class="chip"><span class="dot" style="color:{'#34d399' if settings.use_llm else '#fbbf24'}"></span>{'真实大模型' if settings.use_llm else '离线 Fallback'}</span>
        <span class="chip">🧩 混合检索 向量+BM25+Rerank</span>
        <span class="chip">🛠️ MCP 工具层</span>
        <span class="chip">🔁 Critic 反思循环</span>
        <span class="chip">📚 {esc(n_chunks)} chunks</span>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ============================ 提问区 ============================
st.markdown("<div class='eyebrow'>Ask the knowledge base</div>", unsafe_allow_html=True)

# —— Chat 模型选择（放主区，醒目）——
# 运行时覆盖 settings.chat_model；app/llm.py:chat() 每次现读该值，
# 故一个开关同时作用于 改写(planner)/生成(writer)/裁判(critic) 三处，无需改图或穿参。
_live = settings.use_llm
_PRESETS = ["Qwen/Qwen2.5-7B-Instruct", "Qwen/Qwen2.5-14B-Instruct",
            "Qwen/Qwen2.5-32B-Instruct", "Qwen/Qwen2.5-72B-Instruct",
            "deepseek-ai/DeepSeek-V3", "自定义…"]
_cur = st.session_state.get("chat_model", settings.chat_model)
_opts = _PRESETS if _cur in _PRESETS else [_cur] + _PRESETS
m_lab, m_sel, m_cus = st.columns([1, 2, 2])
with m_lab:
    st.markdown("<div style='padding-top:10px;color:var(--muted);font-weight:600;font-size:.9rem'>🧠 Chat 模型</div>",
                unsafe_allow_html=True)
with m_sel:
    _pick = st.selectbox("chat 模型", _opts,
                         index=_opts.index(_cur) if _cur in _opts else 0,
                         label_visibility="collapsed", disabled=not _live)
with m_cus:
    if _pick == "自定义…":
        _pick = st.text_input("自定义模型名", value=("" if _cur in _PRESETS else _cur),
                              placeholder="如 Qwen/Qwen2.5-72B-Instruct",
                              label_visibility="collapsed", disabled=not _live).strip()
    else:
        _hint = ("影响 改写/生成/裁判三处 · 7B 易把数字写崩，建议 32B+" if _live
                 else "离线 fallback 不调用大模型，切换无效（需在 .env 配 key）")
        st.markdown(f"<div style='padding-top:11px;color:var(--faint);font-size:.74rem'>{_hint}</div>",
                    unsafe_allow_html=True)
_pick = _pick or settings.chat_model
if _live:
    settings.chat_model = _pick               # chat() 每次现读 → 立即生效
    os.environ["CHAT_MODEL"] = _pick
st.session_state["chat_model"] = settings.chat_model

# —— 示例问题（点击即问；放输入框上方，省去面试官打字）——
# 置于输入行之前：按钮在本次 rerun 先于下方 pop("pending_q") 执行，故点击当次即填入并运行。
_DEMOS = [
    ("📊 多公司营收", "公司A和公司B 2025年营收分别是多少？"),
    ("🔢 计算器工具", "(210-205)/205*100"),
    ("🗂️ 知识库统计", "知识库里有多少个文档？"),
    ("📄 套餐 SLA", "AC-110 套餐的 SLA 可用性是多少？"),
    ("📑 报销政策", "公司的报销政策是怎样的？"),
]
st.markdown("<div style='color:var(--faint);font-size:.75rem;margin:2px 0 7px'>示例 · 点击即问</div>",
            unsafe_allow_html=True)
_dc = st.columns(len(_DEMOS))
for _i, (_lab, _q) in enumerate(_DEMOS):
    if _dc[_i].button(_lab, key=f"demo_{_i}", use_container_width=True):
        st.session_state["qbox"] = _q          # 直接写入输入框的 state（key 绑定）
        st.session_state["_autorun"] = True    # 标记：本次点击需自动运行一次

c_in, c_btn = st.columns([4, 1])
with c_in:
    # 用 key 绑定 session_state['qbox']；示例/侧边栏按钮已在上方写好它，
    # 故无需 value=（value= 在按钮场景下常不回显，正是“点了没反应”的根因）。
    def _on_query_enter() -> None:
        # 文本输入按回车才会提交值；提交时顺便标记自动运行，
        # 这样“输入 → 回车”即可直接出答案，无需再点运行。
        st.session_state["_autorun"] = True

    # 查询成功后在下一轮把输入框清空（必须在控件实例化之前设置，否则 Streamlit 报错）
    if st.session_state.pop("_clear_qbox", False):
        st.session_state["qbox"] = ""

    query = st.text_input("向知识库提问", key="qbox", label_visibility="collapsed",
                          placeholder="例如：公司A和公司B 2025年营收分别是多少？",
                          on_change=_on_query_enter)
    st.markdown(
        "<div style='color:var(--faint);font-size:.72rem;margin-top:4px'>"
        "输入后按 Enter 直接运行；或先按 Enter 提交，再点 ⚡ 运行。</div>",
        unsafe_allow_html=True,
    )
with c_btn:
    go = st.button("⚡ 运行", type="primary", use_container_width=True)

_autorun = st.session_state.pop("_autorun", False)
# ============================ 运行与渲染 ============================
_last = None
if (go or _autorun) and query.strip():
    _is_guest = st.session_state.get("_mode") == "guest"
    _s_left, _d_left = _quota_usage()
    if _is_guest:
        _gu_left, _gq_left = _guest_usage()
        if _gq_left is not None and _gq_left <= 0:
            st.warning(f"访客提问额度已用完（最多 {_DEMO_GUEST_QUERIES} 次）。获取密码可继续使用。")
            st.stop()
        _s_left = None if _s_left is None else 999
    if (_s_left is not None and _s_left <= 0) or (_d_left is not None and _d_left <= 0):
        st.warning(
            f"演示额度已用完（本会话剩余 {_s_left} 次，今日全站剩余 {_d_left} 次）。"
            "如需更多额度，请联系作者。"
        )
        st.stop()
    try:
        with st.spinner("Agent 编排执行中：记忆召回 → 改写 → 检索 → 工具 → 生成 → 反思 → 记忆写入…"):
            state = run_query(
                query.strip(),
                user_id=(st.session_state.get("mem_uid") or "alice").strip(),
                session_id=st.session_state.get("mem_sid"),
            )
        if _is_guest:
            _guest_consume_query()
        _quota_consume()
    except Exception as _e:
        st.error(f"运行出错（多为模型接口超时/报错）：{type(_e).__name__}: {_e}　"
                 "可在上方换一个更稳的模型重试。")
        st.stop()

    # 把本次结果存入 session_state：上传/重建索引等 rerun 后仍能继续展示答案与分析
    _last = {
        "answer": state.get("answer", ""),
        "verify": state.get("verify", {}) or {},
        "iterations": state.get("iterations", 0),
        "evidence": state.get("evidence", []) or [],
        "tool_results": state.get("tool_results", []) or [],
        "trace": state.get("trace", []) or [],
        "citations": state.get("citations", []) or [],
        "recalled_memories": state.get("recalled_memories", []) or [],
        "memory_writes": state.get("memory_writes", []) or [],
        "working_memory": state.get("working_memory"),
    }
    st.session_state["last_state"] = _last
    st.session_state["_clear_qbox"] = True
    st.rerun()
else:
    _last = st.session_state.get("last_state")

if _last is not None:
    answer = _last.get("answer", "")
    verify = _last.get("verify", {}) or {}
    iterations = _last.get("iterations", 0)
    evidence = _last.get("evidence", []) or []
    tool_results = _last.get("tool_results", []) or []
    trace = _last.get("trace", []) or []
    cites = _last.get("citations", []) or []
    recalled = _last.get("recalled_memories", []) or []
    mem_writes = _last.get("memory_writes", []) or []
    wm = _last.get("working_memory") or {}

    score = float(verify.get("score", 0) or 0)
    faithful = bool(verify.get("faithful"))
    pct = max(0, min(100, int(round(score * 100))))
    gc = "#34d399" if faithful else ("#fbbf24" if score >= 0.4 else "#fb7185")

    # —— KPI 行 —— 
    k1, k2, k3, k4 = st.columns(4)
    kpis = [
        (k1, "🎯", "Faithfulness", f"{score:.2f}", ("证据支撑达标" if faithful else "未达标")),
        (k2, "🔁", "反思轮数", f"{iterations}", "critic retry loop"),
        (k3, "📎", "命中证据", f"{len(evidence)}", "RRF + Rerank top-k"),
        (k4, "⚖️", "评判方式", f"{verify.get('method','-')}", "LLM-judge / 启发式"),
    ]
    for col, ico, lab, val, sub in kpis:
        col.markdown(
            f"<div class='kpi'><div class='k-ico'>{ico}</div><div class='k-lab'>{lab}</div>"
            f"<div class='k-val'>{esc(val)}</div><div class='k-sub'>{esc(sub)}</div></div>",
            unsafe_allow_html=True,
        )

    # —— 对话历史（短期记忆 buffer，体现多轮）——
    _wm = wm or {}
    _msgs = _wm.get("messages", []) or []
    _summary = _wm.get("running_summary", "")
    if _msgs or _summary:
        st.markdown("<div class='eyebrow'>对话历史 · 本会话短期记忆</div>", unsafe_allow_html=True)
        bubbles = ""
        if _summary:
            bubbles += (f"<div style='border-left:3px solid var(--brand);padding:8px 12px;margin-bottom:8px;"
                        f"background:rgba(124,92,255,.08);border-radius:8px'>"
                        f"<div style='color:var(--faint);font-size:.72rem;margin-bottom:3px'>🗜️ 滚动摘要（已压缩的旧轮次）</div>"
                        f"<div style='color:#c3cbe6;font-size:.84rem;line-height:1.5'>{esc(_summary)}</div></div>")
        for m in _msgs:
            is_user = m.get("role") == "user"
            who = "🧑 用户" if is_user else "🤖 助手"
            align = "flex-end" if is_user else "flex-start"
            bg = "rgba(124,92,255,.16)" if is_user else "var(--surface-2)"
            bubbles += (f"<div style='display:flex;justify-content:{align};margin:6px 0'>"
                        f"<div style='max-width:78%;border:1px solid var(--stroke-2);background:{bg};"
                        f"border-radius:14px;padding:9px 13px'>"
                        f"<div style='color:var(--faint);font-size:.68rem;margin-bottom:3px'>{who}</div>"
                        f"<div style='color:#e9edff;font-size:.85rem;line-height:1.5;white-space:pre-wrap'>"
                        f"{esc((m.get('content','') or '')[:500])}</div></div></div>")
        st.markdown(f"<div class='card'>{bubbles}</div>", unsafe_allow_html=True)
        st.markdown(f"<div style='color:var(--faint);font-size:.72rem;margin:-4px 0 6px'>"
                    f"共 {_wm.get('round_count', 0)} 轮 · 会话 "
                    f"{esc((st.session_state.get('mem_sid') or '')[:8])}… · 短期记忆随会话累积，"
                    f"超阈值自动压缩为摘要</div>", unsafe_allow_html=True)

    st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
    main, side = st.columns([1.4, 1], gap="large")

    with main:
        st.markdown("<div class='eyebrow'>Answer</div>", unsafe_allow_html=True)
        st.markdown(
            f"<div class='card'><div class='gauge-wrap'>"
            f"<div class='gauge' style='--p:{pct};--gc:{gc}'><b>{pct}%</b></div>"
            f"<div><div style='font-weight:700;color:#fff'>{'✅ 可信回答' if faithful else '⚠️ 证据支撑不足'}</div>"
            f"<div style='color:var(--muted);font-size:.8rem;margin-top:4px'>faithfulness = 答案被检索证据支撑的比例</div></div>"
            f"</div><div class='ans' style='margin-top:14px'>{esc(answer) or '（无答案）'}</div></div>",
            unsafe_allow_html=True,
        )

        # —— 可信度推导（这次怎么算的）：评判方式 / 依据 / 计算 / 阈值 + 标尺 ——
        _method = verify.get("method", "-")
        _detail = verify.get("detail", {}) or {}
        _reason = str(verify.get("reason", "") or "")
        _used_tool = _detail.get(
            "used_tool", any((r.get("out", {}) or {}).get("ok") for r in tool_results))
        if _method == "heuristic":
            _nm, _na = _detail.get("n_match", "?"), _detail.get("n_answer", "?")
            _calc = (f"启发式：命中词 <b>{_nm}</b> ÷ 答案词 <b>{_na}</b> = <b>{score:.2f}</b>"
                     "　<span style='color:var(--faint)'>score = |答案∩(证据∪工具)| / |答案|</span>")
        elif _method == "llm":
            _calc = (f"LLM 裁判逐条核对 → <b>{score:.2f}</b>"
                     + (f"<br><span style='color:var(--faint)'>裁判说明：{esc(_reason)}</span>"
                        if _reason else ""))
        else:
            _calc = f"score = <b>{score:.2f}</b>"
        _verdict = ("✅ score ≥ 0.6 → 可信回答" if faithful
                    else "⚠️ score &lt; 0.6 → 证据支撑不足")
        _ruler = (
            "<div style='position:relative;height:24px;margin-top:10px'>"
            "<div style='position:absolute;top:7px;left:0;right:0;height:10px;border-radius:5px;"
            "background:linear-gradient(90deg,#fb7185 0 40%,#fbbf24 40% 60%,#34d399 60% 100%)'></div>"
            "<div style='position:absolute;top:3px;left:60%;width:2px;height:18px;background:#e9edff;opacity:.85'></div>"
            f"<div style='position:absolute;top:4px;left:{pct}%;transform:translateX(-50%);width:14px;height:14px;"
            f"border-radius:50%;background:{gc};border:2px solid #0a0e1a;box-shadow:0 0 8px {gc}'></div></div>"
            "<div style='position:relative;height:14px;font-size:.64rem;color:var(--faint)'>"
            "<span style='position:absolute;left:0'>0.0</span>"
            "<span style='position:absolute;left:60%;transform:translateX(-50%)'>0.6 阈值</span>"
            "<span style='position:absolute;right:0'>1.0</span></div>"
        )
        st.markdown(
            "<div class='eyebrow' style='margin-top:6px'>可信度推导 · 这次怎么算的</div>"
            "<div class='card'><div style='font-size:.82rem;color:#c3cbe6;line-height:1.95'>"
            f"① 评判方式：<b>{'LLM 裁判' if _method=='llm' else ('启发式兜底' if _method=='heuristic' else esc(_method))}</b><br>"
            f"② 依据：检索证据 <b>{len(evidence)}</b> 条 · 工具结果 <b>{'有' if _used_tool else '无'}</b><br>"
            f"③ 计算：{_calc}<br>"
            f"④ 阈值判定：{_verdict}</div>"
            f"{_ruler}</div>",
            unsafe_allow_html=True,
        )

        if cites:
            st.markdown("<div class='eyebrow'>Citations</div>", unsafe_allow_html=True)
            st.markdown("".join(f"<span class='pill'>🔖 {esc(c)}</span>" for c in cites),
                        unsafe_allow_html=True)

        if tool_results:
            st.markdown("<div class='eyebrow'>Tool calls</div>", unsafe_allow_html=True)
            for r in tool_results:
                out = r.get("out", {}) or {}
                ok = out.get("ok")
                cls = "tool" if ok else "bad"
                txt = out.get("result", out.get("error", ""))
                st.markdown(
                    f"<div class='card' style='padding:13px 16px'>"
                    f"<span class='pill {cls}'>{'✓' if ok else '✕'} {esc(r.get('tool'))} · via {esc(out.get('via','local'))}</span>"
                    f"<div style='font-family:JetBrains Mono,monospace;font-size:.84rem;color:#dfe6ff;margin-top:8px'>{esc(txt)}</div></div>",
                    unsafe_allow_html=True,
                )

        if recalled or mem_writes:
            st.markdown("<div class='eyebrow'>Memory · 本轮记忆</div>", unsafe_allow_html=True)
            if recalled:
                cards = ""
                for m in recalled:
                    ico, lab, col = MEM_KIND.get(m.get("kind"), ("🧠", "记忆", "#9aa6c4"))
                    cards += (f"<div class='ev'><div class='ev-top'>"
                              f"<span class='pill' style='background:{col}22;color:{col}'>{ico} {lab}</span>"
                              f"<span class='ev-sc'>召回 · 注入回答</span></div>"
                              f"<div class='ev-txt' style='margin-top:8px'>{esc(m.get('text'))}</div></div>")
                st.markdown("<div style='color:var(--faint);font-size:.75rem;margin-bottom:6px'>"
                            "召回（注入到本次回答的用户画像）</div>" + cards, unsafe_allow_html=True)
            if mem_writes:
                badges = ""
                for w in mem_writes:
                    ico, lab, col = MEM_KIND.get(w.get("kind"), ("🧠", "记忆", "#9aa6c4"))
                    tag = "冲突更新" if int(w.get("version", 1)) > 1 else "写入"
                    badges += (f"<span class='pill' style='background:{col}22;color:{col}'>"
                               f"{ico} {tag}·{lab} v{esc(w.get('version'))}：{esc(w.get('text'))}</span>")
                st.markdown("<div style='color:var(--faint);font-size:.75rem;margin:10px 0 6px'>"
                            "本轮写入 / 更新（经去重·冲突演化）</div>" + badges, unsafe_allow_html=True)

        st.markdown("<div class='eyebrow'>Retrieved evidence</div>", unsafe_allow_html=True)
        for i, e in enumerate(evidence, 1):
            ev = asdict(e) if is_dataclass(e) else e
            sc = float(ev.get("score", 0) or 0)
            w = max(5, min(100, int(sc * 100)))
            txt = (ev.get("text", "") or "")[:240]
            st.markdown(
                f"<div class='ev'><div class='ev-top'>"
                f"<div style='display:flex;align-items:center;gap:10px'><span class='ev-rank'>{i}</span>"
                f"<span class='ev-id'>{esc(ev.get('chunk_id'))}</span></div>"
                f"<span class='ev-sc'>{esc(ev.get('doc_id'))} · {sc:.4f}</span></div>"
                f"<div class='bar'><span style='width:{w}%'></span></div>"
                f"<div class='ev-txt'>{esc(txt)}…</div></div>",
                unsafe_allow_html=True,
            )

    with side:
        st.markdown("<div class='eyebrow'>Execution trace</div>", unsafe_allow_html=True)
        nodes_html = "<div class='tl'>"
        for step in trace:
            node = step.get("node", "?")
            title, _sub = NODE_LABELS.get(node, (node, ""))
            if node == "memory_retrieve":
                d = f"召回 {len(step.get('recalled', []))} 条长期记忆" + \
                    ("· 有短期上下文" if step.get("has_short") else "")
            elif node == "memory_write":
                wrote = step.get("wrote", [])
                d = "写入 " + (esc(", ".join(wrote)) if wrote else "（无新记忆）")
            elif node == "summarize":
                d = f"压缩短期记忆 · summary {esc(step.get('summary_len'))} 字"
            elif node == "planner":
                d = "改写 → " + esc(" / ".join(step.get("queries", [])))
            elif node == "retrieval":
                d = f"iter {esc(step.get('iter'))} · {esc(step.get('mode'))} · {len(step.get('hits', []))} 命中"
            elif node == "tool":
                called = step.get("called", [])
                d = "调用 " + (esc(", ".join(called)) if called else "（无）")
            elif node == "writer":
                d = f"生成答案 · 标注 {len(step.get('citations', []))} 条引用"
            elif node == "critic":
                d = f"faithful={esc(step.get('faithful'))} · score={esc(step.get('score'))}"
            else:
                d = ""
            nodes_html += (f"<div class='node done'><div class='n-t'>{esc(title)}</div>"
                           f"<div class='n-d'>{d}</div></div>")
        nodes_html += "</div>"
        st.markdown(nodes_html, unsafe_allow_html=True)

        # —— 记忆演化审计：现行记忆 + 被覆盖旧值（superseded 链）——
        st.markdown("<div class='eyebrow' style='margin-top:8px'>Memory evolution · 演化审计</div>",
                    unsafe_allow_html=True)
        try:
            from app.memory.store import get_memory_store
            _recs = get_memory_store().list_by_user(
                (st.session_state.get("mem_uid") or "alice").strip())
        except Exception:
            _recs = []
        if _recs:
            _live = [r for r in _recs if not r.superseded_by]
            _dead = [r for r in _recs if r.superseded_by]
            rows = ""
            for r in sorted(_live, key=lambda x: -getattr(x, "updated_at", 0)):
                ico, lab, col = MEM_KIND.get(r.kind, ("🧠", "记忆", "#9aa6c4"))
                chain = ""
                for o in [o for o in _dead if o.superseded_by == r.mem_id]:
                    chain += (f"<div style='color:var(--faint);font-size:.72rem;"
                              f"text-decoration:line-through;margin-top:4px'>"
                              f"v{esc(o.version)} {esc(o.text)}（已被取代）</div>")
                rows += (f"<div class='ev' style='padding:11px 14px'>"
                         f"<span class='pill' style='background:{col}22;color:{col}'>{ico} {lab} v{esc(r.version)}</span>"
                         f"<span class='ev-sc'>命中 {esc(r.use_count)} 次</span>"
                         f"<div class='ev-txt' style='margin-top:6px'>{esc(r.text)}</div>{chain}</div>")
            st.markdown(rows, unsafe_allow_html=True)
            st.markdown(f"<div style='color:var(--faint);font-size:.72rem;margin-top:2px'>"
                        f"现行 {len(_live)} 条 · 审计留痕 {len(_dead)} 条</div>",
                        unsafe_allow_html=True)
        else:
            st.markdown("<div style='color:var(--faint);font-size:.78rem'>"
                        "该用户暂无长期记忆（多问几轮自述偏好即可看到积累）</div>",
                        unsafe_allow_html=True)

        with st.expander("原始 state（调试）"):
            st.json({"iterations": iterations, "verify": verify,
                     "citations": cites, "recalled_memories": recalled,
                     "memory_writes": mem_writes, "trace": trace})

else:
    _hint = ("已接入真实大模型 · 点上方示例问题，或直接提问。" if settings.use_llm
             else "离线 fallback：无需 API key 也能体验完整链路。")
    st.markdown(
        f"<div class='card' style='text-align:center;padding:40px 24px;border-style:dashed'>"
        f"<div style='font-size:2rem'>🧠</div>"
        f"<div style='font-weight:700;font-size:1.05rem;margin-top:8px'>输入问题，开始一次 Agent 编排</div>"
        f"<div style='color:var(--muted);font-size:.86rem;margin-top:6px'>{_hint}</div></div>",
        unsafe_allow_html=True,
    )
