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
        "debug_data": {},
        "last_run_at": None,
        "previous_run": {},          # {keyword: position} from the prior scan
        "previous_run_at": None,
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
    """Subdomain-tolerant match — mirrors the working local-team code.

    - Enter `folio3.com` → matches `folio3.com`, `agtech.folio3.com`,
      `blog.folio3.com`, etc.
    - Enter `agtech.folio3.com` → matches `agtech.folio3.com` and any
      deeper subdomain; will NOT match the parent `folio3.com` or sibling
      subdomains like `blog.folio3.com`.
    """
    if not link or not target_domain:
        return False
    result_domain = get_root_domain(link)
    return (result_domain == target_domain
            or result_domain.endswith("." + target_domain))


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


def detect_serp_features(payload):
    fmap = [
        ("answerBox", "Featured Snippet"),
        ("knowledgeGraph", "Knowledge Graph"),
        ("peopleAlsoAsk", "People Also Ask"),
        ("relatedSearches", "Related Searches"),
        ("images", "Images"),
        ("videos", "Videos"),
        ("topStories", "Top Stories"),
        ("shopping", "Shopping"),
    ]
    return ", ".join(label for k, label in fmap if k in payload) or "—"


def _fetch_page(keyword, page, api_key, gl, hl, location, device):
    """Single Serper page fetch — minimal body, 2 attempts, 2.0s retry sleep.

    Mirrors the working local-team code's logic:
      - No `autocorrect` override (let Google behave naturally).
      - No `location` unless the user explicitly opted in.
      - On transient failures we retry once with a 2s sleep, then fall back
        to an empty page (the caller treats empty as 'no more results').
    """
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    body = {"q": keyword, "gl": gl, "hl": hl,
            "page": page, "num": 10, "device": device}
    if location:
        body["location"] = location

    for attempt in range(2):
        try:
            r = requests.post(SERPER_ENDPOINT, headers=headers,
                              data=json.dumps(body), timeout=15)
            if r.status_code == 403:
                return {"error": "API Key Error",
                        "msg": "Unauthorized — check your Serper.dev API key."}
            if r.status_code in (402, 429):
                return {"error": "Rate Limited",
                        "msg": "Serper.dev rate limit hit."}
            if r.status_code != 200:
                if attempt == 0:
                    time.sleep(2.0)
                    continue
                return {"payload": {"organic": []}}
            data = r.json()
            organic = data.get("organic", [])
            if organic:
                return {"payload": data}
            # Empty but valid — retry once in case of transient hiccup.
            if attempt == 0:
                time.sleep(2.0)
                continue
            return {"payload": data}
        except requests.RequestException as e:
            if attempt == 0:
                time.sleep(2.0)
                continue
            return ({"error": "Error", "msg": str(e)} if page == 1
                    else {"payload": {"organic": []}})
    return {"payload": {"organic": []}}


