"""Local gallery server with byte ranges, matching GitHub Pages video seeking."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
import re


class Handler(SimpleHTTPRequestHandler):
    def send_head(self):
        self.remaining = None
        path = self.translate_path(self.path)
        value = self.headers.get("Range")
        if not value or not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", value)
        if not match or not any(match.groups()):
            self.send_error(416)
            return None
        left, right = match.groups()
        start = int(left) if left else max(0, size-int(right))
        end = min(int(right), size-1) if left and right else size-1
        if start >= size or end < start:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        stream = open(path, "rb")
        stream.seek(start)
        self.remaining = end-start+1
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(self.remaining))
        self.end_headers()
        return stream

    def copyfile(self, source, output):
        try:
            if self.remaining is None:
                return super().copyfile(source, output)
            while self.remaining > 0:
                data = source.read(min(self.remaining, 65536))
                if not data:
                    break
                output.write(data)
                self.remaining -= len(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Browsers cancel ranges when switching recordings.


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument("--directory", default="dist/site")
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, directory=args.directory)).serve_forever()
