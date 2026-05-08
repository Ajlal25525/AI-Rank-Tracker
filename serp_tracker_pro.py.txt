import streamlit as st
import pandas as pd
import requests
import json
import time
from datetime import datetime, timezone
from urllib.parse import urlparse
import plotly.express as px
import plotly.graph_objects as go


SERPER_ENDPOINT = "https://google.serper.dev/search"

LOCATION_PRESETS = {
    "United States": {"gl": "us", "hl": "en", "location": "United States"},
    "United Kingdom": {"gl": "uk", "hl": "en", "location": "United Kingdom"},
    "Canada": {"gl": "ca", "hl": "en", "location": "Canada"},
    "Australia": {"gl": "au", "hl": "en", "location": "Australia"},
    "India": {"gl": "in", "hl": "en", "location": "India"},
    "Pakistan": {"gl": "pk", "hl": "en", "location": "Pakistan"},
    "Germany": {"gl": "de", "hl": "de", "location": "Germany"},
    "France": {"gl": "fr", "hl": "fr", "location": "France"},
    "Spain": {"gl": "es", "hl": "es", "location": "Spain"},
}


def init_session_state():
    defaults = {
        "domain": "",
        "results_data": [],
        "last_run_at": None,
        "previous_ranks": {},
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def get_root_domain(url_or_domain):
    s = str(url_or_domain).strip()
    if not s.startswith(("http://", "https://")):
        s = "http://" + s
    try:
        netloc = urlparse(s).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return str(url_or_domain).lower()


def domain_matches(link, target_domain):
    result_domain = get_root_domain(link)
    if not result_domain or not target_domain:
        return False
    return result_domain == target_domain or result_domain.endswith("." + target_domain)


def determine_page_type(url):
    if not url or url == "N/A":
        return "N/A"
    u = url.lower()
    path = urlparse(u).path
    if any(kw in path for kw in ["/blog", "/article", "/post", "/news", "/insights", "/resources/blog"]):
        return "Blog"
    if any(kw in path for kw in ["/product", "/services", "/solutions", "/pricing", "/features"]):
        return "Product"
    if path in ("", "/"):
        return "Homepage"
    return "Landing Page"


def detect_serp_features(payload):
    feature_map = [
        ("answerBox", "Featured Snippet"),
        ("knowledgeGraph", "Knowledge Graph"),
        ("peopleAlsoAsk", "People Also Ask"),
        ("relatedSearches", "Related Searches"),
        ("images", "Images"),
        ("videos", "Videos"),
        ("topStories", "Top Stories"),
        ("shopping", "Shopping"),
    ]
    return [label for key, label in feature_map if key in payload]


def fetch_serp(keyword, api_key, gl, hl, location, device, num=100):
    """Single Serper.dev call returning top-N organic results plus SERP features.

    Using num=100 + location avoids the multi-page drift that made the old
    tracker unreliable, and uses ~1/10th the API credits.
    """
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    body = {
        "q": keyword,
        "gl": gl,
        "hl": hl,
        "num": num,
        "device": device,
        "autocorrect": False,
    }
    if location:
        body["location"] = location

    last_err = None
    for attempt in range(3):
        try:
            r = requests.post(SERPER_ENDPOINT, headers=headers, data=json.dumps(body), timeout=20)
            if r.status_code == 401 or r.status_code == 403:
                return {"error": "API Key Error", "msg": "Unauthorized — check your Serper.dev API key."}
            if r.status_code in (402, 429):
                return {"error": "Rate Limited", "msg": "Serper.dev rate limit / credits exhausted."}
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                time.sleep(1.5 * (attempt + 1))
                continue
            return {"payload": r.json()}
        except requests.RequestException as e:
            last_err = str(e)
            time.sleep(1.5 * (attempt + 1))

    return {"error": "Network Error", "msg": last_err or "Unknown error"}


def analyze_keyword(keyword, target_domain, api_key, gl, hl, location, device):
    res = fetch_serp(keyword, api_key, gl, hl, location, device, num=100)
    if "error" in res:
        return res

    payload = res["payload"]
    organic = payload.get("organic", []) or []
    features = detect_serp_features(payload)

    matches = []
    for idx, item in enumerate(organic, start=1):
        link = item.get("link", "")
        position = item.get("position", idx)
        if domain_matches(link, target_domain):
            matches.append({
                "position": position,
                "url": link,
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
            })

    top_competitors = [
        {"position": item.get("position", i + 1), "domain": get_root_domain(item.get("link", "")), "url": item.get("link", "")}
        for i, item in enumerate(organic[:10])
    ]

    if matches:
        best = matches[0]
        return {
            "rank": best["position"],
            "url": best["url"],
            "title": best["title"],
            "all_matches": matches,
            "features": features,
            "top_competitors": top_competitors,
            "results_count": len(organic),
        }

    return {
        "rank": None,
        "url": "N/A",
        "title": "",
        "all_matches": [],
        "features": features,
        "top_competitors": top_competitors,
        "results_count": len(organic),
    }


def render_styling():
    st.markdown(
        """
        <style>
        .block-container { padding-top: 2rem; padding-bottom: 2rem; max-width: 1400px; }
        .stTabs [data-baseweb="tab-list"] { gap: 8px; border-bottom: 1px solid #2D333B; }
        .stTabs [data-baseweb="tab"] {
            height: 44px;
            background: transparent;
            border-radius: 8px 8px 0 0;
            padding: 0 18px;
            font-size: 14px;
            font-weight: 600;
            color: #8B949E;
        }
        .stTabs [aria-selected="true"] { color: #FFFFFF; background: rgba(59,130,246,0.08); }

        .kpi-card {
            background: linear-gradient(180deg, #161A25 0%, #131722 100%);
            padding: 18px 20px;
            border-radius: 14px;
            border: 1px solid #2D333B;
            box-shadow: 0 1px 3px rgba(0,0,0,0.2);
            height: 100%;
        }
        .kpi-label {
            font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase;
            color: #8B949E; font-weight: 700;
        }
        .kpi-value {
            font-size: 1.9rem; font-weight: 800; color: #FFFFFF;
            margin-top: 6px; line-height: 1.1;
        }
        .kpi-sub { font-size: 12px; color: #6E7681; margin-top: 4px; }
        .kpi-up   { color: #10b981; font-weight: 700; }
        .kpi-down { color: #ef4444; font-weight: 700; }
        .kpi-flat { color: #8B949E; font-weight: 700; }

        .pill {
            display: inline-block; padding: 2px 8px; border-radius: 10px;
            font-size: 11px; font-weight: 600; margin-right: 4px;
            background: rgba(59,130,246,0.12); color: #60a5fa;
        }
        .live-dot {
            display:inline-block; width:8px; height:8px; border-radius:50%;
            background:#10b981; margin-right:6px; animation: pulse 1.5s infinite;
        }
        @keyframes pulse {
            0% { box-shadow: 0 0 0 0 rgba(16,185,129,0.6); }
            70% { box-shadow: 0 0 0 8px rgba(16,185,129,0); }
            100% { box-shadow: 0 0 0 0 rgba(16,185,129,0); }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def rank_color(val):
    try:
        v = str(val)
        if "Not" in v or v == "N/A" or v == "—":
            return "background-color: rgba(239,68,68,0.10); color: #ef4444; font-weight:600;"
        n = int(v.strip())
        if n <= 3:
            return "background-color: rgba(16,185,129,0.18); color: #10b981; font-weight:700;"
        if n <= 10:
            return "background-color: rgba(52,211,153,0.12); color: #34d399; font-weight:600;"
        if n <= 20:
            return "background-color: rgba(251,191,36,0.12); color: #fbbf24; font-weight:600;"
        if n <= 50:
            return "background-color: rgba(245,158,11,0.10); color: #f59e0b;"
        return "background-color: rgba(239,68,68,0.10); color: #ef4444;"
    except Exception:
        return ""


def delta_str(curr, prev):
    if prev is None or curr is None:
        return "—", "kpi-flat"
    try:
        c = int(curr); p = int(prev)
    except (ValueError, TypeError):
        return "—", "kpi-flat"
    diff = p - c  # positive = improved (lower position number)
    if diff > 0:
        return f"▲ {diff}", "kpi-up"
    if diff < 0:
        return f"▼ {abs(diff)}", "kpi-down"
    return "= 0", "kpi-flat"


def kpi_card(label, value, sub_html=""):
    sub = f'<div class="kpi-sub">{sub_html}</div>' if sub_html else ""
    return f'<div class="kpi-card"><div class="kpi-label">{label}</div><div class="kpi-value">{value}</div>{sub}</div>'


def run_tracking(keywords, target_domain, api_key, gl, hl, location, device):
    root = get_root_domain(target_domain)
    progress_text = st.empty()
    progress_bar = st.progress(0)

    previous = {row["Keyword"]: row.get("Position") for row in st.session_state.get("results_data", [])}
    st.session_state.previous_ranks = previous
    rows = []

    for i, kw in enumerate(keywords):
        progress_text.markdown(f"<span class='live-dot'></span>**Scanning** `{kw}`  · {i+1}/{len(keywords)}", unsafe_allow_html=True)
        res = analyze_keyword(kw, root, api_key, gl, hl, location, device)

        if "error" in res:
            st.error(f"{res['error']}: {res['msg']}")
            break

        rank = res.get("rank")
        url = res.get("url", "N/A")
        position = rank if isinstance(rank, int) else 101
        display_rank = str(rank) if isinstance(rank, int) else "Not in Top 100"

        rows.append({
            "Keyword": kw,
            "Rank": display_rank,
            "Position": position,
            "URL": url,
            "Title": res.get("title", ""),
            "Page Type": determine_page_type(url),
            "SERP Features": ", ".join(res.get("features", [])) or "—",
            "Cannibalization": len(res.get("all_matches", [])),
            "Checked At": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        })

        progress_bar.progress((i + 1) / len(keywords))
        if i < len(keywords) - 1:
            time.sleep(0.4)

    progress_text.empty()
    progress_bar.empty()

    if rows:
        st.session_state.results_data = rows
        st.session_state.last_run_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        st.toast(f"✓ Tracked {len(rows)} keywords", icon="✅")


def render_sidebar():
    with st.sidebar:
        st.markdown("### ⚙️ Tracker Configuration")

        target_domain = st.text_input(
            "Target Domain",
            placeholder="agtech.folio3.com",
            value=st.session_state.domain,
            help="Enter the exact domain or subdomain you want to track.",
        )
        serper_key = st.text_input("Serper.dev API Key", type="password")

        with st.expander("🌍 SERP Targeting", expanded=True):
            country = st.selectbox("Country / Location", list(LOCATION_PRESETS.keys()), index=0)
            preset = LOCATION_PRESETS[country]
            device = st.selectbox("Device", ["desktop", "mobile"], index=0)
            depth = st.select_slider("Tracking Depth", options=[10, 20, 50, 100], value=100,
                                     help="How deep into the SERP to check. 100 = top 100 results.")

        st.divider()
        st.markdown("### 📥 Keywords")
        method = st.radio("Input", ["Paste", "CSV Upload"], horizontal=True, label_visibility="collapsed")

        keywords = []
        if method == "CSV Upload":
            f = st.file_uploader("CSV file", type=["csv"], label_visibility="collapsed")
            if f is not None:
                try:
                    df = pd.read_csv(f)
                    col = next((c for c in df.columns if c.lower() in ["keyword", "keywords", "search term", "query", "term"]), None)
                    if col:
                        keywords = df[col].dropna().astype(str).str.strip().tolist()
                        st.success(f"Loaded {len(keywords)} keywords")
                    else:
                        st.error("No 'Keyword' column found.")
                except Exception as e:
                    st.error(f"CSV error: {e}")
        else:
            pasted = st.text_area("Paste keywords (one per line)", height=160, label_visibility="collapsed",
                                  placeholder="ai keyword tracker\nrank tracker tool\nseo monitoring software")
            keywords = [k.strip() for k in pasted.split("\n") if k.strip()]
            if keywords:
                st.caption(f"{len(keywords)} keyword(s) ready")

        run_btn = st.button("🚀 Run Live Tracking", type="primary", use_container_width=True)

        st.divider()
        if st.session_state.last_run_at:
            st.caption(f"Last scan: {st.session_state.last_run_at}")

        return {
            "target_domain": target_domain,
            "serper_key": serper_key,
            "country": country,
            "gl": preset["gl"],
            "hl": preset["hl"],
            "location": preset["location"],
            "device": device,
            "depth": depth,
            "keywords": keywords,
            "run_btn": run_btn,
        }


def render_dashboard(df_res):
    all_pos = df_res["Position"].tolist()
    ranked = [p for p in all_pos if p <= 100]
    total_kw = len(df_res)
    top_3 = sum(1 for p in all_pos if p <= 3)
    top_10 = sum(1 for p in all_pos if p <= 10)
    top_20 = sum(1 for p in all_pos if p <= 20)
    top_100 = sum(1 for p in all_pos if p <= 100)
    avg_pos = sum(ranked) / len(ranked) if ranked else None
    visibility = round(top_10 / total_kw * 100, 1) if total_kw else 0

    prev = st.session_state.get("previous_ranks") or {}
    improved = declined = unchanged = 0
    for _, row in df_res.iterrows():
        p = prev.get(row["Keyword"])
        if p is None:
            continue
        if row["Position"] < p:
            improved += 1
        elif row["Position"] > p:
            declined += 1
        else:
            unchanged += 1

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.markdown(kpi_card("Visibility Index", f"{visibility}%", f"{top_10}/{total_kw} in top 10"), unsafe_allow_html=True)
    c2.markdown(kpi_card("Top 3", str(top_3), "premium positions"), unsafe_allow_html=True)
    c3.markdown(kpi_card("Top 10", str(top_10), f"{top_20} in top 20"), unsafe_allow_html=True)
    c4.markdown(kpi_card("Avg Position", f"{avg_pos:.1f}" if avg_pos is not None else "—",
                         f"{len(ranked)} ranked of {total_kw}"), unsafe_allow_html=True)
    movement_html = (
        f'<span class="kpi-up">▲ {improved}</span> &nbsp; '
        f'<span class="kpi-down">▼ {declined}</span> &nbsp; '
        f'<span class="kpi-flat">= {unchanged}</span>'
    )
    c5.markdown(kpi_card("Movement", f"{improved + declined + unchanged}", movement_html), unsafe_allow_html=True)

    st.markdown("####")

    g1, g2 = st.columns([2, 1])
    with g1:
        st.markdown("##### Ranking Distribution")
        dist = {
            "1-3": top_3,
            "4-10": top_10 - top_3,
            "11-20": top_20 - top_10,
            "21-50": sum(1 for p in all_pos if 20 < p <= 50),
            "51-100": sum(1 for p in all_pos if 50 < p <= 100),
            "Not Ranked": total_kw - top_100,
        }
        bar_df = pd.DataFrame(list(dist.items()), columns=["Range", "Count"])
        fig = px.bar(
            bar_df, x="Range", y="Count", color="Range", text="Count",
            color_discrete_sequence=["#10b981", "#34d399", "#fbbf24", "#f59e0b", "#ef4444", "#4b5563"],
            template="plotly_dark",
        )
        fig.update_traces(textposition="outside")
        fig.update_layout(showlegend=False, margin=dict(l=0, r=0, t=20, b=0),
                          plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                          xaxis_title="", yaxis_title="")
        st.plotly_chart(fig, use_container_width=True)

    with g2:
        st.markdown("##### Page Type Mix")
        pt = df_res[df_res["Page Type"] != "N/A"]["Page Type"].value_counts().reset_index()
        pt.columns = ["Page Type", "Count"]
        if not pt.empty:
            fig_pie = px.pie(pt, values="Count", names="Page Type", hole=0.65, template="plotly_dark",
                             color_discrete_sequence=["#3b82f6", "#8b5cf6", "#ec4899", "#06b6d4"])
            fig_pie.update_layout(showlegend=True, margin=dict(l=0, r=0, t=20, b=0),
                                  plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                                  legend=dict(orientation="h", y=-0.1))
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("No ranked pages yet.")

    opps = df_res[(df_res["Position"] >= 4) & (df_res["Position"] <= 20)].sort_values("Position")
    if not opps.empty:
        st.markdown("##### 🎯 Quick-Win Opportunities (positions 4–20)")
        st.caption("These keywords are close to page-1 / top-3 — prioritize internal linking and on-page optimization here.")
        st.dataframe(
            opps[["Keyword", "Rank", "URL", "Page Type", "SERP Features"]],
            use_container_width=True, hide_index=True, height=min(300, 45 + 35 * len(opps)),
        )


def render_intelligence(df_res):
    st.markdown("##### 🔍 Keyword Intelligence")

    col_a, col_b, col_c = st.columns([2, 1, 1])
    with col_a:
        search = st.text_input("Search keywords", placeholder="filter by keyword or URL...", label_visibility="collapsed")
    with col_b:
        rank_filter = st.selectbox("Position", ["All", "Top 3", "Top 10", "Top 20", "21-100", "Not Ranked"])
    with col_c:
        page_filter = st.selectbox("Page Type", ["All"] + sorted(df_res["Page Type"].unique().tolist()))

    df = df_res.copy()
    if search:
        s = search.lower()
        df = df[df["Keyword"].str.lower().str.contains(s) | df["URL"].str.lower().str.contains(s)]
    if rank_filter == "Top 3":
        df = df[df["Position"] <= 3]
    elif rank_filter == "Top 10":
        df = df[df["Position"] <= 10]
    elif rank_filter == "Top 20":
        df = df[df["Position"] <= 20]
    elif rank_filter == "21-100":
        df = df[(df["Position"] > 20) & (df["Position"] <= 100)]
    elif rank_filter == "Not Ranked":
        df = df[df["Position"] > 100]
    if page_filter != "All":
        df = df[df["Page Type"] == page_filter]

    prev = st.session_state.get("previous_ranks") or {}
    df["Δ"] = df.apply(lambda r: delta_str(r["Position"] if r["Position"] <= 100 else None,
                                            prev.get(r["Keyword"]) if (prev.get(r["Keyword"]) or 999) <= 100 else None)[0], axis=1)

    display_cols = ["Keyword", "Rank", "Δ", "URL", "Page Type", "SERP Features", "Cannibalization", "Checked At"]
    df_show = df[display_cols].sort_values("Rank", key=lambda s: s.map(lambda v: int(v) if str(v).isdigit() else 999))

    st.caption(f"Showing **{len(df_show)}** of {len(df_res)} keywords")
    styled = df_show.style.map(rank_color, subset=["Rank"])
    st.dataframe(
        styled, use_container_width=True, hide_index=True, height=560,
        column_config={
            "URL": st.column_config.LinkColumn("URL", width="medium"),
            "Cannibalization": st.column_config.NumberColumn(
                "Cannibalization", help="Number of URLs from your domain ranking for this keyword. >1 indicates content overlap."
            ),
        },
    )


def render_exports(df_res):
    st.markdown("##### 📑 Export & Share")
    csv = df_res.to_csv(index=False).encode("utf-8")
    json_bytes = df_res.to_json(orient="records", indent=2).encode("utf-8")

    c1, c2 = st.columns(2)
    domain_slug = st.session_state.domain.replace(".", "_") or "report"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    with c1:
        st.download_button("📥 CSV Report", data=csv,
                           file_name=f"{domain_slug}_rankings_{stamp}.csv",
                           mime="text/csv", type="primary", use_container_width=True)
    with c2:
        st.download_button("📥 JSON Report", data=json_bytes,
                           file_name=f"{domain_slug}_rankings_{stamp}.json",
                           mime="application/json", use_container_width=True)

    st.divider()
    st.markdown("##### Run Summary")
    st.json({
        "domain": st.session_state.domain,
        "keywords_tracked": len(df_res),
        "last_run_at": st.session_state.last_run_at,
        "ranked_in_top_100": int((df_res["Position"] <= 100).sum()),
    })


def main():
    st.set_page_config(page_title="SERP Tracker Pro", page_icon="🎯", layout="wide", initial_sidebar_state="expanded")
    init_session_state()
    render_styling()

    st.markdown(
        "<h1 style='margin-bottom:0'>🎯 SERP Tracker Pro</h1>"
        "<p style='color:#8B949E; margin-top:4px'>"
        "<span class='live-dot'></span>Real-time Google rank tracking · powered by Serper.dev"
        "</p>",
        unsafe_allow_html=True,
    )

    cfg = render_sidebar()

    if cfg["run_btn"]:
        if not cfg["target_domain"]:
            st.sidebar.error("Target Domain is required.")
        elif not cfg["serper_key"]:
            st.sidebar.error("Serper.dev API Key is required.")
        elif not cfg["keywords"]:
            st.sidebar.error("Add at least one keyword.")
        else:
            st.session_state.domain = cfg["target_domain"]
            run_tracking(
                cfg["keywords"], cfg["target_domain"], cfg["serper_key"],
                cfg["gl"], cfg["hl"], cfg["location"], cfg["device"],
            )

    tab1, tab2, tab3 = st.tabs(["📊 Dashboard", "🔍 Keyword Intelligence", "📑 Export"])

    if not st.session_state.results_data:
        with tab1:
            st.info("Configure the sidebar and click **Run Live Tracking** to populate the dashboard.")
            st.markdown(
                "**What's new vs the old tracker:**\n"
                "- **1 API call per keyword** (was up to 10) using Serper's `num=100` — faster, no SERP drift.\n"
                "- **`location` parameter** for accurate US-targeted rankings.\n"
                "- **SERP feature detection** (Featured Snippet, PAA, Knowledge Graph, etc.).\n"
                "- **Cannibalization detection** — flags multiple URLs ranking for the same keyword.\n"
                "- **Movement tracking** — ▲ improved / ▼ declined since last run.\n"
                "- **Quick-win opportunities** view (positions 4–20)."
            )
        return

    df_res = pd.DataFrame(st.session_state.results_data)
    with tab1:
        render_dashboard(df_res)
    with tab2:
        render_intelligence(df_res)
    with tab3:
        render_exports(df_res)


if __name__ == "__main__":
    main()