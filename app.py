import streamlit as st
import pandas as pd
import requests
import json
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, quote_plus
import plotly.express as px


SERPER_ENDPOINT = "https://google.serper.dev/search"

LOCATION_PRESETS = {
    "United States": {"gl": "us", "hl": "en"},
    "United Kingdom": {"gl": "uk", "hl": "en"},
    "Canada": {"gl": "ca", "hl": "en"},
    "Australia": {"gl": "au", "hl": "en"},
    "India": {"gl": "in", "hl": "en"},
    "Pakistan": {"gl": "pk", "hl": "en"},
    "Germany": {"gl": "de", "hl": "de"},
    "France": {"gl": "fr", "hl": "fr"},
    "Spain": {"gl": "es", "hl": "es"},
}


def init_session_state():
    defaults = {
        "domain": "",
        "results_data": [],
        "serp_cache": {},
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
    """Strict exact-host match.

    Only the exact host the user entered counts (the `www.` prefix is normalized
    away by get_root_domain, so `www.folio3.com` and `folio3.com` are treated as
    the same host).
    """
    result_domain = get_root_domain(link)
    if not result_domain or not target_domain:
        return False
    return result_domain == target_domain


def determine_page_type(url):
    """Classify a URL into Blog / Product / Homepage / Landing Page."""
    if not url or url == "N/A":
        return "N/A"
    path = urlparse(url.lower()).path
    blog_markers = ["/blog", "/article", "/post", "/news", "/insights",
                    "/resources/blog", "/learn", "/guides", "/case-stud"]
    if any(m in path for m in blog_markers):
        return "Blog"
    if any(m in path for m in ["/product", "/services", "/solutions", "/pricing", "/features"]):
        return "Product"
    if path in ("", "/"):
        return "Homepage"
    return "Landing Page"


def google_verify_url(keyword, gl="us"):
    return f"https://www.google.com/search?q={quote_plus(keyword)}&gl={gl}&pws=0&num=20"


def _post_serper(body, api_key):
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    last_err = None
    for attempt in range(3):
        try:
            r = requests.post(SERPER_ENDPOINT, headers=headers, data=json.dumps(body), timeout=25)
            if r.status_code in (401, 403):
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


def analyze_keyword(keyword, target_domain, api_key, gl, hl, location, device, depth=100):
    """Walk Google SERP page-by-page (num=10) up to `depth` results.

    Google deprecated the `num=` query parameter in late 2025, so requesting
    `num=100` no longer reliably returns 100 results. The supported path is
    paginated `page=1..N` with `num=10`. We walk every requested page (only
    stopping on a truly empty response) and use Serper's `position` field —
    Google's actual organic rank — so that SERP features don't cause us to
    under-count.
    """
    pages_needed = max(1, (depth + 9) // 10)

    all_organic = []
    rank_counter = 0
    matches = []

    for page in range(1, pages_needed + 1):
        # Mirror what a generic user in this country sees: country-level gl + hl,
        # no `location` hint unless the user explicitly opted in. Force
        # `autocorrect: False` so Google scores the exact query the user typed —
        # query-rewriting on long-tail keywords can shift the SERP enough to
        # bury a real ranking.
        body = {
            "q": keyword, "gl": gl, "hl": hl,
            "num": 10, "page": page, "device": device,
            "autocorrect": False,
        }
        if location:
            body["location"] = location

        res = _post_serper(body, api_key)
        if "error" in res:
            if all_organic:
                break
            return res

        organic = res["payload"].get("organic", []) or []
        if not organic:
            break

        for item in organic:
            rank_counter += 1
            link = item.get("link", "")
            # Prefer Serper's `position` field — it reflects Google's actual
            # organic position INCLUDING the slots taken by SERP features
            # (PAA, ads, featured snippet, knowledge panel). Falling back to
            # the cumulative counter would systematically *undercount* on
            # competitive queries because SERP-feature slots aren't counted.
            api_pos = item.get("position")
            if isinstance(api_pos, int) and api_pos >= rank_counter:
                position = api_pos
            else:
                position = rank_counter
            entry = {
                "position": position, "url": link,
                "title": item.get("title", ""), "snippet": item.get("snippet", ""),
            }
            all_organic.append(entry)
            if domain_matches(link, target_domain):
                matches.append(entry)

        if matches:
            break
        # Do NOT break on partial pages (len < 10). Competitive SERPs often
        # return 7–9 organic results per page because SERP features take the
        # rest. Breaking here would abort pagination and miss page 2–10
        # rankings. Only the empty-page check above (`if not organic: break`)
        # should terminate the walk early.
        if page < pages_needed:
            time.sleep(0.5)

    top_competitors = [
        {
            "position": e["position"],
            "domain": get_root_domain(e["url"]),
            "url": e["url"],
            "title": e["title"],
        }
        for e in all_organic[:10]
    ]

    if matches:
        best = min(matches, key=lambda e: e["position"])
        return {
            "rank": best["position"],
            "url": best["url"],
            "title": best["title"],
            "all_matches": matches,
            "top_competitors": top_competitors,
            "results_count": len(all_organic),
        }

    return {
        "rank": None, "url": "N/A", "title": "",
        "all_matches": [],
        "top_competitors": top_competitors,
        "results_count": len(all_organic),
    }


def render_styling():
    st.markdown(
        """
        <style>
        .block-container { padding-top: 2.2rem; padding-bottom: 2rem; max-width: 1400px; }
        h1 { letter-spacing: -0.02em; font-weight: 800; }

        .stTabs [data-baseweb="tab-list"] { gap: 4px; }
        .stTabs [data-baseweb="tab"] {
            height: 44px; padding: 0 18px;
            font-size: 14px; font-weight: 600;
            border-radius: 8px 8px 0 0;
        }
        .stTabs [aria-selected="true"] { background: rgba(59,130,246,0.08); }

        .kpi-card {
            background: var(--secondary-background-color, #f8fafc);
            padding: 18px 22px; border-radius: 12px;
            border: 1px solid rgba(127, 127, 127, 0.18); height: 100%;
        }
        .kpi-label {
            font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase;
            color: var(--text-color, #6b7280); opacity: 0.7; font-weight: 700;
        }
        .kpi-value {
            font-size: 1.85rem; font-weight: 800;
            color: var(--text-color, #111827);
            margin-top: 6px; line-height: 1.1;
        }
        .kpi-sub {
            font-size: 12px; color: var(--text-color, #6b7280);
            opacity: 0.6; margin-top: 4px;
        }
        .live-dot {
            display:inline-block; width:8px; height:8px; border-radius:50%;
            background:#10b981; margin-right:6px; animation: pulse 1.5s infinite;
            vertical-align: middle;
        }
        @keyframes pulse {
            0%   { box-shadow: 0 0 0 0 rgba(16,185,129,0.55); }
            70%  { box-shadow: 0 0 0 8px rgba(16,185,129,0); }
            100% { box-shadow: 0 0 0 0 rgba(16,185,129,0); }
        }
        section[data-testid="stSidebar"] h3 { letter-spacing: -0.01em; }
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


def kpi_card(label, value, sub_html=""):
    sub = f'<div class="kpi-sub">{sub_html}</div>' if sub_html else ""
    return f'<div class="kpi-card"><div class="kpi-label">{label}</div><div class="kpi-value">{value}</div>{sub}</div>'


def run_tracking(keywords, target_domain, api_key, gl, hl, location, device, depth=100):
    root = get_root_domain(target_domain)
    progress_text = st.empty()
    progress_bar = st.progress(0)

    rows = []
    cache = {}

    for i, kw in enumerate(keywords):
        progress_text.markdown(
            f"<span class='live-dot'></span>**Scanning** `{kw}`  · {i+1}/{len(keywords)}",
            unsafe_allow_html=True,
        )
        res = analyze_keyword(kw, root, api_key, gl, hl, location, device, depth=depth)

        if "error" in res:
            st.error(f"{res['error']}: {res['msg']}")
            break

        rank = res.get("rank")
        url = res.get("url", "N/A")
        position = rank if isinstance(rank, int) else depth + 1
        display_rank = str(rank) if isinstance(rank, int) else f"Not in Top {depth}"
        top_competitors = res.get("top_competitors", [])
        top_result = top_competitors[0]["domain"] if top_competitors else "—"

        rows.append({
            "Keyword": kw,
            "Rank": display_rank,
            "Position": position,
            "URL": url,
            "Title": res.get("title", ""),
            "Page Type": determine_page_type(url),
            "Top Result": top_result,
            "Results Found": res.get("results_count", 0),
        })
        cache[kw] = top_competitors

        progress_bar.progress((i + 1) / len(keywords))
        if i < len(keywords) - 1:
            time.sleep(0.4)

    progress_text.empty()
    progress_bar.empty()

    if rows:
        st.session_state.results_data = rows
        st.session_state.serp_cache = cache
        st.session_state.last_run_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        st.toast(f"✓ Tracked {len(rows)} keywords", icon="✅")


def render_sidebar():
    with st.sidebar:
        st.markdown("### ⚙️ Tracker Configuration")
        target_domain = st.text_input(
            "Target Domain",
            placeholder="yourdomain.com  or  sub.yourdomain.com",
            value=st.session_state.domain,
            help="Enter the EXACT host you want to track. Strict match only — "
                 "`folio3.com` will NOT match `agtech.folio3.com`, and "
                 "`agtech.folio3.com` will NOT match `folio3.com` or "
                 "`blog.folio3.com`. To track multiple subdomains, scan each "
                 "one separately.",
        )
        if target_domain:
            resolved = get_root_domain(target_domain)
            st.caption(f"🎯 Strict exact match: **`{resolved}`** only")
        serper_key = st.text_input("Serper.dev API Key", type="password")

        with st.expander("🌍 SERP Targeting", expanded=True):
            country = st.selectbox("Country", list(LOCATION_PRESETS.keys()), index=0)
            preset = LOCATION_PRESETS[country]
            custom_location = st.text_input(
                "City-level Location (optional)",
                placeholder="e.g. Austin, Texas, United States",
                help="Leave blank for the broad, country-level view that any "
                     "generic user in this country sees on Google. Fill in a "
                     "city only if you want results geo-targeted to that "
                     "specific city (matches a VPN exiting from that city).",
            )
            location = custom_location.strip()
            device = st.selectbox("Device", ["desktop", "mobile"], index=0)
            depth_label = st.select_slider(
                "Tracking Depth",
                options=["Top 10", "Top 20", "Top 30", "Top 50", "Top 100"],
                value="Top 100",
                help="How deep to scan the SERP. Walks page=1..N (num=10 each) and "
                     "stops early once your domain is found, to save API credits.",
            )
            depth_map = {"Top 10": 10, "Top 20": 20, "Top 30": 30, "Top 50": 50, "Top 100": 100}
            depth = depth_map[depth_label]
            if location:
                st.caption(f"📍 Targeting: **{location}**")
            else:
                st.caption(f"📍 Country-level **{country}** (gl={preset['gl']}, hl={preset['hl']})")

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
            pasted = st.text_area(
                "Paste keywords (one per line)", height=160, label_visibility="collapsed",
                placeholder="ai keyword tracker\nrank tracker tool\nseo monitoring software",
            )
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
            "location": location,
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

    if total_kw > 0 and top_100 == 0 and (df_res["Results Found"] > 0).any():
        st.warning(
            "⚠️ **None of your keywords ranked in the top 100, but Serper IS returning results.** "
            "Open the **SERP Inspector** tab to see who's ranking for each keyword."
        )

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(kpi_card("Visibility Index", f"{visibility}%", f"{top_10}/{total_kw} in top 10"), unsafe_allow_html=True)
    c2.markdown(kpi_card("Top 3", str(top_3), "premium positions"), unsafe_allow_html=True)
    c3.markdown(kpi_card("Top 10", str(top_10), f"{top_20} in top 20"), unsafe_allow_html=True)
    c4.markdown(kpi_card("Avg Position", f"{avg_pos:.1f}" if avg_pos is not None else "—",
                         f"{len(ranked)} ranked of {total_kw}"), unsafe_allow_html=True)

    st.markdown("####")

    st.markdown("##### Ranking Distribution")
    dist = {
        "1-3": top_3, "4-10": top_10 - top_3,
        "11-20": top_20 - top_10,
        "21-50": sum(1 for p in all_pos if 20 < p <= 50),
        "51-100": sum(1 for p in all_pos if 50 < p <= 100),
        "Not Ranked": total_kw - top_100,
    }
    bar_df = pd.DataFrame(list(dist.items()), columns=["Range", "Count"])
    fig = px.bar(
        bar_df, x="Range", y="Count", color="Range", text="Count",
        color_discrete_sequence=["#10b981", "#34d399", "#fbbf24", "#f59e0b", "#ef4444", "#9ca3af"],
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(
        showlegend=False, margin=dict(l=0, r=0, t=10, b=0), height=320,
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        xaxis_title="", yaxis_title="",
    )
    st.plotly_chart(fig, use_container_width=True)

    opps = df_res[(df_res["Position"] >= 4) & (df_res["Position"] <= 20)].sort_values("Position")
    if not opps.empty:
        st.markdown("##### 🎯 Quick-Win Opportunities")
        st.caption("Keywords ranking in positions 4–20 — closest to page-1 / top-3.")
        st.dataframe(
            opps[["Keyword", "Rank", "URL", "Page Type", "Top Result"]],
            use_container_width=True, hide_index=True,
            height=min(320, 45 + 38 * len(opps)),
            column_config={"URL": st.column_config.LinkColumn("URL", width="medium")},
        )


def render_intelligence(df_res):
    st.markdown("##### 🔍 Keyword Intelligence")

    col_a, col_b = st.columns([3, 1])
    with col_a:
        search = st.text_input("Search keywords", placeholder="Filter by keyword or URL…", label_visibility="collapsed")
    with col_b:
        rank_filter = st.selectbox("Position", ["All", "Top 3", "Top 10", "Top 20", "21–100", "Not Ranked"])

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
    elif rank_filter == "21–100":
        df = df[(df["Position"] > 20) & (df["Position"] <= 100)]
    elif rank_filter == "Not Ranked":
        df = df[df["Position"] > 100]

    display_cols = ["Keyword", "Rank", "URL", "Page Type", "Top Result"]
    df_show = df[display_cols].sort_values(
        "Rank", key=lambda s: s.map(lambda v: int(v) if str(v).isdigit() else 999)
    )

    st.caption(f"Showing **{len(df_show)}** of {len(df_res)} keywords")
    styled = df_show.style.map(rank_color, subset=["Rank"])
    st.dataframe(
        styled, use_container_width=True, hide_index=True, height=560,
        column_config={
            "URL": st.column_config.LinkColumn("Your URL", width="medium"),
            "Top Result": st.column_config.TextColumn(
                "Top Result", help="The #1 ranking domain in Google for this keyword."
            ),
        },
    )

    with st.expander("ℹ️ How are these ranks computed?"):
        st.markdown(
            "We mirror what a generic user in the selected country sees on Google:\n\n"
            "- **Country-level only by default** (`gl=us`, `hl=en`). No `location` "
            "hint — the broadest, most user-like SERP. Add a city in **SERP "
            "Targeting → City-level Location** only if you want results "
            "geo-targeted to a specific city.\n"
            "- **Authoritative rank.** We use Serper's `position` field — "
            "Google's actual organic position, which preserves the gaps left "
            "by SERP features (PAA, ads, featured snippet, knowledge panel). "
            "A simple cumulative counter would underestimate the rank because "
            "those feature slots aren't counted.\n"
            "- **Walk every requested page.** We never break on a partial "
            "page — competitive SERPs often return 7–9 organic per page, and "
            "stopping there would miss your page 2–10 rankings.\n"
            "- **`autocorrect: false`.** Forces Google to score the exact "
            "query you entered — no query rewriting, no broadening.\n"
            "- **Strict host match.** Only the exact host you entered counts.\n"
            "- **Paginated walk.** Google deprecated `num=100`, so we walk "
            "`page=1..N` with `num=10` and accumulate the full top-100 list."
        )


def render_serp_inspector(df_res):
    st.markdown("##### 🔬 SERP Inspector")
    st.caption("Pick any keyword to see Google's actual top 10 results.")

    cache = st.session_state.get("serp_cache") or {}
    if not cache:
        st.info("Run a tracking session first.")
        return

    target = get_root_domain(st.session_state.get("domain", ""))
    keyword = st.selectbox("Keyword", list(cache.keys()))
    if not keyword:
        return

    competitors = cache.get(keyword, [])
    if not competitors:
        st.warning("Serper returned zero organic results for this keyword. Try changing device or location.")
        return

    rows = []
    found_target = False
    for c in competitors:
        is_you = domain_matches(c["url"], target) if target else False
        if is_you:
            found_target = True
        rows.append({
            "#": c["position"],
            "Domain": c["domain"] + (" 🟢 (you)" if is_you else ""),
            "Title": c["title"],
            "URL": c["url"],
        })

    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True, hide_index=True, height=min(420, 45 + 38 * len(rows)),
        column_config={"URL": st.column_config.LinkColumn("URL", width="medium")},
    )

    st.link_button("🔗 Verify this SERP on Google", google_verify_url(keyword), use_container_width=False)

    if target and not found_target:
        st.info(f"`{target}` is **not in the top 10** for this keyword.")
    elif found_target:
        st.success(f"✓ `{target}` appears in the top 10 for this keyword.")


def render_exports(df_res):
    st.markdown("##### 📑 Export & Share")
    csv = df_res.drop(columns=["Position", "Results Found"], errors="ignore").to_csv(index=False).encode("utf-8")

    domain_slug = st.session_state.domain.replace(".", "_") or "report"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    st.download_button("📥 CSV Report", data=csv,
                       file_name=f"{domain_slug}_rankings_{stamp}.csv",
                       mime="text/csv", type="primary", use_container_width=False)


def main():
    st.set_page_config(page_title="SERP Tracker Pro", page_icon="🎯", layout="wide", initial_sidebar_state="expanded")
    init_session_state()
    render_styling()

    st.markdown(
        "<h1 style='margin-bottom:0'>🎯 SERP Tracker Pro</h1>"
        "<p style='opacity:0.65; margin-top:4px; font-size:0.95rem;'>"
        "<span class='live-dot'></span>Real-time Google rank tracking"
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
                depth=cfg["depth"],
            )

    tab1, tab2, tab3, tab4 = st.tabs(["📊 Dashboard", "🔍 Keyword Intelligence", "🔬 SERP Inspector", "📑 Export"])

    if not st.session_state.results_data:
        with tab1:
            st.info("Configure the sidebar and click **Run Live Tracking** to populate the dashboard.")
        return

    df_res = pd.DataFrame(st.session_state.results_data)
    with tab1:
        render_dashboard(df_res)
    with tab2:
        render_intelligence(df_res)
    with tab3:
        render_serp_inspector(df_res)
    with tab4:
        render_exports(df_res)


if __name__ == "__main__":
    main()
