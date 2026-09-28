"""Command-line alternative to the operator console.

    python send_requests.py data/feeder_snapshot.csv

Reads the feeder snapshot CSV, detects triggered feeders, and sends Iteration 1
requests to the enrolled customers on those feeders.
"""
import sys
import pandas as pd
from adr import core, workflow as wf

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "data/feeder_snapshot.csv"
    classified, log = wf.create_events(core.load_snapshot(path), core.load_customers())
    print(classified[["feeder", "s_kva", "s_rated_kva", "loading", "status", "target_kw"]].to_string(index=False))
    print()
    print(pd.DataFrame(log).to_string(index=False) if log else "No new requests sent.")
