"""
Allocation Output Publisher — Streamlit app (planner login only)

Screen 1 · Optimizer output   : load the output of the allocation optimizer (results_output.pkl /
                                CSV / Excel, or demo output), set the run parameters, review it.
Screen 2 · Publish to BigQuery : choose the destination, check, and write the tables to BigQuery.

The optimizer itself is NOT part of this app — it runs in the pipeline (model_main.py).

Demo mode (default): a local SQLite file stands in for BigQuery.
Connected mode: add [gcp_service_account] (and optional [bigquery]) to Streamlit secrets and
install google-cloud-bigquery + pyarrow.

Add real users in secrets — generate a block with:
    python app.py adduser <username> "<Full Name>" <password>
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import pickle
import re
import secrets as pysecrets
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

DATA_DIR = Path(os.environ.get("ALLOC_DATA_DIR", Path(__file__).resolve().parent / "data"))
DEMO_DB = DATA_DIR / "bigquery_demo.sqlite"
PUB_LOG = DATA_DIR / "publications.json"


# ======================================================================================
# Auth — planner only
# ======================================================================================
def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()


def make_user(name: str, password: str) -> dict:
    salt = pysecrets.token_hex(16)
    return {"name": name, "role": "planner", "salt": salt, "password_hash": hash_password(password, salt)}


_DEMO_SALT = "8f1d2c3b4a5968778695a4b3c2d1e0f1"
DEMO_USERS = {"yogesh": {"name": "Yogesh", "role": "planner", "salt": _DEMO_SALT,
                         "password_hash": hash_password("planner123", _DEMO_SALT)}}
DEMO_PASSWORD = "planner123"

if __name__ == "__main__" and len(sys.argv) >= 2 and sys.argv[1] == "adduser":
    if len(sys.argv) != 5:
        print('usage: python app.py adduser <username> "<Full Name>" <password>')
        sys.exit(1)
    _u, _n, _p = sys.argv[2:]
    print(f"[users.{_u}]")
    for _k, _v in make_user(_n, _p).items():
        print(f"{_k} = {json.dumps(_v)}")
    sys.exit(0)

import streamlit as st  # noqa: E402

st.set_page_config(page_title="Allocation Output Publisher", page_icon=":material/upload:", layout="wide")
ss = st.session_state
for _k in ["src", "month", "run_id", "env", "model_version", "notes", "gcs_path"]:  # keep values across pages
    if _k in ss:
        ss[_k] = ss[_k]


def _secrets() -> dict:
    try:
        return st.secrets
    except Exception:
        return {}


def load_users() -> tuple[dict, bool]:
    try:
        users = {k: dict(v) for k, v in _secrets()["users"].items()}
        users = {k: v for k, v in users.items() if v.get("role", "planner") == "planner"}
        if users:
            return users, False
    except Exception:
        pass
    return DEMO_USERS, True


def check_login(users, username, password):
    u = users.get((username or "").strip().lower())
    if not u:
        hash_password(password or "", "00" * 16)
        return None
    if hmac.compare_digest(hash_password(password or "", u["salt"]), u["password_hash"]):
        return {"username": username.strip().lower(), "name": u["name"]}
    return None


# ======================================================================================
# Demo optimizer output (static sample of results_output.pkl — no optimizer code here)
# ======================================================================================
DEMO_ALLOCATION = [  # warehouse, zone, product_type, storage, units, unit_cost, cost, method
    ('CFC 2 · Central', 'East', '410-520-low', 'FLA', 40.0, 2.1, 84.0, 'Slow seller'),
    ('CFC 2 · Central', 'Central', '410-520-low', 'FLA', 35.0, 1.2, 42.0, 'Slow seller'),
    ('CFC 2 · Central', 'West', '410-520-low', 'FLA', 25.0, 2.1, 52.5, 'Slow seller'),
    ('CFC 2 · Central', 'East', '415-525-low', 'HFG', 20.0, 4.9, 98.0, 'Slow seller'),
    ('CFC 2 · Central', 'Central', '415-525-low', 'HFG', 10.0, 2.8, 28.0, 'Slow seller'),
    ('CFC 2 · Central', 'West', '415-525-low', 'HFG', 30.0, 4.9, 147.0, 'Slow seller'),
    ('CFC 1 · East', '(no demand)', '520-610-high', 'FLA', 20.0, None, 0.0, 'No demand'),
    ('CFC 2 · Central', '(no demand)', '520-610-high', 'FLA', 20.0, None, 0.0, 'No demand'),
    ('CFC 3 · West', '(no demand)', '520-610-high', 'FLA', 20.0, None, 0.0, 'No demand'),
    ('CFC 1 · East', 'East', '610-700-high', 'FLA', 80.0, 0.8, 64.0, 'Restricted'),
    ('CFC 1 · East', 'Central', '610-700-high', 'FLA', 60.0, 1.4, 84.0, 'Restricted'),
    ('CFC 1 · East', 'West', '610-700-high', 'FLA', 70.0, 2.0, 140.0, 'Restricted'),
    ('CFC 1 · East', 'East', '101-210-high', 'FLA', 600.0, 2.0, 1200.0, 'Optimizer'),
    ('CFC 2 · Central', 'Central', '101-210-high', 'FLA', 300.0, 2.0, 600.0, 'Optimizer'),
    ('CFC 3 · West', 'West', '101-210-high', 'FLA', 400.0, 2.0, 800.0, 'Optimizer'),
    ('CFC 1 · East', 'East', '101-215-high', 'FLA', 450.0, 1.8, 810.0, 'Optimizer'),
    ('CFC 2 · Central', 'Central', '101-215-high', 'FLA', 350.0, 1.8, 630.0, 'Optimizer'),
    ('CFC 3 · West', 'West', '101-215-high', 'FLA', 500.0, 1.8, 900.0, 'Optimizer'),
    ('CFC 1 · East', 'East', '202-330-medium', 'HFG', 200.0, 2.4, 480.0, 'Optimizer'),
    ('CFC 1 · East', 'Central', '202-330-medium', 'HFG', 50.0, 4.2, 210.0, 'Optimizer'),
    ('CFC 3 · West', 'Central', '202-330-medium', 'HFG', 100.0, 4.2, 420.0, 'Optimizer'),
    ('CFC 3 · West', 'West', '202-330-medium', 'HFG', 300.0, 2.4, 720.0, 'Optimizer'),
    ('CFC 2 · Central', 'East', '202-340-medium', 'HFG', 120.0, 4.55, 546.0, 'Optimizer'),
    ('CFC 2 · Central', 'Central', '202-340-medium', 'HFG', 180.0, 2.6, 468.0, 'Optimizer'),
    ('CFC 2 · Central', 'West', '202-340-medium', 'HFG', 90.0, 4.55, 409.5, 'Optimizer'),
    ('CFC 1 · East', 'East', '305-410-high', 'HFG', 250.0, 3.0, 750.0, 'Optimizer'),
    ('CFC 1 · East', 'Central', '305-410-high', 'HFG', 200.0, 5.25, 1050.0, 'Optimizer'),
    ('CFC 3 · West', 'West', '305-410-high', 'HFG', 150.0, 3.0, 450.0, 'Optimizer'),
]

DEMO_CAPACITY = {'FLA': {'CFC 1 · East': 2000, 'CFC 2 · Central': 1500, 'CFC 3 · West': 1800}, 'HFG': {'CFC 1 · East': 700, 'CFC 2 · Central': 450, 'CFC 3 · West': 500}}
DEMO_LIMITS = {'min_pct': {'CFC 1 · East': 25.0, 'CFC 2 · Central': 20.0, 'CFC 3 · West': 25.0}, 'max_pct': {'CFC 1 · East': 50.0, 'CFC 2 · Central': 40.0, 'CFC 3 · West': 45.0}}

REQUIRED = ["warehouse", "zone", "product_type", "storage", "units"]
OPTIONAL = ["unit_cost", "cost", "method"]

BQ_TABLES = {
    "allocation_plan": {
        "desc": "Units to stock per warehouse × zone × product type, with cost and how it was decided.",
        "schema": [("run_id", "STRING"), ("target_month", "DATE"), ("warehouse", "STRING"), ("zone", "STRING"),
                   ("product_type", "STRING"), ("storage", "STRING"), ("units", "FLOAT64"),
                   ("unit_cost", "FLOAT64"), ("cost", "FLOAT64"), ("method", "STRING"),
                   ("published_at", "TIMESTAMP")]},
    "capacity_summary": {
        "desc": "Space used per warehouse × storage type, with overflow above capacity.",
        "schema": [("run_id", "STRING"), ("target_month", "DATE"), ("warehouse", "STRING"), ("storage", "STRING"),
                   ("allocated", "FLOAT64"), ("capacity", "FLOAT64"), ("used_pct", "FLOAT64"),
                   ("overflow", "FLOAT64"), ("published_at", "TIMESTAMP")]},
    "warehouse_share": {
        "desc": "Each warehouse's share of all units vs its min / max target.",
        "schema": [("run_id", "STRING"), ("target_month", "DATE"), ("warehouse", "STRING"), ("units", "FLOAT64"),
                   ("share_pct", "FLOAT64"), ("min_pct", "FLOAT64"), ("max_pct", "FLOAT64"),
                   ("published_at", "TIMESTAMP")]},
    "run_log": {
        "desc": "One row per publish: run parameters, totals, source file and who published it.",
        "schema": [("run_id", "STRING"), ("target_month", "DATE"), ("pipeline_env", "STRING"),
                   ("model_version", "STRING"), ("solver_status", "STRING"), ("source", "STRING"),
                   ("total_units", "FLOAT64"), ("total_cost", "FLOAT64"), ("overflow_units", "FLOAT64"),
                   ("row_count", "INT64"), ("notes", "STRING"), ("published_by", "STRING"),
                   ("published_at", "TIMESTAMP")]},
}
_ID_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_ID_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def demo_output() -> dict:
    alloc = pd.DataFrame(DEMO_ALLOCATION, columns=REQUIRED[:4] + ["units", "unit_cost", "cost", "method"])
    cap = pd.DataFrame(DEMO_CAPACITY).rename_axis("warehouse").reset_index().melt(
        id_vars="warehouse", var_name="storage", value_name="capacity")
    lim = pd.DataFrame(DEMO_LIMITS).rename_axis("warehouse").reset_index()
    return {"allocation": alloc, "capacity": cap, "limits": lim, "solver_status": "Optimal",
            "source": "Demo output (sample results_output.pkl)"}


# ======================================================================================
# Reading optimizer output files
# ======================================================================================
def _norm(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    alias = {"loc": "warehouse", "cfc": "warehouse", "dest": "zone", "destination": "zone", "prod_type": "product_type",
             "strg_type": "storage", "storage_type": "storage", "supply": "units", "allocation": "units"}
    return df.rename(columns={k: v for k, v in alias.items() if k in df.columns and v not in df.columns})


def read_table_file(f) -> pd.DataFrame:
    name = f.name.lower()
    if name.endswith(".csv"):
        return _norm(pd.read_csv(f))
    if name.endswith((".xlsx", ".xls")):
        return _norm(pd.read_excel(f))
    raise ValueError("Use a .csv or .xlsx file")


def read_results_pickle(data: bytes) -> dict:
    """Accepts a DataFrame, or a dict / tuple holding DataFrames (allocation + optional capacity / limits)."""
    obj = pickle.loads(data)  # only for files from your own pipeline
    out = {}
    if isinstance(obj, pd.DataFrame):
        out["allocation"] = _norm(obj)
    elif isinstance(obj, dict):
        for key in ("allocation", "detail", "allocation_plan", "results", "final_results"):
            if isinstance(obj.get(key), pd.DataFrame):
                out["allocation"] = _norm(obj[key]); break
        for key in ("capacity", "storage_capacity", "new_storage_cap"):
            if isinstance(obj.get(key), pd.DataFrame):
                out["capacity"] = _norm(obj[key]); break
        if isinstance(obj.get("limits"), pd.DataFrame):
            out["limits"] = _norm(obj["limits"])
        k = obj.get("kpis") or {}
        out["solver_status"] = k.get("status") or obj.get("status")
    elif isinstance(obj, (list, tuple)):
        dfs = [x for x in obj if isinstance(x, pd.DataFrame)]
        if dfs:
            out["allocation"] = _norm(dfs[0])
    if "allocation" not in out:
        raise ValueError("No allocation table found in the pickle (expected a DataFrame or a dict with "
                         "'allocation' / 'detail').")
    return out


def validate_output(out: dict) -> list[dict]:
    a = out.get("allocation")
    res = []
    if a is None or a.empty:
        return [{"level": "error", "text": "No allocation rows"}]
    miss = [c for c in REQUIRED if c not in a.columns]
    res.append({"level": "error" if miss else "ok",
                "text": "Required columns present" if not miss else f"Missing columns: {', '.join(miss)}"})
    if miss:
        return res
    units = pd.to_numeric(a["units"], errors="coerce")
    bad = int(units.isna().sum())
    res.append({"level": "error" if bad else "ok", "text": "Units are numbers" if not bad else f"{bad} non-numeric units"})
    neg = int((units < 0).sum())
    res.append({"level": "error" if neg else "ok", "text": "No negative units" if not neg else f"{neg} negative units"})
    nk = int(a[["warehouse", "product_type", "storage"]].isna().sum().sum())
    res.append({"level": "error" if nk else "ok",
                "text": "No missing warehouse / product / storage" if not nk else f"{nk} missing key values"})
    dup = int(a.duplicated(subset=["warehouse", "zone", "product_type"]).sum())
    res.append({"level": "warn" if dup else "ok",
                "text": "No duplicate warehouse × zone × product rows" if not dup else f"{dup} duplicate rows"})
    res.append({"level": "ok" if out.get("capacity") is not None else "warn",
                "text": "Capacity included" if out.get("capacity") is not None
                else "No capacity file — capacity_summary will show allocation only"})
    st_ = out.get("solver_status")
    if st_:
        res.append({"level": "ok" if st_ == "Optimal" else "warn", "text": f"Solver status: {st_}"})
    return res


def derive_tables(out: dict, meta: dict, published_by: str, published_at: str) -> dict:
    a = out["allocation"].copy()
    a["units"] = pd.to_numeric(a["units"], errors="coerce").fillna(0)
    for c in OPTIONAL:
        if c not in a.columns:
            a[c] = None
    if a["cost"].isna().all() and a["unit_cost"].notna().any():
        a["cost"] = pd.to_numeric(a["unit_cost"], errors="coerce") * a["units"]
    common = {"run_id": meta["run_id"], "target_month": meta["target_month"], "published_at": published_at}

    def frame(df, name):
        df = df.copy()
        for k, v in common.items():
            df[k] = v
        cols = [c for c, _ in BQ_TABLES[name]["schema"]]
        for c in cols:
            if c not in df.columns:
                df[c] = None
        return df[cols].reset_index(drop=True)

    alloc_ws = a.groupby(["warehouse", "storage"], as_index=False)["units"].sum().rename(columns={"units": "allocated"})
    if out.get("capacity") is not None:
        cap = out["capacity"][["warehouse", "storage", "capacity"]]
        cs = alloc_ws.merge(cap, on=["warehouse", "storage"], how="outer").fillna({"allocated": 0})
        cs["used_pct"] = (100 * cs["allocated"] / cs["capacity"]).round(1)
        cs["overflow"] = (cs["allocated"] - cs["capacity"]).clip(lower=0).round(1)
    else:
        cs = alloc_ws.assign(capacity=None, used_pct=None, overflow=None)
    tot = a["units"].sum()
    sh = a.groupby("warehouse", as_index=False)["units"].sum()
    sh["share_pct"] = (100 * sh["units"] / tot).round(1) if tot else 0
    if out.get("limits") is not None:
        sh = sh.merge(out["limits"][["warehouse", "min_pct", "max_pct"]], on="warehouse", how="left")
    overflow = float(pd.to_numeric(cs["overflow"], errors="coerce").fillna(0).sum())
    log = pd.DataFrame([{**common, "pipeline_env": meta["env"], "model_version": meta["model_version"],
                         "solver_status": out.get("solver_status") or "Unknown", "source": out.get("source"),
                         "total_units": round(float(tot), 2),
                         "total_cost": round(float(pd.to_numeric(a["cost"], errors="coerce").fillna(0).sum()), 2),
                         "overflow_units": overflow, "row_count": len(a), "notes": meta["notes"],
                         "published_by": published_by}])
    return {"allocation_plan": frame(a, "allocation_plan"), "capacity_summary": frame(cs, "capacity_summary"),
            "warehouse_share": frame(sh, "warehouse_share"), "run_log": frame(log, "run_log")}


# ======================================================================================
# BigQuery writers — demo (SQLite) and connected (google-cloud-bigquery)
# ======================================================================================
def bq_live() -> bool:
    try:
        return "gcp_service_account" in _secrets()
    except Exception:
        return False


def demo_publish(tables, dataset, names, month, mode) -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    rows = {}
    with sqlite3.connect(DEMO_DB) as con:
        for key, df in tables.items():
            t = f"{dataset}__{names[key]}"
            if mode == "replace" and con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone():
                con.execute(f'DELETE FROM "{t}" WHERE target_month = ?', (month,))
            df.to_sql(t, con, if_exists="append", index=False)
            rows[key] = len(df)
    return rows


def demo_inventory(dataset) -> pd.DataFrame:
    if not DEMO_DB.exists():
        return pd.DataFrame()
    out = []
    with sqlite3.connect(DEMO_DB) as con:
        for (n,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE ?",
                                (f"{dataset}__%",)).fetchall():
            c, m, r = con.execute(f'SELECT COUNT(*), COUNT(DISTINCT target_month), COUNT(DISTINCT run_id) '
                                  f'FROM "{n}"').fetchone()
            out.append({"Table": f"{dataset}.{n.split('__', 1)[1]}", "Rows": c, "Months": m, "Runs": r})
    return pd.DataFrame(out)


def demo_query(sql, project, dataset) -> pd.DataFrame:
    q = sql.strip().rstrip(";")
    if not re.match(r"(?is)^\s*(select|with)\b", q) or ";" in q:
        raise ValueError("Only a single SELECT query is allowed here.")
    q = q.replace("`", "")
    q = re.sub(rf"\b{re.escape(project)}\.{re.escape(dataset)}\.(\w+)", rf"{dataset}__\1", q)
    q = re.sub(rf"\b{re.escape(dataset)}\.(\w+)", rf"{dataset}__\1", q)
    con = sqlite3.connect(f"file:{DEMO_DB}?mode=ro", uri=True)
    try:
        return pd.read_sql_query(q, con)
    finally:
        con.close()


def bq_client(project, location):
    from google.cloud import bigquery
    from google.oauth2 import service_account
    info = dict(_secrets()["gcp_service_account"])
    creds = service_account.Credentials.from_service_account_info(info)
    return bigquery.Client(project=project or info.get("project_id"), credentials=creds, location=location)


def bq_publish(client, tables, project, dataset, names, month, mode) -> dict:
    from google.cloud import bigquery
    client.create_dataset(bigquery.Dataset(f"{project}.{dataset}"), exists_ok=True)
    rows = {}
    for key, df in tables.items():
        table_id = f"{project}.{dataset}.{names[key]}"
        schema = [bigquery.SchemaField(n, t) for n, t in BQ_TABLES[key]["schema"]]
        tbl = bigquery.Table(table_id, schema=schema)
        tbl.time_partitioning = bigquery.TimePartitioning(type_=bigquery.TimePartitioningType.MONTH,
                                                          field="target_month")
        tbl.description = BQ_TABLES[key]["desc"]
        client.create_table(tbl, exists_ok=True)
        if mode == "replace":
            client.query(f"DELETE FROM `{table_id}` WHERE target_month = @m",
                         job_config=bigquery.QueryJobConfig(query_parameters=[
                             bigquery.ScalarQueryParameter("m", "DATE", month)])).result()
        df = df.copy()
        df["target_month"] = pd.to_datetime(df["target_month"]).dt.date
        df["published_at"] = pd.to_datetime(df["published_at"])
        job = client.load_table_from_dataframe(df, table_id, job_config=bigquery.LoadJobConfig(
            schema=schema, write_disposition=bigquery.WriteDisposition.WRITE_APPEND))
        job.result()
        rows[key] = job.output_rows
    return rows


def read_pub_log() -> list:
    try:
        return json.loads(PUB_LOG.read_text())
    except Exception:
        return []


def add_pub_log(entry: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log = read_pub_log() + [entry]
    PUB_LOG.write_text(json.dumps(log, indent=1, default=str))


# ======================================================================================
# UI
# ======================================================================================
def month_options():
    t = date.today()
    return [date(t.year + (t.month - 1 + i) // 12, (t.month - 1 + i) % 12 + 1, 1).strftime("%B %Y")
            for i in range(-2, 4)]


def login_page():
    users, demo = load_users()
    _, mid, _ = st.columns([1, 1.1, 1])
    with mid:
        st.write(""); st.write("")
        st.markdown("## Allocation Output Publisher")
        st.caption("Send the allocation optimizer's output to BigQuery · planner sign-in")
        with st.form("login", border=True):
            u = st.text_input("Username", placeholder="e.g. yogesh")
            p = st.text_input("Password", type="password")
            ok = st.form_submit_button("Sign in", type="primary", width="stretch")
        if ok:
            user = check_login(users, u, p)
            if user:
                ss["user"] = user
                st.rerun()
            st.error("Username or password is incorrect.")
        if demo:
            with st.expander("Demo account"):
                st.markdown(f"Planner · username `yogesh` · password `{DEMO_PASSWORD}`")


ICON = {"ok": ":green[✓]", "warn": ":orange[!]", "error": ":red[✕]"}


def page_output():
    st.markdown("## 1 · Optimizer output")
    st.caption("Load the output the optimizer produced (`results_output.pkl` from the pipeline), set the run "
               "parameters, and review it before publishing.")

    st.markdown("#### Source")
    src = st.radio("Where is the output?", ["Demo output", "Upload file", "GCS path"], horizontal=True,
                   key="src", label_visibility="collapsed")
    out = None
    if src == "Demo output":
        out = demo_output()
        st.caption("A sample of the optimizer's output for 3 warehouses and 9 product types.")
    elif src == "Upload file":
        c1, c2 = st.columns(2)
        f = c1.file_uploader("Allocation output (results_output.pkl, .csv or .xlsx)", type=["pkl", "csv", "xlsx"])
        capf = c2.file_uploader("Capacity (optional .csv / .xlsx: warehouse, storage, capacity)", type=["csv", "xlsx"])
        st.caption("Only upload .pkl files produced by your own pipeline — pickle files can run code when opened. "
                   f"Columns needed: {', '.join(REQUIRED)} (optional: {', '.join(OPTIONAL)}).")
        if f:
            try:
                if f.name.lower().endswith(".pkl"):
                    out = read_results_pickle(f.getvalue())
                else:
                    out = {"allocation": read_table_file(f)}
                out["source"] = f"Upload: {f.name}"
                if capf:
                    out["capacity"] = read_table_file(capf)
            except Exception as e:
                st.error(f"Could not read the file: {type(e).__name__}: {e}")
    else:
        ss.setdefault("gcs_path", "gs://[allocation-bucket]/interim/results_output.pkl")
        st.text_input("GCS path", key="gcs_path")
        if not bq_live():
            st.info("Reading from GCS needs the service account in secrets (connected mode). "
                    "Use **Demo output** or **Upload file** for now.")
        else:
            if st.button("Load from GCS"):
                try:
                    from google.cloud import storage
                    from google.oauth2 import service_account
                    info = dict(_secrets()["gcp_service_account"])
                    cl = storage.Client(credentials=service_account.Credentials.from_service_account_info(info),
                                        project=info.get("project_id"))
                    m = re.match(r"gs://([^/]+)/(.+)", ss["gcs_path"])
                    data = cl.bucket(m.group(1)).blob(m.group(2)).download_as_bytes()
                    ss["gcs_out"] = {**read_results_pickle(data), "source": ss["gcs_path"]}
                except Exception as e:
                    st.error(f"{type(e).__name__}: {e}")
            out = ss.get("gcs_out")

    if out is None:
        return

    st.markdown("#### Run parameters")
    st.caption("These travel with the data to BigQuery (`run_id`, `target_month` on every row; the rest in `run_log`).")
    c1, c2, c3, c4 = st.columns(4)
    ss.setdefault("run_id", "RUN-" + datetime.now().strftime("%y%m%d-%H%M"))
    ss.setdefault("month", month_options()[2])
    ss.setdefault("model_version", "alloc_solver_v2")
    month = c1.selectbox("Target month", month_options(), key="month")
    run_id = c2.text_input("Run ID", key="run_id")
    env = c3.selectbox("Pipeline environment", ["prod", "dev"], key="env")
    mv = c4.text_input("Model version", key="model_version")
    notes = st.text_input("Notes (optional)", key="notes", placeholder="e.g. Re-run after capacity update for CFC 3")

    checks = validate_output(out)
    errors = any(c["level"] == "error" for c in checks)
    if not errors:
        a = out["allocation"]
        units = pd.to_numeric(a["units"], errors="coerce")
        cost = pd.to_numeric(a.get("cost"), errors="coerce") if "cost" in a else None
        k = st.columns(4)
        k[0].metric("Rows", f"{len(a):,}", border=True)
        k[1].metric("Units", f"{units.sum():,.0f}", border=True)
        k[2].metric("Shipping cost", f"${cost.sum():,.0f}" if cost is not None else "—", border=True)
        k[3].metric("Warehouses / products", f"{a['warehouse'].nunique()} / {a['product_type'].nunique()}", border=True)
        t1, t2, t3 = st.tabs(["Allocation", "By warehouse", "Capacity"])
        with t1:
            st.dataframe(a, hide_index=True, width="stretch")
        with t2:
            st.bar_chart(a.assign(units=units).pivot_table(index="warehouse", columns="storage", values="units",
                                                           aggfunc="sum"), horizontal=True, stack=False, height=220,
                         color=["#1C1C1A", "#C26A1B"][: a["storage"].nunique()] if a["storage"].nunique() <= 2 else None)
        with t3:
            if out.get("capacity") is not None:
                st.dataframe(out["capacity"], hide_index=True, width="stretch")
            else:
                st.caption("No capacity provided.")

    st.markdown("#### Checks")
    for c in checks:
        st.markdown(f"{ICON[c['level']]} &nbsp; {c['text']}")
    if st.button("Use this output → Publish to BigQuery", type="primary", disabled=errors):
        ss["output"] = out
        ss["meta"] = {"run_id": run_id.strip(), "month_label": month,
                      "target_month": datetime.strptime(month, "%B %Y").date().isoformat(),
                      "env": env, "model_version": mv, "notes": notes}
        ss["_goto"] = "2 · Publish to BigQuery"
        st.rerun()


def page_publish():
    live = bq_live()
    st.markdown("## 2 · Publish to BigQuery")
    if live:
        st.success("Connected mode — writing to real BigQuery.", icon=":material/cloud_done:")
    else:
        st.info("**Demo mode** — a local BigQuery simulator stands in for the dataset. Nothing leaves this app.",
                icon=":material/science:")
    out, meta = ss.get("output"), ss.get("meta")
    if not out:
        st.warning("No optimizer output selected yet.")
        if st.button("Go to 1 · Optimizer output", type="primary"):
            ss["_goto"] = "1 · Optimizer output"; st.rerun()
        return
    st.markdown(f"**Run** `{meta['run_id']}` · **{meta['month_label']}** · {meta['env']} · {out.get('source')}")

    cfg = dict(_secrets().get("bigquery", {})) if live else {}
    st.markdown("#### Destination")
    d1, d2, d3, d4 = st.columns([1.3, 1.1, 0.7, 1.4])
    project = d1.text_input("GCP project", value=cfg.get("project", "demo-allocation-project"))
    dataset = d2.text_input("Dataset", value=cfg.get("dataset", "com_allocation"))
    location = d3.text_input("Location", value=cfg.get("location", "US"))
    mode_l = d4.radio("Write mode", ["Replace this month's rows", "Append"],
                      help="Replace removes rows for the same target month first, so publishing again never duplicates.")
    mode = "replace" if mode_l.startswith("Replace") else "append"
    cfg_tbl = st.data_editor(pd.DataFrame([{"publish": True, "output": k, "table": k, "meaning": v["desc"]}
                                           for k, v in BQ_TABLES.items()]), hide_index=True, width="stretch",
                             disabled=["output", "meaning"], key="tblcfg",
                             column_config={"publish": st.column_config.CheckboxColumn("Publish", width="small"),
                                            "output": "Output", "table": "BigQuery table",
                                            "meaning": st.column_config.TextColumn("What it holds", width="large")})
    names = {r["output"]: r["table"] for _, r in cfg_tbl.iterrows() if r["publish"]}

    published_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tables = derive_tables(out, meta, ss["user"]["name"], published_at)
    st.markdown("#### Preview")
    if names:
        tabs = st.tabs([f"{names[k]} · {len(tables[k])} rows" for k in names])
        for tab, k in zip(tabs, names):
            with tab:
                st.caption(f"`{project}.{dataset}.{names[k]}` — partitioned by month on `target_month`")
                st.dataframe(tables[k].head(300), hide_index=True, width="stretch")
                with st.expander("Schema"):
                    st.dataframe(pd.DataFrame(BQ_TABLES[k]["schema"], columns=["column", "type"]),
                                 hide_index=True, width="stretch")

    st.markdown("#### Checks")
    ok_ids = bool(_ID_PROJECT.match(project or "")) and bool(_ID_NAME.match(dataset or "")) and \
        all(_ID_NAME.match(t or "") for t in names.values())
    checks = [
        {"level": "ok" if names else "error", "text": f"{len(names)} table(s) selected"},
        {"level": "ok" if ok_ids else "error",
         "text": "Project, dataset and table names are valid" if ok_ids else
         "Check names: project = lower-case letters/digits/hyphens (6–30); dataset/table = letters, digits, _"},
        {"level": "ok" if meta["run_id"] else "error", "text": "Run ID is set" if meta["run_id"] else "Run ID is empty"},
        {"level": "ok" if abs(tables["allocation_plan"]["units"].sum() - tables["run_log"]["total_units"][0]) < 1
         else "error", "text": f"Totals match ({tables['allocation_plan']['units'].sum():,.0f} units)"},
    ]
    prev = [p for p in read_pub_log() if p["target_month"] == meta["target_month"] and p["target"] == f"{project}.{dataset}"]
    if prev:
        checks.append({"level": "warn", "text": f"{meta['month_label']} was published before ({len(prev)}×) — "
                       + ("its rows will be replaced" if mode == "replace" else "rows will be added again")})
    for c in checks:
        st.markdown(f"{ICON[c['level']]} &nbsp; {c['text']}")
    blocked = any(c["level"] == "error" for c in checks)

    if st.button(f"Publish {len(names)} table(s) to BigQuery", type="primary", disabled=blocked):
        sub = {k: tables[k] for k in names}
        try:
            with st.spinner("Writing to BigQuery…"):
                rows = (bq_publish(bq_client(project, location), sub, project, dataset, names, meta["target_month"], mode)
                        if live else demo_publish(sub, dataset, names, meta["target_month"], mode))
            add_pub_log({"at": published_at, "by": ss["user"]["name"], "run_id": meta["run_id"],
                         "target_month": meta["target_month"], "target": f"{project}.{dataset}", "mode": mode,
                         "rows": rows, "backend": "bigquery" if live else "demo"})
            ss["last_pub"] = {"rows": rows, "target": f"{project}.{dataset}", "names": names}
            st.rerun()
        except ModuleNotFoundError:
            st.error("Install `google-cloud-bigquery` and `pyarrow` (requirements.txt) for connected mode.")
        except Exception as e:
            st.error(f"Publish failed: {type(e).__name__}: {e}")
    lp = ss.get("last_pub")
    if lp:
        st.success(f"Published to `{lp['target']}`: " +
                   ", ".join(f"**{lp['names'].get(k, k)}** {n:,} rows" for k, n in lp["rows"].items()))

    st.markdown("#### Published data")
    log = read_pub_log()
    if log:
        st.dataframe(pd.DataFrame([{"Published": p["at"], "By": p["by"], "Run": p["run_id"],
                                    "Month": p["target_month"], "Target": p["target"], "Mode": p["mode"],
                                    "Rows": sum(p["rows"].values())} for p in reversed(log)]),
                     hide_index=True, width="stretch")
    if not live:
        inv = demo_inventory(dataset)
        if not inv.empty:
            st.dataframe(inv, hide_index=True, width="stretch")
            plan = names.get("allocation_plan", "allocation_plan")
            sql = st.text_area("Query the published tables (BigQuery-style SQL, SELECT only)", height=140,
                               value=f"SELECT warehouse, storage, ROUND(SUM(units)) AS units, ROUND(SUM(cost)) AS cost\n"
                                     f"FROM `{project}.{dataset}.{plan}`\nWHERE target_month = '{meta['target_month']}'\n"
                                     f"GROUP BY warehouse, storage\nORDER BY warehouse, storage")
            if st.button("Run query"):
                try:
                    st.dataframe(demo_query(sql, project, dataset), hide_index=True, width="stretch")
                except Exception as e:
                    st.error(f"{type(e).__name__}: {e}")
    else:
        st.caption(f"Open the BigQuery console → `{project}.{dataset}` to see the tables.")

    with st.expander("Connecting to real BigQuery"):
        st.markdown("Add this to the app's **Secrets** and uncomment the two lines in `requirements.txt`:")
        st.code('''[gcp_service_account]   # service-account JSON key, field by field
type = "service_account"
project_id = "your-project"
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\\n...\\n-----END PRIVATE KEY-----\\n"
client_email = "allocation-app@your-project.iam.gserviceaccount.com"
token_uri = "https://oauth2.googleapis.com/token"

[bigquery]
project = "your-project"
dataset = "com_allocation"
location = "US"''', language="toml")


PAGES = {"1 · Optimizer output": page_output, "2 · Publish to BigQuery": page_publish}


def main():
    if "user" not in ss:
        login_page()
        return
    if "_goto" in ss:
        ss["page"] = ss.pop("_goto")
    if ss.get("page") not in PAGES:
        ss["page"] = "1 · Optimizer output"
    with st.sidebar:
        st.markdown("### Allocation Output Publisher")
        st.markdown(f"**{ss['user']['name']}**  \nPlanner")
        st.radio("Navigate", list(PAGES), key="page", label_visibility="collapsed")
        st.divider()
        if st.button("Log out", width="stretch"):
            for k in list(ss.keys()):
                del ss[k]
            st.rerun()
    PAGES[ss["page"]]()


main()
