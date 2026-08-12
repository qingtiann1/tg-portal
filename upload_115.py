#!/usr/bin/env python3
"""Upload files to 115 cloud via alist API"""
import json, os, sys, urllib.parse, urllib.request

ALIST_URL = "http://localhost:5244"
ALIST_USER = "admin"
ALIST_PASS = "New115_Upload"
TARGET_DIR = "/115/beifen"  # Default upload folder on 115


def get_token():
    """Get alist admin token"""
    data = json.dumps({"username": ALIST_USER, "password": ALIST_PASS}).encode()
    req = urllib.request.Request(
        f"{ALIST_URL}/api/auth/login",
        data=data,
        headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=10)
    return json.loads(resp.read())["data"]["token"]


def upload_file(filepath, target_dir=None):
    """Upload a file to 115 via alist"""
    if target_dir is None:
        target_dir = TARGET_DIR

    if not os.path.exists(filepath):
        print(f"ERROR: file not found: {filepath}")
        return False

    filename = os.path.basename(filepath)
    filesize = os.path.getsize(filepath)
    remote_path = f"{target_dir}/{filename}"

    print(f"Uploading: {filename} ({filesize / 1024 / 1024:.1f} MB) -> 115{target_dir}")

    try:
        token = get_token()
    except Exception as e:
        print(f"ERROR: alist login failed: {e}")
        return False

    try:
        with open(filepath, "rb") as f:
            data = f.read()

        # URL-encode File-Path to handle Chinese filenames
        encoded_path = urllib.parse.quote(remote_path, safe="/")

        req = urllib.request.Request(
            f"{ALIST_URL}/api/fs/put",
            data=data,
            headers={
                "Authorization": token,
                "File-Path": encoded_path,
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(data)),
            },
            method="PUT",
        )
        resp = urllib.request.urlopen(req, timeout=600)
        result = json.loads(resp.read())

        if result.get("code") == 200:
            print(f"  OK: {remote_path}")
            return True
        else:
            print(f"  FAIL: {result}")
            return False
    except Exception as e:
        print(f"  ERROR: {e}")
        return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 upload_115.py <filepath> [target_dir]")
        sys.exit(1)

    filepath = sys.argv[1]
    target = sys.argv[2] if len(sys.argv) > 2 else None
    upload_file(filepath, target)
