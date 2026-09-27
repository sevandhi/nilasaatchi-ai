"""Daily AWS spend guard (D-028): month-to-date and project-to-date cost vs the self-imposed cap.

Exit codes: 0 = under 80% of cap, 2 = at/over 80% (stop and ask the user), 1 = could not read costs.
"""
import datetime as dt
import os
import sys

import boto3
from dotenv import load_dotenv

load_dotenv()
CAP = float(os.environ.get("AWS_SPEND_CAP_USD", "15"))
PROJECT_START = os.environ.get("AWS_PROJECT_START", "2026-09-01")


def main() -> int:
    try:
        ce = boto3.Session(profile_name=os.environ.get("AWS_COST_PROFILE", "fai-cost")).client(
            "ce", region_name="us-east-1"
        )
        end = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        r = ce.get_cost_and_usage(
            TimePeriod={"Start": PROJECT_START, "End": end},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
        )
    except Exception as e:  # expired SSO session is the usual cause
        print(f"could not read costs: {type(e).__name__}: {e}\n"
              "hint: aws sso login --sso-session fai-tce --use-device-code")
        return 1
    by_service: dict[str, float] = {}
    for period in r["ResultsByTime"]:
        for g in period["Groups"]:
            by_service[g["Keys"][0]] = by_service.get(g["Keys"][0], 0.0) + float(
                g["Metrics"]["UnblendedCost"]["Amount"]
            )
    total = sum(by_service.values())
    for svc, amt in sorted(by_service.items(), key=lambda x: -x[1]):
        if amt >= 0.001:
            print(f"  {svc:45s} ${amt:8.3f}")
    pct = 100 * total / CAP
    print(f"TOTAL since {PROJECT_START}: ${total:.3f} of ${CAP:.2f} cap ({pct:.1f}%)")
    if pct >= 80:
        print("STOP: at/over 80% of the AWS cap — ask the user before any further AWS usage.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
