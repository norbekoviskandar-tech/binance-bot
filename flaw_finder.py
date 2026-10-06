"""Flaw Finder (Strategy Lab section). Takes the trades of a strategy and slices them by entry hour, weekday, volume surge,
RSI, how far the coin had already run, stop size, BTC's 24h move, exit type and coin, to show WHERE the strategy loses.

Honesty rules (slicing is the easiest way to fool yourself):
- every slice is judged on the SEARCH period first; the UNSEEN period is only the confirmation column;
- a slice is a 'confirmed leak' / 'confirmed pocket' only if it has the same sign in both periods with enough trades;
- the 'fix test' removes only slices that were bad on the search period and then measures the unseen period;
- many slices are tested at once, so a few will look bad or good by pure chance. Treat a slice as real only if there is a reason for it.
"""
import numpy as np
import pandas as pd

MIN_N = 15       # min trades in a slice on the search period to judge it
MIN_N_UNSEEN = 8  # min trades on the unseen period to confirm it
BAD = -0.05      # expectancy (R) below this = leak, above -BAD = pocket
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
# Conditions you can know BEFORE entering: these can become filters.
ENTRY_DIMS = ["Entry hour (UTC)", "Weekday", "Volume surge", "RSI at signal", "Rank in 24h gainers", "Already up in 24h",
              "Stop size (% of price)", "BTC 24h move", "Coin"]
# Outcomes: only known AFTER the trade. Shown for information, never used as filters (that would be hindsight).
OUTCOME_DIMS = ["Time in trade", "How it ended"]
DIMS = ENTRY_DIMS + OUTCOME_DIMS


def enrich(trades, data, p):
    """Attach the conditions that were true at the signal candle to every trade."""
    btc24 = None
    if "BTCUSDT" in data:
        d = data["BTCUSDT"][0]
        btc24 = pd.Series((d.c / d.c.shift(24) - 1).values * 100, index=d.t.values)
    rows = []
    for tr in trades:
        df, f, a = data[tr["sym"]]
        e = int(np.searchsorted(a["t"], tr["t_in"]))
        i = e - 1
        if i < 24 or e >= len(a["t"]) or a["t"][e] != tr["t_in"]:
            continue
        ts = pd.to_datetime(tr["t_in"], unit="ms")
        risk_pct = p["stop_pct"] if p.get("stop_pct") else p["stop_atr"] * a["atr"][i] / a["o"][e] * 100
        rows.append(dict(sym=tr["sym"], R=tr["R"], why=tr["why"], bars=tr["bars"], t_in=tr["t_in"], hour=ts.hour, dow=ts.dayofweek,
                         vol_x=f.vol_x.iat[i], rsi=f.rsi.iat[i], rank24=f.rank24.iat[i] if "rank24" in f else np.nan,
                         ext24=(a["c"][i] / a["c"][i - 24] - 1) * 100, risk_pct=risk_pct,
                         btc24=btc24.get(int(a["t"][i]), np.nan) if btc24 is not None else np.nan))
    return pd.DataFrame(rows)


