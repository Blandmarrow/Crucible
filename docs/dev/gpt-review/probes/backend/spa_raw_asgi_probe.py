import asyncio, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[5]))
spec = sys.argv[1] if len(sys.argv) > 1 else None

async def main():
    from backend.main import app
    status, chunks = {}, []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(m):
        if m["type"] == "http.response.start":
            status["code"] = m["status"]
        elif m["type"] == "http.response.body":
            chunks.append(m.get("body", b""))
    asgi = {"version": "3.0"}
    if spec:
        asgi["spec_version"] = spec
    await app({"type": "http", "asgi": asgi, "http_version": "1.1", "method": "GET",
               "scheme": "http", "path": "//etc/passwd", "raw_path": b"//etc/passwd",
               "query_string": b"", "root_path": "", "headers": [(b"host", b"test")],
               "client": ("1.2.3.4", 9999), "server": ("test", 80)}, receive, send)
    body = b"".join(chunks)
    print("spec_version=", spec, "status=", status.get("code"), "bytes=", len(body),
          "is_index=", body.startswith(b"<!") or b"<html" in body[:200].lower())

asyncio.run(main())
