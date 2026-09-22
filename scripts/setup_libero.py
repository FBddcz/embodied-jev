"""Download pinned LIBERO source/assets with resumable, verified individual files."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen

REVISION = "8f1084e3132a39270c3a13ebe37270a43ece2a01"
BASE = "https://raw.githubusercontent.com/Lifelong-Robot-Learning/LIBERO/" + REVISION + "/"


def blob_hash(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def setup(root, tree_file=None, workers=8):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if tree_file:
        tree = json.loads(Path(tree_file).read_text())
    else:
        request = Request("https://api.github.com/repos/Lifelong-Robot-Learning/LIBERO/git/trees/" + REVISION + "?recursive=1",
                          headers={"User-Agent": "EmbodiedJev-setup"})
        with urlopen(request, timeout=60) as response:
            tree = json.load(response)
    if tree.get("truncated"):
        raise ValueError("Incomplete upstream source tree")
    files = [item for item in tree["tree"] if item["type"] == "blob" and
             (item["path"].startswith("libero/") or item["path"] in {"LICENSE", "README.md"})]
    def fetch(item):
        path = root / item["path"]
        path.resolve().relative_to(root)
        if path.is_file() and blob_hash(path.read_bytes()) == item["sha"]:
            return item
        last = None
        for attempt in range(4):
            try:
                request = Request(BASE + item["path"], headers={"User-Agent": "EmbodiedJev-setup"})
                with urlopen(request, timeout=90) as response:
                    data = response.read()
                if len(data) != item["size"] or blob_hash(data) != item["sha"]:
                    raise ValueError("Source file hash mismatch: " + item["path"])
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(path.suffix + ".download")
                temporary.write_bytes(data)
                temporary.replace(path)
                return item
            except Exception as exc:
                last = exc
                time.sleep(min(2 ** attempt, 8))
        raise RuntimeError(item["path"] + ": " + type(last).__name__) from last
    complete = []
    failures = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch, item): item for item in files}
        for future in as_completed(futures):
            try:
                complete.append(future.result())
            except Exception as exc:
                failures.append(str(exc))
            if (len(complete) + len(failures)) % 50 == 0:
                print(f"{len(complete)}/{len(files)} verified files; {len(failures)} failed", flush=True)
    if failures:
        raise RuntimeError("Re-run to resume these files: " + "; ".join(failures[:10]))
    manifest = {"revision": REVISION, "source": BASE, "files": complete}
    (root / ".libero-source.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"LIBERO {REVISION}: {len(complete)} verified source files → {root}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--tree-file", help="Previously fetched official GitHub recursive tree")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    setup(args.root, args.tree_file, args.workers)
