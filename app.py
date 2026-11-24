"""Healthy Habit Tracker - Streamlit GUI.

Run:  streamlit run app.py
Uses the same core modules and the same data file as the terminal app.
"""
from __future__ import annotations

import html
import io
import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from core.alerts import CRITICAL, INFO, WARNING, count_by_level, generate_alerts
from core.analytics import available_weeks, default_week, overall_stats, summarize_week, week_start
from core.charts import (ChartError, distribution_figure, figure_bytes, goal_completion_figure, habit_colors,
                         progress_trend_figure, weekly_trend_figure)
from core.importer import ImportFormatError, import_csv_stream, import_json_text
from core.models import fmt_value
from core.reports import export_reports, render_csv_report, render_markdown, render_text_audit
from core.search import SORT_OPTIONS, paginate, search_entries
from core.seed import seed_tracker
from core.storage import StorageError
from core.tracker import DEFAULT_DATA_PATH, Tracker
from core.validation import ValidationError, parse_number

ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "reports"
DATA_PATH = Path(os.environ.get("HABIT_DATA", DEFAULT_DATA_PATH))

st.set_page_config(page_title="Healthy Habit Tracker", page_icon="🌿", layout="wide")

INK, MIST, SPROUT = "#12332E", "#F2F6F3", "#2F9E6B"
STATUS_COLORS = {"Achieved": "#1E8A5A", "On track": "#2B7FB8", "Behind": "#B7791F", "Off track": "#C0392B", "No data": "#7A8B85"}

