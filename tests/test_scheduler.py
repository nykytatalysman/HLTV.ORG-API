from __future__ import annotations

from hltv_service.scheduler import run_cycle


class FakeWorker:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def run(self, command: str) -> dict[str, str]:
        self.commands.append(command)
        return {"status": "success", "command": command}


def test_scheduler_cycle_runs_exactly_one_refresh() -> None:
    worker = FakeWorker()
    result = run_cycle(lambda: worker)  # type: ignore[arg-type]
    assert result == {"status": "success", "command": "refresh"}
    assert worker.commands == ["refresh"]
