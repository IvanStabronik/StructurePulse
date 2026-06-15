import argparse
import json
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test StructurePulse services")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--worker-url", default="http://localhost:8001")
    args = parser.parse_args()

    checks = (
        ("api_live", f"{args.api_url}/health/live", 200),
        ("api_ready", f"{args.api_url}/health/ready", 200),
        ("worker_live", f"{args.worker_url}/health/live", 200),
        ("worker_ready", f"{args.worker_url}/health/ready", 200),
    )
    failed = False
    for name, url, expected_status in checks:
        try:
            with urlopen(url, timeout=5) as response:
                status = response.status
                body = response.read().decode()
        except HTTPError as exc:
            status = exc.code
            body = exc.read().decode()
        except URLError as exc:
            status = 0
            body = str(exc.reason)
        ok = status == expected_status
        failed = failed or not ok
        print(
            json.dumps(
                {"check": name, "ok": ok, "status": status, "body": body},
                ensure_ascii=True,
            )
        )
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