def analyze_keyword(keyword, target_domain, api_key, gl, hl, location, device, depth=100):
    """Walk Google SERP page-by-page (num=10) up to `depth` results.

    Port of the working local-team code's logic:
      - Minimal request body (no autocorrect override).
      - 2-attempt fetch per page with a 2s retry sleep (`_fetch_page`).
      - 1.0s sleep between pages so Serper returns a stable snapshot.
      - Subdomain-tolerant `domain_matches` for the rank check.
      - Pure cumulative rank counter (never trusts Serper's position field
        across pages).
    """
    pages_needed = max(1, (depth + 9) // 10)

    all_organic = []
    rank_counter = 0
    matches = []
    first_page_payload = None
    debug_pages = []

    for page in range(1, pages_needed + 1):
        res = _fetch_page(keyword, page, api_key, gl, hl, location, device)
        if "error" in res:
            debug_pages.append({"page": page, "error": res.get("msg") or res["error"],
                                "organic_count": 0, "urls": []})
            if all_organic:
                break
            return {**res, "debug_pages": debug_pages}

        payload = res["payload"]
        if page == 1:
            first_page_payload = payload

        organic = payload.get("organic", []) or []
        page_urls = []
        for item in organic:
            rank_counter += 1
            link = item.get("link", "")
            page_urls.append({
                "cumulative": rank_counter,
                "serper_position": item.get("position"),
                "url": link,
                "domain": get_root_domain(link),
            })
            entry = {
                "position": rank_counter,
                "url": link,
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
                "page": page,
            }
            all_organic.append(entry)
            if domain_matches(link, target_domain):
                matches.append(entry)

        debug_pages.append({
            "page": page,
            "organic_count": len(organic),
            "urls": page_urls,
        })

        if not organic:
            break
        if matches:
            break
        if page < pages_needed:
            time.sleep(1.0)

    top_competitors = [
        {"position": e["position"], "domain": get_root_domain(e["url"]),
         "url": e["url"], "title": e["title"]}
        for e in all_organic[:10]
    ]

    base = {
        "features": detect_serp_features(first_page_payload or {}),
        "top_competitors": top_competitors,
        "results_count": len(all_organic),
        "debug_pages": debug_pages,
    }

    if matches:
        best = min(matches, key=lambda e: e["position"])
        return {
            "rank": best["position"],
            "url": best["url"],
            "title": best["title"],
            "all_matches": matches,
            **base,
        }

    return {"rank": None, "url": "N/A", "title": "", "all_matches": [], **base}


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
    debug = {}

    for i, kw in enumerate(keywords):
        progress_text.markdown(
            f"<span class='live-dot'></span>**Scanning** `{kw}`  · {i+1}/{len(keywords)}",
            unsafe_allow_html=True,
        )
        res = analyze_keyword(kw, root, api_key, gl, hl, location, device, depth=depth)

        if "error" in res:
            st.error(f"{res['error']}: {res['msg']}")
            if res.get("debug_pages"):
                debug[kw] = res["debug_pages"]
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
            "SERP Features": res.get("features", "—"),
            "Top Result": top_result,
            "Results Found": res.get("results_count", 0),
        })
        cache[kw] = top_competitors
        debug[kw] = res.get("debug_pages", [])

        progress_bar.progress((i + 1) / len(keywords))
        if i < len(keywords) - 1:
            time.sleep(1.0)

    progress_text.empty()
    progress_bar.empty()

    if rows:
        # Stash the prior scan's keyword→position map so the dashboard can
        # compute deltas (up/down counts, avg-position change, etc.).
        if st.session_state.results_data:
            st.session_state.previous_run = {
                r["Keyword"]: r["Position"]
                for r in st.session_state.results_data
            }
            st.session_state.previous_run_at = st.session_state.last_run_at

        st.session_state.results_data = rows
        st.session_state.serp_cache = cache
        st.session_state.debug_data = debug
        st.session_state.last_run_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        st.toast(f"✓ Tracked {len(rows)} keywords", icon="✅")


def render_sidebar():
    with st.sidebar:
        st.markdown("### ⚙️ Tracker Configuration")
        target_domain = st.text_input(
            "Target Domain",
            placeholder="yourdomain.com  or  sub.yourdomain.com",
            value=st.session_state.domain,
            help="Subdomain-tolerant match. `folio3.com` matches "
                 "`folio3.com`, `agtech.folio3.com`, `blog.folio3.com`, etc. "
                 "Enter a specific subdomain (e.g. `agtech.folio3.com`) to "
                 "narrow the match to that host and its sub-subdomains only.",
        )
        if target_domain:
            resolved = get_root_domain(target_domain)
            st.caption(f"🎯 Matches **`{resolved}`** and its subdomains")
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
                help="How deep to walk the SERP. 1 API credit per page; "
                     "stops early once your domain is found.",
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


def _format_delta(delta, lower_is_better=False, suffix=""):
    """Return colored ▲/▼ HTML for a numeric change, or em-dash if no delta."""
    if delta is None:
        return '<span style="color:#9ca3af;">—</span>'
    if delta == 0:
        return f'<span style="color:#9ca3af;">— {abs(delta):.1f}{suffix}</span>'
    improving = (delta < 0) if lower_is_better else (delta > 0)
    color = "#10b981" if improving else "#ef4444"
    arrow = "▲" if delta > 0 else "▼"
    return f'<span style="color:{color};">{arrow} {abs(delta):.1f}{suffix}</span>'


def _compute_movement(df_res, previous_run):
    """Count keywords whose Position went up / down / unchanged vs prior scan."""
    up = down = same = new = 0
    biggest_movers = []
    for _, row in df_res.iterrows():
        kw, cur = row["Keyword"], row["Position"]
        prev = previous_run.get(kw)
        if prev is None:
            new += 1
            continue
        delta = prev - cur  # positive = moved up the SERP (lower position number)
        if delta > 0:
            up += 1
        elif delta < 0:
            down += 1
        else:
            same += 1
        if delta != 0:
            biggest_movers.append({"Keyword": kw, "Previous": prev,
                                   "Current": cur, "Δ": delta})
    return up, down, same, new, biggest_movers


def _avg_delta(movers, sign):
    """Average absolute Δ across movers in one direction. sign=1 → up, -1 → down."""
    vals = [m["Δ"] for m in movers if (m["Δ"] > 0 if sign == 1 else m["Δ"] < 0)]
    if not vals:
        return "—"
    return f"{abs(sum(vals)) / len(vals):.1f}"


