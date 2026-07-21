from pathlib import Path
import time
import httpx

BASE = "http://127.0.0.1:8000/api/v1"
ROOT = Path(__file__).resolve().parents[1]


def main():
    with httpx.Client(timeout=180) as client:
        case = client.post(f"{BASE}/cases", json={
            "title": "AP 配置重载后 WLAN 服务异常",
            "device_type": "AP",
            "device_model": "Demo-AP-01",
            "firmware_version": "V1R1-demo",
            "issue_time": "2026-07-20 10:32:18",
            "description": "测试过程中修改无线配置后，SSID 消失且客户端断连。",
            "reproduction_steps": "修改信道并下发配置，等待服务重载。"
        }).raise_for_status().json()
        log_zip = ROOT / "sample_data" / "collectDebuginfo_demo.zip"
        with log_zip.open("rb") as handle:
            artifact = client.post(
                f"{BASE}/cases/{case['id']}/artifacts",
                files={"file": (log_zip.name, handle, "application/zip")},
                data={"kind": "debug_log"},
            ).raise_for_status().json()
        job = client.post(f"{BASE}/cases/{case['id']}/artifacts/{artifact['id']}/parse").raise_for_status().json()
        wait_job(client, job["id"])
        repo_zip = ROOT / "sample_data" / "demo_router_repo.zip"
        with repo_zip.open("rb") as handle:
            repo = client.post(f"{BASE}/cases/{case['id']}/repositories", files={"file": (repo_zip.name, handle, "application/zip")}).raise_for_status().json()
        job = client.post(f"{BASE}/repositories/{repo['repository_id']}/index").raise_for_status().json()
        wait_job(client, job["id"])
        job = client.post(f"{BASE}/cases/{case['id']}/analyses").raise_for_status().json()
        wait_job(client, job["id"])
        print(f"Demo created: http://localhost:5173/cases/{case['id']}")


def wait_job(client: httpx.Client, job_id: str):
    while True:
        job = client.get(f"{BASE}/jobs/{job_id}").raise_for_status().json()
        print(job["kind"], job["status"], job["progress"], job["message"])
        if job["status"] == "COMPLETED":
            return
        if job["status"] == "FAILED":
            raise RuntimeError(job.get("error_message"))
        time.sleep(1)


if __name__ == "__main__":
    main()
