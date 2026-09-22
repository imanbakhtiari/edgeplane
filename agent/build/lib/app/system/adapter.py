import asyncio
from dataclasses import dataclass


@dataclass
class Result:
    code: int = 0
    stdout: str = ""
    stderr: str = ""

    def dict(self):
        return {"code": self.code, "stdout": self.stdout[-8192:], "stderr": self.stderr[-8192:]}


class HostSystemAdapter:
    async def run(self, *args):
        process = await asyncio.create_subprocess_exec(
            *map(str, args), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout=60)
        except TimeoutError:
            process.kill()
            await process.communicate()
            return Result(124, stderr="Management command timed out")
        return Result(process.returncode, out.decode(errors="replace"), err.decode(errors="replace"))


class SandboxSystemAdapter:
    """Explicit simulation; never claims that real service validation occurred."""

    def __init__(self):
        self.calls = []

    async def run(self, *args):
        self.calls.append(tuple(map(str, args)))
        return Result(stdout="SANDBOX: simulated success")
