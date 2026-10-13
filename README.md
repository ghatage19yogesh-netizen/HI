# Allocation Output Publisher

Streamlit app for planners to send the allocation optimizer's output to BigQuery.
The optimizer itself is not in this app — it runs in the `scs-com-allocation` pipeline (`model_main.py`).

**Login:** planner only. Demo account: `yogesh` / `planner123` (turned off once real users are set in Secrets).

| Screen | What it does |
|---|---|
| 1 · Optimizer output | Load `results_output.pkl` (upload or GCS), a CSV/Excel, or demo output; set run parameters (month, run ID, environment, model version, notes); review and check the data |
| 2 · Publish to BigQuery | Choose project / dataset / tables and write mode, preview, check, publish, see publish history and query the tables |

Tables written (partitioned by month on `target_month`): `allocation_plan`, `capacity_summary`, `warehouse_share`, `run_log`.

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy on Streamlit Community Cloud
1. Push this repo to GitHub.
2. share.streamlit.io → **Create app** → pick the repo, branch `main`, main file `app.py`.
3. (Optional) **Advanced settings → Secrets**: add users and the BigQuery key (see `.streamlit/secrets.toml.example`).
4. Deploy and share the link.

## Users
```bash
python app.py adduser <username> "<Full Name>" <password>
```
Paste the printed block into Secrets.

## Real BigQuery (connected mode)
Without a key the app runs in **demo mode** (a local SQLite file stands in for BigQuery).
To write to real BigQuery: add `[gcp_service_account]` and `[bigquery]` to Secrets and uncomment the
Google lines in `requirements.txt`. The service account needs BigQuery Data Editor + Job User on the
dataset (and Storage Object Viewer to read `results_output.pkl` from GCS).

## Notes
- Only upload `.pkl` files produced by your own pipeline — pickle files can run code when opened.
- Publish history is stored in `data/` on the app server and is reset when Streamlit Cloud restarts the app.
