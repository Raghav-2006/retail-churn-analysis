"""Altair chart builders for the dashboard (pure functions: DataFrame in, chart out).

Palette: the project palette from src/style.py, validated with the dataviz checker
(all checks pass; aqua is below 3:1 contrast, so every chart also has a legend or direct
labels and a data table). Dark mode uses the same hues stepped for the dark surface.
One axis per chart; thin marks; tooltips on every mark.
"""
import altair as alt
import numpy as np
import pandas as pd

LIGHT = {"series": ["#2a78d6", "#eb6834", "#1baf7a"], "muted": "#8c8b86", "text": "#52514e", "grid": "#e6e5e0"}
DARK = {"series": ["#3987e5", "#d95926", "#199e70"], "muted": "#8f8e88", "text": "#c3c2b7", "grid": "#33322f"}
STATUS = {  # reserved for correctness labels; always shown with an icon and a word
    "correct": ("#008300", "✅"), "refused": ("#008300", "✅"),
    "wrong": ("#d03b3b", "⛔"), "answered_unanswerable": ("#d03b3b", "⛔"),
    "error": ("#c98500", "⚠️"), "abstained": ("#c98500", "⚠️"),
}
OUTCOME_LABEL = {
    "correct": "Correct: result matches the reference SQL",
    "refused": "Correctly refused: the data can't answer this",
    "wrong": "Confidently wrong: answered, but the result doesn't match the reference",
    "answered_unanswerable": "Confidently wrong: answered a question the data can't answer",
    "error": "SQL error after one retry (a visible failure)",
    "abstained": "Refused a question that was answerable",
}


def palette(dark: bool = False) -> dict:
    return DARK if dark else LIGHT


def _base(chart: alt.Chart, p: dict) -> alt.Chart:
    return chart.configure_axis(gridColor=p["grid"], labelColor=p["text"], titleColor=p["text"], domain=False,
                                tickColor=p["grid"]).configure_view(strokeWidth=0).configure_legend(
        labelColor=p["text"], titleColor=p["text"], orient="top")


def monthly_revenue(monthly: pd.DataFrame, dark: bool = False) -> alt.Chart:
    p = palette(dark)
    df = monthly.assign(month=pd.to_datetime(monthly["month"]), revenue_k=monthly["revenue"].astype(float) / 1e3,
                        status=np.where(monthly["is_partial"].astype(bool), "Partial month (9 days)", "Full month"))
    line = alt.Chart(df[~df["is_partial"].astype(bool)]).mark_line(strokeWidth=2, color=p["series"][0]).encode(
        x=alt.X("month:T", title="Month", axis=alt.Axis(format="%b %Y")),
        y=alt.Y("revenue_k:Q", title="Revenue (£ thousands)"))
    points = alt.Chart(df).mark_point(filled=True, size=64).encode(
        x="month:T", y="revenue_k:Q",
        color=alt.Color("status:N", scale=alt.Scale(domain=["Full month", "Partial month (9 days)"],
                                                    range=[p["series"][0], p["muted"]]), title=None),
        tooltip=[alt.Tooltip("month:T", format="%b %Y", title="Month"),
                 alt.Tooltip("revenue:Q", format=",.0f", title="Revenue (£)"),
                 alt.Tooltip("orders:Q", format=",", title="Orders"),
                 alt.Tooltip("active_customers:Q", format=",", title="Active customers"),
                 alt.Tooltip("status:N", title="")])
    return _base((line + points).properties(height=280), p)


def tier_future_revenue(scores: pd.DataFrame, dark: bool = False) -> alt.Chart:
    p = palette(dark)
    t = (scores.groupby("tier")["future_revenue"].agg(["mean", "sum", "size"]).reset_index()
         .rename(columns={"mean": "mean_revenue", "sum": "total", "size": "customers"}))
    t["share_pct"] = 100 * t["total"] / t["total"].sum()
    t["label"] = t.apply(lambda r: f"£{r.mean_revenue:,.0f} · {r.share_pct:.0f}%", axis=1)
    bars = alt.Chart(t).mark_bar(color=p["series"][0], cornerRadiusTopLeft=4, cornerRadiusTopRight=4, width=48).encode(
        x=alt.X("tier:N", title="Tier on 1 Jun 2011", sort=["Tier 1", "Tier 2", "Tier 3", "Tier 4"],
                axis=alt.Axis(labelAngle=0)),
        y=alt.Y("mean_revenue:Q", title="Mean revenue per customer, Jun–Nov 2011 (£)"),
        tooltip=[alt.Tooltip("tier:N"), alt.Tooltip("customers:Q", format=","),
                 alt.Tooltip("mean_revenue:Q", format=",.0f", title="Mean future revenue (£)"),
                 alt.Tooltip("share_pct:Q", format=".1f", title="Share of future revenue (%)")])
    text = bars.mark_text(dy=-8, color=p["text"], fontSize=12).encode(text="label:N")
    return _base((bars + text).properties(height=280), p)


