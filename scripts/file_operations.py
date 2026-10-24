"""Bounded retries for transient Windows rename locks; no copying or deletion."""
import os
import time


def replace_with_retry(source, destination):
    for attempt in range(5):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.2 * 2 ** attempt)
