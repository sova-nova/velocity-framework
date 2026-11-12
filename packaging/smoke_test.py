"""A quick check of a packaged build (run by the build scripts and the release workflow):
the packaged command line works, and velocity's tool server starts from the packaged
binary and answers over stdio, as the own loop and Claude Code use it. In a throwaway
config and data folder.

    python packaging/smoke_test.py build/dist/velocity/velocity-cli.exe
"""

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main(binary: str) -> int:
    with tempfile.TemporaryDirectory(prefix="velocity-smoke-") as tmp:
        root = Path(tmp)
        env = {
            **os.environ,
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_DATA_HOME": str(root / "data"),
        }
        (root / "vault").mkdir()
        (root / "config/velocity").mkdir(parents=True)
        (root / "config/velocity/config.toml").write_text(
            f"vault_path = '{root / 'vault'}'\n", encoding="utf-8"
        )

        def run(*args: str) -> str:
            done = subprocess.run(
                [binary, *args],
                check=False,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=120,
            )
            if done.returncode != 0:
                raise SystemExit(f"`velocity {' '.join(args)}` failed:\n{done.stderr}")
            return done.stdout

        run("new", "Calculus")
        assert "calculus" in run("subjects"), "the new subject isn't listed"
        print("CLI: ok")

        from mcp import Client, StdioServerParameters

        async def tools() -> list[str]:
            server = StdioServerParameters(
                command=binary, args=["mcp", "--subject", "calculus"], env=env
            )
            async with Client(server) as mcp:
                return [t.name for t in (await mcp.list_tools()).tools]

        names = asyncio.run(tools())
        assert "record_question" in names, names
        print(f"Tool server: ok ({len(names)} tools)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
