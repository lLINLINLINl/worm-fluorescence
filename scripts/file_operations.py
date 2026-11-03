"""Bounded rename retries and long Windows filenames; no copying or deletion."""
import os
import time


def filesystem_path(path):
    """Use extended Windows syntax only at the filesystem API boundary."""
    raw = os.fspath(path)
    if os.name != 'nt' or len(raw) < 248:
        return path
    full = os.path.abspath(raw)
    separator = chr(92)
    prefix = separator * 2 + '?' + separator
    if full.startswith(prefix):
        return full
    if full.startswith(separator * 2):
        return prefix + 'UNC' + separator + full[2:]
    return prefix + full


def replace_with_retry(source, destination):
    for attempt in range(5):
        try:
            os.replace(filesystem_path(source), filesystem_path(destination))
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.2 * 2 ** attempt)