def label(df):
    """Turn the raw numbers into readable buckets, one column per dimension."""
    d = pd.DataFrame(index=df.index)
    if df.empty:
        return d.assign(**{k: [] for k in DIMS})
    d["Entry hour (UTC)"] = ((df.hour // 4) * 4).map(lambda h: f"{h:02d}–{h + 3:02d}")
    d["Weekday"] = pd.Categorical(df.dow.map(dict(enumerate(DOW))), categories=DOW, ordered=True)
    cut = lambda s, bins, labs: pd.cut(s, bins, labels=labs, right=False)
    d["Volume surge"] = cut(df.vol_x, [0, 2, 3, 5, 1e9], ["<2×", "2–3×", "3–5×", "5×+"])
    d["RSI at signal"] = cut(df.rsi, [0, 50, 55, 60, 65, 101], ["<50", "50–55", "55–60", "60–65", "65+"])
    d["Rank in 24h gainers"] = cut(df.rank24, [1, 4, 7, 11, 21, 1e9], ["1–3", "4–6", "7–10", "11–20", "21+"])
    d["Already up in 24h"] = cut(df.ext24, [-1e9, 0, 3, 6, 10, 1e9], ["<0%", "0–3%", "3–6%", "6–10%", "10%+"])
    d["Stop size (% of price)"] = cut(df.risk_pct, [0, 1.5, 2.5, 4, 1e9], ["<1.5%", "1.5–2.5%", "2.5–4%", "4%+"])
    d["BTC 24h move"] = cut(df.btc24, [-1e9, -2, 0, 2, 1e9], ["below −2%", "−2–0%", "0–2%", "above +2%"])
    d["Time in trade"] = cut(df.bars, [1, 4, 13, 25, 1e9], ["1–3h", "4–12h", "13–24h", "25h+"])
    d["How it ended"] = df.why
    d["Coin"] = df.sym
    return d


def _agg(R, lab):
    return R.groupby(lab, observed=True).agg(n="size", win=lambda x: (x > 0).mean() * 100, exp="mean")


def _flag(r):
    ns, nu = r["n_s"], r["n_u"]
    if not ns >= MIN_N:
        return "too few trades"
    es, eu = r["exp_s"], r["exp_u"]
    if es < BAD:
        if nu < MIN_N_UNSEEN:
            return "❔ bad on search, too few unseen"
        return "❌ confirmed leak" if eu < 0 else "⚠️ bad on search only (noise?)"
    if es > -BAD:
        if nu < MIN_N_UNSEEN:
            return "❔ good on search, too few unseen"
        return "✅ confirmed pocket" if eu > 0 else "⚠️ good on search only (noise?)"
    return "– neutral"


def slice_table(tr, te, trl, tel, dim):
    a, b = _agg(tr.R, trl[dim]), _agg(te.R, tel[dim])
    t = a.join(b, how="outer", lsuffix="_s", rsuffix="_u")
    for c in ("n_s", "n_u"):
        t[c] = t[c].fillna(0).astype(int)
    t["verdict"] = t.apply(_flag, axis=1)
    if dim == "Coin":
        t = t[t.n_s >= MIN_N].sort_values("exp_s")
    return t


def _stats(R):
    R = np.asarray(R, float)
    if len(R) == 0:
        return 0, 0.0, 0.0, 0.0
    w, l = R[R > 0].sum(), -R[R <= 0].sum()
    return len(R), R.mean(), R.sum(), (w / l if l > 0 else 99.0)


def fix_test(tr, te, trl, tel):
    """Remove slices that were bad on the SEARCH period only, then look at the unseen period."""
    bad = {}
    for dim in ENTRY_DIMS:
        a = _agg(tr.R, trl[dim])
        bad[dim] = set(a[(a.n >= MIN_N) & (a.exp < BAD)].index)
    rows = []
    n0, e0, t0, p0 = _stats(te.R)
    rows.append(("No filter (as it is)", "–", n0, e0, t0, p0))
    for dim in ENTRY_DIMS:
        if not bad[dim] or dim == "Coin":
            continue
        keep = ~tel[dim].isin(bad[dim])
        n, e, t, p = _stats(te.R[keep])
        rows.append((f"Skip bad {dim.lower()}", ", ".join(map(str, sorted(bad[dim]))), n, e, t, p))
    combo = [d for d in ENTRY_DIMS if d != "Coin" and bad[d]]
    if len(combo) > 1:
        keep = np.ones(len(te), bool)
        for d in combo:
            keep &= ~tel[d].isin(bad[d]).values
        n, e, t, p = _stats(te.R[keep])
        rows.append(("Skip ALL of the above", f"{len(combo)} filters combined", n, e, t, p))
    return pd.DataFrame(rows, columns=["Rule (learned on search period only)", "Skipped slices", "Unseen trades", "Expectancy R", "Total R", "Profit factor"])


def build_flaw_data(data, res, fee):
    import strategy_lab as sl
    items = [("Original script", res["base"]["p"])] + [(f"Candidate #{i + 1}", b["p"]) for i, b in enumerate(res["best"])]
    out = {}
    for name, p in items:
        out[name] = dict(train=enrich(sl.run_config(data, p, fee, res["lo"], res["cut"]), data, p),
                         test=enrich(sl.run_config(data, p, fee, res["cut"], res["hi"] + 1), data, p))
    return out


def render_flaws(st, res):
    fl = res.get("flaw")
    if not fl:
        return
    st.markdown("### 🔍 Flaw finder")
    st.caption("Where does the strategy lose? Every slice is judged on the SEARCH period first; the unseen period only confirms it. "
               "Many slices are tested at once, so a few will look bad or good by luck. Only believe a slice that is ✅/❌ in BOTH periods "
               "and that you can explain (e.g. 'thin weekend liquidity').")
    names = list(fl)
    pick = st.selectbox("Strategy to inspect", names, index=1 if len(names) > 1 else 0, key="ff_pick")
    tr, te = fl[pick]["train"], fl[pick]["test"]
    if len(tr) < 30 or len(te) < 15:
        st.warning("Too few trades to slice reliably. Use more days or more coins.")
        return
    trl, tel = label(tr), label(te)
    tabs = {d: slice_table(tr, te, trl, tel, d) for d in DIMS}
    allrows = pd.concat([t.assign(dim=d, slice=t.index.astype(str)) for d, t in tabs.items() if d in ENTRY_DIMS and d != "Coin"])
    nslices = len(allrows)
    leaks = allrows[allrows.verdict == "❌ confirmed leak"].assign(lost=lambda x: x.exp_s * x.n_s).sort_values("lost")
    pockets = allrows[allrows.verdict == "✅ confirmed pocket"].assign(won=lambda x: x.exp_s * x.n_s).sort_values("won", ascending=False)
    show = lambda t: t.assign(**{"Search exp R": t.exp_s.round(3), "Unseen exp R": t.exp_u.round(3), "Search trades": t.n_s,
                                 "Unseen trades": t.n_u})[["dim", "slice", "Search trades", "Search exp R", "Unseen trades", "Unseen exp R"]] \
        .rename(columns={"dim": "Dimension", "slice": "Slice"})
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**❌ Confirmed leaks** (negative in both periods)")
        st.dataframe(show(leaks).head(8), use_container_width=True, hide_index=True) if len(leaks) else st.caption("None found.")
    with c2:
        st.markdown("**✅ Confirmed pockets** (positive in both periods)")
        st.dataframe(show(pockets).head(8), use_container_width=True, hide_index=True) if len(pockets) else st.caption("None found.")
    st.caption(f"{nslices} slices were tested, so expect a handful of fake patterns from luck alone.")
    st.markdown("**Fix test:** skip the slices that were bad on the search period, then check the unseen period")
    ft = fix_test(tr, te, trl, tel)
    st.dataframe(ft.round(3), use_container_width=True, hide_index=True)
    st.caption("Skipping trades after the fact ignores that a skipped trade would have freed that coin for a later one, so treat this as an estimate. "
               "If a filter does not improve the unseen numbers, it was noise, do not add it to the bot.")
    st.markdown("**All slices**")
    for dim, t in tabs.items():
        with st.expander(dim + ("  (result of the trade, not a filter)" if dim in OUTCOME_DIMS else ""), expanded=False):
            out = t.rename(columns={"n_s": "Search trades", "win_s": "Search win %", "exp_s": "Search exp R", "n_u": "Unseen trades",
                                    "win_u": "Unseen win %", "exp_u": "Unseen exp R", "verdict": "Verdict"}).round(3)
            st.dataframe(out, use_container_width=True)