def _aggregate_competitors(serp_cache, target_domain):
    """Roll up the top-10 per keyword into per-domain stats."""
    by_domain = {}
    for kw, competitors in serp_cache.items():
        for c in competitors[:10]:
            d = c.get("domain", "")
            if not d:
                continue
            by_domain.setdefault(d, []).append(c.get("position", 0))
    rows = []
    for d, positions in by_domain.items():
        positions = [p for p in positions if isinstance(p, (int, float)) and p > 0]
        if not positions:
            continue
        is_you = (d == target_domain
                  or (target_domain and d.endswith("." + target_domain)))
        rows.append({
            "Site": (f"🟢 {d} (you)" if is_you else d),
            "Appearances": len(positions),
            "Avg Position": sum(positions) / len(positions),
        })
    rows.sort(key=lambda r: (-r["Appearances"], r["Avg Position"]))
    return rows[:15]


def render_dashboard(df_res):
    all_pos = df_res["Position"].tolist()
    ranked = [p for p in all_pos if p <= 100]
    total_kw = len(df_res)
    top_3 = sum(1 for p in all_pos if p <= 3)
    top_10 = sum(1 for p in all_pos if p <= 10)
    top_20 = sum(1 for p in all_pos if p <= 20)
    top_100 = sum(1 for p in all_pos if p <= 100)
    not_ranked = total_kw - top_100
    avg_pos = sum(ranked) / len(ranked) if ranked else None

    # ---------- Deltas vs previous scan ----------
    previous_run = st.session_state.get("previous_run") or {}
    prev_avg_pos = None
    if previous_run:
        prev_ranked = [p for p in previous_run.values() if p <= 100]
        if prev_ranked:
            prev_avg_pos = sum(prev_ranked) / len(prev_ranked)
    avg_pos_delta = (
        (avg_pos - prev_avg_pos) if (avg_pos is not None and prev_avg_pos is not None) else None
    )
    moved_up, moved_down, same, new_kws, biggest_movers = _compute_movement(df_res, previous_run)

    if total_kw > 0 and top_100 == 0 and (df_res["Results Found"] > 0).any():
        st.warning(
            "⚠️ **None of your keywords ranked in the top 100, but Serper IS returning results.** "
            "Open the **SERP Inspector** tab to see who's ranking for each keyword."
        )

    # ============================================================
    # ROW 1 — KPI strip
    # ============================================================
    c1, c2, c3, c4 = st.columns(4)

    # Average Position with delta vs previous scan
    avg_val = f"{avg_pos:.1f}" if avg_pos is not None else "—"
    avg_sub = _format_delta(avg_pos_delta, lower_is_better=True) if avg_pos_delta is not None else \
              f"{len(ranked)} ranked of {total_kw}"
    c1.markdown(kpi_card("Avg Position", avg_val, avg_sub), unsafe_allow_html=True)

    # Search Visibility (top-10 share + breakdown)
    visibility_pct = round(top_10 / total_kw * 100, 1) if total_kw else 0
    vis_sub = (
        f'<span style="color:#10b981;">Top 3: {top_3}</span> · '
        f'<span style="color:#34d399;">Top 10: {top_10}</span> · '
        f'<span style="color:#fbbf24;">Top 100: {top_100}</span>'
    )
    c2.markdown(kpi_card("Search Visibility", f"{visibility_pct}%", vis_sub),
                unsafe_allow_html=True)

    # Keywords Up / Down counter (vs previous scan)
    if previous_run:
        ud_value = (
            f'<span style="color:#10b981;">▲ {moved_up}</span> &nbsp; '
            f'<span style="color:#ef4444;">▼ {moved_down}</span> &nbsp; '
            f'<span style="color:#9ca3af;">— {same}</span>'
        )
        ud_sub = f"{new_kws} new" if new_kws else "vs previous scan"
        c3.markdown(kpi_card("Keywords Up / Down", ud_value, ud_sub),
                    unsafe_allow_html=True)
    else:
        c3.markdown(kpi_card("Keywords Up / Down", "—",
                             "run again to see movement"), unsafe_allow_html=True)

    # Indexed Pages (ranked keywords) with delta
    indexed_delta = None
    if previous_run:
        prev_indexed = sum(1 for p in previous_run.values() if p <= 100)
        indexed_delta = top_100 - prev_indexed
    idx_sub = _format_delta(indexed_delta, lower_is_better=False) if indexed_delta is not None else \
              f"{not_ranked} not ranked"
    c4.markdown(kpi_card("Indexed Pages", str(top_100), idx_sub),
                unsafe_allow_html=True)

    st.markdown("####")

    # ============================================================
    # ROW 2 — Distribution donut + View Performance table
    # ============================================================
    left, right = st.columns([1, 1])

    with left:
        st.markdown("##### Keyword Distribution")
        dist = {
            "Top 3": top_3,
            "Top 10": top_10 - top_3,
            "Top 100": top_100 - top_10,
            "No rank": not_ranked,
        }
        donut_df = pd.DataFrame(
            [(k, v) for k, v in dist.items() if v > 0],
            columns=["Range", "Count"],
        )
        if not donut_df.empty:
            color_map = {"Top 3": "#10b981", "Top 10": "#3b82f6",
                         "Top 100": "#60a5fa", "No rank": "#4b5563"}
            fig = px.pie(
                donut_df, values="Count", names="Range", hole=0.65,
                color="Range", color_discrete_map=color_map,
            )
            fig.update_traces(textinfo="value", textfont_size=14,
                              hovertemplate="<b>%{label}</b><br>%{value} keywords<extra></extra>")
            fig.update_layout(
                margin=dict(l=0, r=0, t=10, b=0), height=280,
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                legend=dict(orientation="v", yanchor="middle", y=0.5,
                            xanchor="left", x=1.05),
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No keyword data yet.")

    with right:
        st.markdown("##### View Performance")
        if previous_run:
            st.caption(f"Movement vs scan from {st.session_state.previous_run_at or 'last run'}.")
            perf_rows = [
                {"View": "Keywords went up",   "Count": moved_up,
                 "Avg Δ": _avg_delta(biggest_movers, sign=1)},
                {"View": "Keywords went down",  "Count": moved_down,
                 "Avg Δ": _avg_delta(biggest_movers, sign=-1)},
                {"View": "Unchanged",           "Count": same,    "Avg Δ": "—"},
                {"View": "New keywords",        "Count": new_kws,  "Avg Δ": "—"},
            ]
            st.dataframe(pd.DataFrame(perf_rows), use_container_width=True,
                         hide_index=True, height=190)
        else:
            st.info("Run the tracker at least twice to see keyword movement here.")

    # ============================================================
    # ROW 3 — Competitor Performance
    # ============================================================
    st.markdown("##### Competitor Performance")
    st.caption("Domains most often appearing in the top 10 across your tracked keywords, "
               "and their average position. Your site is highlighted.")

    target = get_root_domain(st.session_state.get("domain", ""))
    competitor_rows = _aggregate_competitors(st.session_state.get("serp_cache") or {}, target)
    if competitor_rows:
        comp_df = pd.DataFrame(competitor_rows)
        st.dataframe(
            comp_df, use_container_width=True, hide_index=True,
            height=min(420, 45 + 35 * len(comp_df)),
            column_config={
                "Site": st.column_config.TextColumn("Site", width="medium"),
                "Appearances": st.column_config.NumberColumn(
                    "Appearances",
                    help="How many of your tracked keywords this domain ranks in (top 10).",
                ),
                "Avg Position": st.column_config.NumberColumn(
                    "Avg Position", format="%.1f",
                    help="Average position of this domain across the keywords it appears for.",
                ),
            },
        )
    else:
        st.info("Top-10 competitor data will appear once a scan has completed.")

    # ============================================================
    # ROW 4 — Quick-Win Opportunities
    # ============================================================
    opps = df_res[(df_res["Position"] >= 4) & (df_res["Position"] <= 20)].sort_values("Position")
    if not opps.empty:
        st.markdown("##### 🎯 Quick-Win Opportunities")
        st.caption("Keywords ranking in positions 4–20 — closest to page 1 / top 3.")
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
            "hint, no autocorrect override — the broadest, most user-like SERP. "
            "Add a city in **SERP Targeting → City-level Location** only if you "
            "want results geo-targeted to a specific city.\n"
            "- **Pure cumulative counter.** Rank = the Nth organic result "
            "we've seen across all pages walked so far. No per-result "
            "position-field heuristics (which are inconsistent across "
            "Serper's paginated calls).\n"
            "- **2-attempt fetch per page.** On a transient failure we retry "
            "once with a 2s sleep before giving up on that page.\n"
            "- **Walk every requested page.** Only an empty `organic` array "
            "stops the walk early. Partial pages (7–9 results) are normal on "
            "competitive SERPs and are not a signal to stop.\n"
            "- **Subdomain-tolerant match.** `folio3.com` also matches "
            "`agtech.folio3.com`, `blog.folio3.com`, etc.\n"
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