st.markdown(
    f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;800&display=swap');
.stApp, .stMarkdown, p, label, h1, h2, h3, h4, button, input, textarea, [data-testid="stSidebar"] {{ font-family: 'Manrope', 'Segoe UI', sans-serif; }}
span[data-testid="stIconMaterial"], .material-icons, .material-symbols-rounded {{ font-family: 'Material Symbols Rounded' !important; }}
.stApp {{ background: {MIST}; color: {INK}; }}
h1, h2, h3 {{ color: {INK}; letter-spacing: -0.01em; }}
section[data-testid="stSidebar"] {{ background: {INK}; }}
section[data-testid="stSidebar"] * {{ color: #E7F1EC !important; }}
.hh-brand {{ font-size: 1.35rem; font-weight: 800; line-height: 1.2; margin-bottom: .2rem; }}
.hh-sub {{ opacity: .75; font-size: .85rem; margin-bottom: 1rem; }}
.hh-hero {{ display:flex; align-items:baseline; gap:1.2rem; flex-wrap:wrap; margin:.2rem 0 1rem 0; }}
.hh-score {{ font-size: 4.2rem; font-weight: 800; line-height: 1; color: {INK}; }}
.hh-score small {{ font-size: 1.3rem; font-weight: 600; opacity: .55; }}
.hh-range {{ font-size: 1rem; opacity: .75; }}
.hh-pill {{ display:inline-block; padding: .15rem .65rem; border-radius: 999px; font-size: .8rem; font-weight: 600; color: white; }}
.hh-card {{ background: white; border-radius: 14px; padding: 1rem 1.1rem; border-left: 6px solid var(--c); box-shadow: 0 1px 2px rgba(18,51,46,.08); }}
.hh-card h4 {{ margin: 0 0 .2rem 0; font-size: 1rem; font-weight: 700; }}
.hh-big {{ font-size: 2rem; font-weight: 800; line-height: 1.1; }}
.hh-meta {{ font-size: .82rem; opacity: .8; margin-top: .45rem; line-height: 1.5; }}
.hh-track {{ background: #E3ECE7; border-radius: 99px; height: 8px; margin-top: .5rem; overflow: hidden; }}
.hh-fill {{ height: 8px; border-radius: 99px; background: var(--c); }}
table.hh-strip {{ border-collapse: separate; border-spacing: 6px; width: 100%; }}
table.hh-strip th {{ font-weight: 600; font-size: .8rem; opacity: .75; text-align: center; padding-bottom: 2px; }}
table.hh-strip td.name {{ font-weight: 700; font-size: .9rem; white-space: nowrap; padding-right: .6rem; }}
table.hh-strip td.cell {{ text-align: center; border-radius: 10px; padding: .85rem .2rem; font-weight: 700; font-size: .95rem; }}
table.hh-strip td.cell small {{ display:block; font-weight: 500; opacity: .7; font-size: .68rem; }}
.hh-future {{ background: transparent; border: 1.5px dashed #B9C9C1; color: #8A9A93; }}
.hh-empty {{ background: #E3ECE7; color: #6A7C75; }}
.hh-breach {{ outline: 2.5px solid #C0392B; }}
:focus-visible {{ outline: 3px solid {SPROUT} !important; outline-offset: 2px; }}
@media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; animation: none !important; }} }}
</style>
""",
    unsafe_allow_html=True,
)


# ----------------------------------------------------------------------- helpers
def esc(text: object) -> str:
    return html.escape(str(text))


def flash(kind: str, message: str) -> None:
    st.session_state["flash"] = (kind, message)


def show_flash() -> None:
    item = st.session_state.pop("flash", None)
    if item:
        {"success": st.success, "error": st.error, "warning": st.warning}.get(item[0], st.info)(item[1])


def hex_rgba(color: str, alpha: float) -> str:
    color = color.lstrip("#")
    r, g, b = int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)
    return f"rgba({r},{g},{b},{alpha:.2f})"


def pill(status: str) -> str:
    return f'<span class="hh-pill" style="background:{STATUS_COLORS.get(status, "#7A8B85")}">{esc(status)}</span>'


def week_label(start: date) -> str:
    return f"{start:%d %b} – {start + timedelta(days=6):%d %b %Y}"


def week_picker(tracker: Tracker, key: str) -> date:
    weeks = set(available_weeks(tracker.entries)) | {week_start(tracker.today)}
    options = sorted(weeks, reverse=True)
    default = default_week(tracker.entries, tracker.today)
    return st.selectbox("Week", options, index=options.index(default), format_func=week_label, key=key)


def show_alerts(alerts, limit: int = 4) -> None:
    box = {CRITICAL: st.error, WARNING: st.warning, INFO: st.info}
    icon = {CRITICAL: "🚨", WARNING: "⚠️", INFO: "ℹ️"}
    for a in alerts[:limit]:
        box[a.level](a.message, icon=icon[a.level])
    if len(alerts) > limit:
        with st.expander(f"Show {len(alerts) - limit} more notice(s)"):
            for a in alerts[limit:]:
                box[a.level](a.message, icon=icon[a.level])


def entries_frame(tracker: Tracker, entries) -> pd.DataFrame:
    return pd.DataFrame(
        [{"ID": e.id, "Date": e.date.isoformat(), "Day": e.date.strftime("%a"), "Habit": e.habit, "Value": e.value,
          "Unit": tracker.habits[e.habit].unit if e.habit in tracker.habits else "", "Notes": e.notes} for e in entries],
        columns=["ID", "Date", "Day", "Habit", "Value", "Unit", "Notes"],
    )


def habit_card(w, color: str) -> str:
    pct = min(w.completion_pct, 100)
    return f"""
<div class="hh-card" style="--c:{color}">
  <h4>{esc(w.habit)}</h4>
  <div class="hh-big">{w.completion_pct:.0f}<span style="font-size:1rem;font-weight:600;opacity:.6">% of goal</span></div>
  <div class="hh-track"><div class="hh-fill" style="width:{pct:.0f}%"></div></div>
  <div class="hh-meta">
    {pill(w.status)}<br>
    {fmt_value(w.total)} {esc(w.unit)} total · {fmt_value(w.average)} {esc(w.unit)}/day<br>
    Goal met on {w.days_goal_met} of {w.elapsed_days} days · Streak {w.streak}<br>
    Trend: {esc(w.trend)}
  </div>
</div>"""


def week_strip(summary, tracker: Tracker, colors) -> str:
    head = "".join(f"<th>{d:%a}<br>{d:%d}</th>" for d in summary.habits[0].days)
    rows = []
    for w in summary.habits:
        cfg = tracker.habits[w.habit]
        cells = []
        for day, value in zip(w.days, w.daily):
            if day > tracker.today:
                cells.append('<td class="cell hh-future">·</td>')
            elif value == 0:
                cells.append('<td class="cell hh-empty">–</td>')
            else:
                alpha = 0.18 + 0.72 * min(value / w.goal, 1.0)
                text = "#FFFFFF" if alpha > 0.55 else INK
                breach = " hh-breach" if value > cfg.daily_limit else ""
                cells.append(f'<td class="cell{breach}" style="background:{hex_rgba(colors[w.habit], alpha)};color:{text}">'
                             f'{fmt_value(value)}<small>{esc(w.unit)}</small></td>')
        rows.append(f'<tr><td class="name">{esc(w.habit)}</td>{"".join(cells)}</tr>')
    return f'<table class="hh-strip"><thead><tr><th></th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'


# ------------------------------------------------------------------------- pages
def page_dashboard(tracker: Tracker) -> None:
    st.title("Your week")
    if not tracker.entries:
        st.info("No entries yet. Log your first one on the **Log entry** page, or load the demo data to explore.")
        if st.button("Load demo data"):
            seed_tracker(tracker)
            flash("success", "Loaded 50 demo entries.")
            st.rerun()
        return

    start = week_picker(tracker, "dash_week")
    summary = summarize_week(tracker.entries, tracker.habits, start, tracker.today)
    colors = habit_colors(tracker.habits)

    st.markdown(
        f'<div class="hh-hero"><div class="hh-score">{summary.wellness_score:.0f}<small>/100</small></div>'
        f'<div><div class="hh-range">Wellness score · {esc(week_label(start))}</div>{pill(summary.overall_status)}'
        f' &nbsp;<span class="hh-range">{summary.total_entries} entries</span></div></div>',
        unsafe_allow_html=True,
    )

    alerts = generate_alerts(tracker.entries, tracker.habits, tracker.today)
    important = [a for a in alerts if a.level in (CRITICAL, WARNING)]
    if important:
        show_alerts(important, limit=3)

    columns = st.columns(max(1, len(summary.habits)))
    for column, w in zip(columns, summary.habits):
        column.markdown(habit_card(w, colors[w.habit]), unsafe_allow_html=True)

    st.subheader("Day by day")
    st.markdown(week_strip(summary, tracker, colors), unsafe_allow_html=True)
    st.caption("Colour depth shows progress toward each day's goal. A red outline marks a day above the safety limit.")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Executive summary")
        table = pd.DataFrame([{
            "Habit": w.habit, "Total": f"{fmt_value(w.total)} {w.unit}", "Avg / day": f"{fmt_value(w.average)} {w.unit}",
            "Goal days": f"{w.days_goal_met}/{w.elapsed_days}", "Completion": f"{w.completion_pct:.0f}%",
            "Status": w.status, "Trend": w.trend, "Streak": w.streak} for w in summary.habits])
        st.dataframe(table, hide_index=True)
    with right:
        st.subheader("Key trends")
        for note in summary.insights:
            st.markdown(f"- {note}")

    st.subheader("Charts")
    try:
        tab1, tab2, tab3, tab4 = st.tabs(["Weekly trend", "Goal completion", "Distribution", "Progress over time"])
        import matplotlib.pyplot as plt

        with tab1:
            fig = weekly_trend_figure(tracker.entries, tracker.habits, start, tracker.today)
            st.pyplot(fig)
            plt.close(fig)
        with tab2:
            fig = goal_completion_figure(summary, tracker.habits)
            st.pyplot(fig)
            plt.close(fig)
        with tab3:
            fig = distribution_figure(tracker.entries, tracker.habits)
            st.pyplot(fig)
            plt.close(fig)
        with tab4:
            fig = progress_trend_figure(tracker.entries, tracker.habits, tracker.today)
            st.pyplot(fig)
            plt.close(fig)
    except (ChartError, ImportError) as exc:
        st.warning(f"Charts are unavailable: {exc}. Install Matplotlib with `pip install matplotlib`.")


def page_add(tracker: Tracker) -> None:
    st.title("Log an entry")
    names = list(tracker.habits)
    habit = st.selectbox("Habit", names)
    cfg = tracker.habits[habit]
    with st.form("add_entry", clear_on_submit=True):
        c1, c2 = st.columns(2)
        day = c1.date_input("Date", value=tracker.today, max_value=tracker.today)
        raw_value = c2.text_input(f"Value ({cfg.unit})", placeholder=f"{fmt_value(cfg.min_value)} to {fmt_value(cfg.max_value)}")
        notes = st.text_input("Notes (optional)", max_chars=200)
        submitted = st.form_submit_button("Save entry")
    st.caption(f"Daily goal: {fmt_value(cfg.daily_goal)} {cfg.unit} · safety limit: {fmt_value(cfg.daily_limit)} {cfg.unit} per day.")
    if submitted:
        try:
            entry = tracker.add_entry(day, habit, raw_value, notes)
        except ValidationError as exc:
            st.error(str(exc))
            return
        except StorageError as exc:
            st.error(f"Could not save: {exc}")
            return
        total = sum(e.value for e in tracker.entries if e.habit == habit and e.date == entry.date)
        st.success(f"Saved #{entry.id}: {habit} {fmt_value(entry.value)} {cfg.unit} on {entry.date:%a %d %b}.")
        st.progress(min(total / cfg.daily_goal, 1.0), text=f"Day total {fmt_value(total)} of {fmt_value(cfg.daily_goal)} {cfg.unit}")
        if total > cfg.daily_limit:
            st.error(f"That puts {habit} at {fmt_value(total)} {cfg.unit} for the day, above the safety limit of "
                     f"{fmt_value(cfg.daily_limit)} {cfg.unit}.")


def page_search(tracker: Tracker) -> None:
    st.title("Entries and search")
    if not tracker.entries:
        st.info("Nothing to search yet. Log an entry first.")
        return
    c1, c2, c3 = st.columns([2, 1, 1])
    keyword = c1.text_input("Keyword", placeholder="notes, habit or date, e.g. library")
    habit = c2.selectbox("Habit", ["All"] + list(tracker.habits))
    sort = c3.selectbox("Sort", list(SORT_OPTIONS), format_func=SORT_OPTIONS.get)
    c4, c5, c6 = st.columns(3)
    use_dates = c4.checkbox("Filter by dates")
    d_from = d_to = None
    if use_dates:
        first = min(e.date for e in tracker.entries)
        picked = c4.date_input("Range", value=(first, tracker.today), max_value=tracker.today)
        if isinstance(picked, (tuple, list)) and len(picked) == 2:
            d_from, d_to = picked
    raw_low = c5.text_input("Minimum value", placeholder="any")
    raw_high = c6.text_input("Maximum value", placeholder="any")
    try:
        low = parse_number(raw_low, "Minimum") if raw_low.strip() else None
        high = parse_number(raw_high, "Maximum") if raw_high.strip() else None
    except ValidationError as exc:
        st.error(str(exc))
        return

    results = search_entries(tracker.entries, keyword, None if habit == "All" else habit, d_from, d_to, low, high, sort)
    size = st.select_slider("Rows per page", options=[10, 25, 50, 100], value=10)
    _, page, pages = paginate(results, 1, size)
    if pages > 1:
        page = st.number_input("Page", min_value=1, max_value=pages, value=1, step=1)
    chunk, page, pages = paginate(results, int(page), size)
    st.caption(f"{len(results)} matching record(s) · page {page} of {pages}")
    st.dataframe(entries_frame(tracker, chunk), hide_index=True)

    with st.expander("Edit or delete an entry"):
        if not results:
            st.write("No entries to edit.")
            return
        options = {f"#{e.id} · {e.date} · {e.habit} {fmt_value(e.value)}": e.id for e in results[:300]}
        label = st.selectbox("Entry", list(options))
        entry = tracker.get_entry(options[label])
        if entry is None:
            return
        with st.form(f"edit_{entry.id}"):
            e_day = st.date_input("Date", value=entry.date, max_value=tracker.today, key=f"d{entry.id}")
            e_habit = st.selectbox("Habit", list(tracker.habits), index=list(tracker.habits).index(entry.habit), key=f"h{entry.id}")
            e_value = st.text_input("Value", value=fmt_value(entry.value), key=f"v{entry.id}")
            e_notes = st.text_input("Notes", value=entry.notes, max_chars=200, key=f"n{entry.id}")
            save = st.form_submit_button("Save changes")
        if save:
            try:
                tracker.update_entry(entry.id, e_day, e_habit, e_value, e_notes)
                flash("success", f"Updated entry #{entry.id}.")
                st.rerun()
            except (ValidationError, StorageError) as exc:
                st.error(str(exc))
        confirm = st.checkbox(f"Yes, delete entry #{entry.id}", key=f"c{entry.id}")
        if st.button("Delete entry", disabled=not confirm):
            tracker.delete_entries([entry.id])
            flash("success", f"Deleted entry #{entry.id}.")
            st.rerun()


def page_reports(tracker: Tracker) -> None:
    st.title("Reports and export")
    if not tracker.entries:
        st.info("Log some entries first, then export a report here.")
        return
    start = week_picker(tracker, "rep_week")
    summary = summarize_week(tracker.entries, tracker.habits, start, tracker.today)
    alerts = generate_alerts(tracker.entries, tracker.habits, tracker.today)
    markdown = render_markdown(summary, alerts, tracker.entries, tracker.habits)
    audit = render_text_audit(summary, alerts, tracker.entries, tracker.habits)
    csv_text = render_csv_report(summary)

    d1, d2, d3 = st.columns(3)
    d1.download_button("Download Markdown", markdown, f"summary_{start}.md", "text/markdown")
    d2.download_button("Download audit log (.txt)", audit, f"audit_log_{start}.txt", "text/plain")
    d3.download_button("Download CSV report", csv_text, f"report_{start}.csv", "text/csv")
    st.download_button("Download all entries (CSV)", _entries_csv(tracker), "all_entries.csv", "text/csv")

    if st.button("Save all reports and charts to the reports/ folder"):
        try:
            files, warnings = export_reports(tracker.entries, tracker.habits, start, tracker.today, REPORT_DIR)
            st.success("Saved " + ", ".join(p.name for p in files.values()))
            for warning in warnings:
                st.warning(warning)
        except StorageError as exc:
            st.error(str(exc))

    st.subheader("Preview")
    st.markdown(markdown)


def _entries_csv(tracker: Tracker) -> str:
    import csv

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["id", "date", "habit", "value", "notes"])
    for e in sorted(tracker.entries, key=lambda e: (e.date, e.id)):
        writer.writerow([e.id, e.date.isoformat(), e.habit, e.value, e.notes])
    return out.getvalue()


def page_import(tracker: Tracker) -> None:
    st.title("Bulk import")
    st.write("Upload a legacy **CSV** or **JSON** file. Required columns: `date`, `habit`, `value` (optional `notes`). "
             "Valid rows are imported; every rejected row is listed with its line number and reason.")
    sample = ROOT / "data" / "sample_import_with_errors.csv"
    if sample.exists():
        st.download_button("Download a sample file with deliberate errors", sample.read_text(encoding="utf-8"),
                           "sample_import_with_errors.csv", "text/csv")
    upload = st.file_uploader("Choose a file", type=["csv", "json"])
    if upload is None:
        return
    if not st.button("Import file"):
        return
    try:
        text = upload.getvalue().decode("utf-8-sig")
    except UnicodeDecodeError:
        st.error("The file is not UTF-8 text. Re-save it as 'CSV UTF-8' and try again.")
        return
    log_path = tracker.storage.path.with_name("import_errors.log")
    try:
        if upload.name.lower().endswith(".json"):
            result = import_json_text(text, tracker, upload.name, log_path)
        else:
            result = import_csv_stream(io.StringIO(text, newline=""), tracker, upload.name, log_path)
    except ImportFormatError as exc:
        st.error(str(exc))
        return
    except StorageError as exc:
        st.error(f"Could not save imported data: {exc}")
        return
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Rows read", result.total_rows)
    m2.metric("Imported", result.imported)
    m3.metric("Duplicates skipped", result.duplicates)
    m4.metric("Rejected", result.failed)
    if result.imported:
        st.success(f"Imported {result.imported} entries.")
    if result.errors:
        st.warning("Some rows were rejected:")
        st.dataframe(pd.DataFrame([{"Line": e.line, "Problem": e.reason, "Row": e.raw} for e in result.errors]), hide_index=True)
        log_text = "\n".join(f"line {e.line}: {e.reason} | {e.raw}" for e in result.errors)
        st.download_button("Download error log", log_text, "import_errors.txt", "text/plain")


def page_settings(tracker: Tracker) -> None:
    st.title("Habits and data")
    st.subheader("Your habits")
    st.dataframe(pd.DataFrame([{"Habit": c.name, "Unit": c.unit, "Entry range": f"{fmt_value(c.min_value)}–{fmt_value(c.max_value)}",
                                "Daily goal": c.daily_goal, "Safety limit": c.daily_limit} for c in tracker.habits.values()]),
                 hide_index=True)

    with st.expander("Change a goal or limit"):
        name = st.selectbox("Habit", list(tracker.habits), key="edit_habit_name")
        cfg = tracker.habits[name]
        with st.form(f"habit_edit_{name}"):
            goal = st.text_input("Daily goal", value=fmt_value(cfg.daily_goal))
            limit = st.text_input("Daily safety limit", value=fmt_value(cfg.daily_limit))
            go = st.form_submit_button("Save")
        if go:
            try:
                tracker.update_habit(name, cfg.unit, cfg.min_value, cfg.max_value, goal, limit)
                flash("success", f"Updated {name}.")
                st.rerun()
            except (ValidationError, StorageError) as exc:
                st.error(str(exc))

    with st.expander("Add a habit"):
        with st.form("habit_add", clear_on_submit=True):
            a1, a2 = st.columns(2)
            new_name = a1.text_input("Name", placeholder="Meditation")
            new_unit = a2.text_input("Unit", placeholder="min")
            b1, b2, b3, b4 = st.columns(4)
            n_min = b1.text_input("Min per entry", value="0")
            n_max = b2.text_input("Max per entry", value="120")
            n_goal = b3.text_input("Daily goal", value="10")
            n_limit = b4.text_input("Daily limit", value="240")
            add = st.form_submit_button("Add habit")
        if add:
            try:
                cfg = tracker.add_habit(new_name, new_unit, n_min, n_max, n_goal, n_limit)
                flash("success", f"Added habit {cfg.name}.")
                st.rerun()
            except (ValidationError, StorageError) as exc:
                st.error(str(exc))

    with st.expander("Remove a habit"):
        victim = st.selectbox("Habit to remove", list(tracker.habits), key="remove_habit_name")
        if st.button("Remove habit"):
            try:
                tracker.remove_habit(victim)
                flash("success", f"Removed {victim}.")
                st.rerun()
            except (ValidationError, StorageError) as exc:
                st.error(str(exc))

    st.subheader("Data")
    stats = overall_stats(tracker.entries, tracker.habits)
    st.write(f"{stats['entries']} entries" + (f" from {stats['first']} to {stats['last']}." if stats["entries"] else "."))
    st.caption(f"Stored in {tracker.storage.path}")
    with st.expander("Load demo data"):
        st.write("Replaces all entries with 50 realistic ones ending today (boundary values, a limit breach, a missed log).")
        ok = st.checkbox("I understand this replaces my current entries")
        if st.button("Load demo data", disabled=not ok):
            seed_tracker(tracker)
            flash("success", "Loaded 50 demo entries.")
            st.rerun()
    with st.expander("Clear all entries"):
        ok = st.checkbox("Yes, delete every entry", key="confirm_clear")
        if st.button("Clear all entries", disabled=not ok):
            tracker.clear_entries()
            flash("success", "All entries deleted.")
            st.rerun()


PAGES = {
    "Dashboard": page_dashboard,
    "Log entry": page_add,
    "Entries and search": page_search,
    "Reports and export": page_reports,
    "Bulk import": page_import,
    "Habits and data": page_settings,
}


def main() -> None:
    try:
        tracker = Tracker(DATA_PATH)
    except StorageError as exc:
        st.error(f"Cannot open the data file: {exc}")
        st.stop()
        return

    with st.sidebar:
        st.markdown('<div class="hh-brand">Healthy Habit Tracker</div>'
                    '<div class="hh-sub">Reading · exercise · hydration</div>', unsafe_allow_html=True)
        page = st.radio("Go to", list(PAGES), label_visibility="collapsed")
        counts = count_by_level(generate_alerts(tracker.entries, tracker.habits, tracker.today))
        if counts[CRITICAL] or counts[WARNING]:
            st.markdown(f"**{counts[CRITICAL]}** critical · **{counts[WARNING]}** warnings")
        st.caption(f"{len(tracker.entries)} entries saved · {tracker.today:%a %d %b %Y}")

    if tracker.notices and not st.session_state.get("notices_shown"):
        for notice in tracker.notices:
            st.info(notice)
        st.session_state["notices_shown"] = True
    show_flash()
    PAGES[page](tracker)


main()
