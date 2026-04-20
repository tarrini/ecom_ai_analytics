import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

SERVER = os.getenv("TABLEAU_SERVER_URL", "").rstrip("/")
SITE = os.getenv("TABLEAU_SITE_NAME", "").strip()
PAT_NAME = os.getenv("TABLEAU_PAT_NAME", "").strip()
PAT_SECRET = os.getenv("TABLEAU_PAT_SECRET", "").strip()
DATASOURCE_ID = os.getenv("TABLEAU_DATASOURCE_ID", "").strip()
WORKBOOK_ID = os.getenv("TABLEAU_WORKBOOK_ID", "").strip()
API_VERSION = os.getenv("TABLEAU_API_VERSION", "3.22")


def sign_in():
    signin_url = f"{SERVER}/api/{API_VERSION}/auth/signin"
    payload = {
        "credentials": {
            "personalAccessTokenName": PAT_NAME,
            "personalAccessTokenSecret": PAT_SECRET,
            "site": {"contentUrl": SITE},
        }
    }
    response = requests.post(signin_url, json=payload, timeout=60)
    response.raise_for_status()
    data = response.json()
    token = data["credentials"]["token"]
    site_id = data["credentials"]["site"]["id"]
    return token, site_id


def sign_out(token):
    signout_url = f"{SERVER}/api/{API_VERSION}/auth/signout"
    requests.post(signout_url, headers={"X-Tableau-Auth": token}, timeout=60)


def trigger_datasource_refresh(token, site_id, datasource_id):
    refresh_url = f"{SERVER}/api/{API_VERSION}/sites/{site_id}/datasources/{datasource_id}/refresh"
    response = requests.post(refresh_url, headers={"X-Tableau-Auth": token}, timeout=60)
    response.raise_for_status()
    return response.json()["job"]["id"]


def trigger_workbook_refresh(token, site_id, workbook_id):
    refresh_url = f"{SERVER}/api/{API_VERSION}/sites/{site_id}/workbooks/{workbook_id}/refresh"
    response = requests.post(refresh_url, headers={"X-Tableau-Auth": token}, timeout=60)
    response.raise_for_status()
    return response.json()["job"]["id"]


def poll_job(token, site_id, job_id, max_wait_sec=1800, interval=15):
    job_url = f"{SERVER}/api/{API_VERSION}/sites/{site_id}/jobs/{job_id}"
    waited = 0
    while waited < max_wait_sec:
        response = requests.get(job_url, headers={"X-Tableau-Auth": token}, timeout=60)
        response.raise_for_status()
        job = response.json()["job"]
        finish_code = job.get("finishCode")
        progress = job.get("progress", "0")
        print(f"Job {job_id} progress={progress} finishCode={finish_code}")
        if finish_code is not None:
            return str(finish_code) == "0"
        time.sleep(interval)
        waited += interval
    raise TimeoutError(f"Timed out waiting for Tableau job {job_id}")


def main():
    if not SERVER or not PAT_NAME or not PAT_SECRET:
        raise ValueError("Missing TABLEAU_SERVER_URL / TABLEAU_PAT_NAME / TABLEAU_PAT_SECRET")
    if not DATASOURCE_ID and not WORKBOOK_ID:
        raise ValueError("Set TABLEAU_DATASOURCE_ID and/or TABLEAU_WORKBOOK_ID")

    token, site_id = sign_in()
    try:
        job_ids = []
        if DATASOURCE_ID:
            job_ids.append(trigger_datasource_refresh(token, site_id, DATASOURCE_ID))
        if WORKBOOK_ID:
            job_ids.append(trigger_workbook_refresh(token, site_id, WORKBOOK_ID))

        for job_id in job_ids:
            ok = poll_job(token, site_id, job_id)
            if not ok:
                raise RuntimeError(f"Tableau refresh failed for job_id={job_id}")

        print("Tableau refresh completed successfully.")
    finally:
        sign_out(token)


if __name__ == "__main__":
    main()