def capture_curve(scores: pd.DataFrame, dark: bool = False) -> alt.Chart:
    """Share of next-period revenue captured vs share of customers targeted, by ranking method."""
    p = palette(dark)
    total = scores["future_revenue"].sum()
    n = len(scores)
    frames = []
    for name, col in [("Tier score (v1)", "score"), ("Monetary only", "monetary")]:
        ranked = scores.sort_values([col, "customer_id"], ascending=[False, True])
        cum = ranked["future_revenue"].cumsum().to_numpy() / total * 100
        idx = np.unique(np.linspace(0, n - 1, 101).astype(int))
        frames.append(pd.DataFrame({"method": name, "customers_pct": 100 * (idx + 1) / n, "revenue_pct": cum[idx]}))
    df = pd.concat(frames)
    lines = alt.Chart(df).mark_line(strokeWidth=2).encode(
        x=alt.X("customers_pct:Q", title="Customers targeted, ranked by method (%)", scale=alt.Scale(domain=[0, 100])),
        y=alt.Y("revenue_pct:Q", title="Share of Jun–Nov 2011 revenue captured (%)", scale=alt.Scale(domain=[0, 100])),
        color=alt.Color("method:N", title=None, scale=alt.Scale(domain=["Tier score (v1)", "Monetary only"],
                                                                range=p["series"][:2])),
        tooltip=["method:N", alt.Tooltip("customers_pct:Q", format=".0f", title="Customers targeted (%)"),
                 alt.Tooltip("revenue_pct:Q", format=".1f", title="Revenue captured (%)")])
    diag = alt.Chart(pd.DataFrame({"x": [0, 100], "y": [0, 100]})).mark_line(
        strokeDash=[4, 4], strokeWidth=1, color=p["muted"]).encode(x="x:Q", y="y:Q")
    rule = alt.Chart(pd.DataFrame({"x": [10]})).mark_rule(color=p["muted"], strokeWidth=1).encode(x="x:Q")
    return _base((diag + rule + lines).properties(height=300), p)


def contributions(breakdown: pd.DataFrame, dark: bool = False) -> alt.Chart:
    """Points each feature adds to the 0-100 score; unweighted features shown by percentile only."""
    p = palette(dark)
    df = breakdown.assign(kind=np.where(breakdown["weight"] > 0, "Weighted (adds points)", "Not weighted in v1"),
                          value_str=breakdown["value"].map(lambda v: "n/a" if v is None or v != v else f"{v:,.2f}"))
    df = df.sort_values(["points", "percentile"], ascending=False)
    order = df["label"].tolist()   # one explicit order shared by both layers, so labels and bars line up
    y = alt.Y("label:N", sort=order, title=None, axis=alt.Axis(labelLimit=260))
    bars = alt.Chart(df).mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4).encode(
        y=y,
        x=alt.X("points:Q", title="Points added to the score (of 100)", scale=alt.Scale(domain=[0, 30])),
        color=alt.Color("kind:N", title=None, scale=alt.Scale(domain=["Weighted (adds points)", "Not weighted in v1"],
                                                              range=[p["series"][0], p["muted"]])),
        tooltip=[alt.Tooltip("label:N", title="Feature"), alt.Tooltip("value_str:N", title="Value"),
                 alt.Tooltip("percentile:Q", format=".1f", title="Percentile (100 = best)"),
                 alt.Tooltip("weight:Q", format=".2f", title="Weight"), alt.Tooltip("points:Q", format=".1f")])
    # labels wear the text colour, never the series colour
    text = alt.Chart(df).mark_text(align="left", dx=4).encode(
        y=y, x="points:Q", text=alt.Text("points:Q", format=".1f"), color=alt.value(p["text"]))
    return _base((bars + text).properties(height=alt.Step(30)), p)
