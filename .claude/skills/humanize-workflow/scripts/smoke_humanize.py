#!/usr/bin/env python3
"""Exercise the real hmz CLI with a trusted zero-agent flow and no model call."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from safe_mapping import (
    HMZ_VERSION,
    MappingError,
    UPSTREAM_REPOSITORY,
    UPSTREAM_REVISION,
    validate_hmz_binary,
)

TASK = "safe deterministic smoke"
SKILLS_ROOT = Path(__file__).resolve().parents[2]
AUTHORING_SKILL = SKILLS_ROOT / "write-humanize-flow"
BUNDLED_DEFAULT = AUTHORING_SKILL / "assets/project/.humanize/flows/default.py"
BUNDLED_SEED_SLOT = AUTHORING_SKILL / "assets/project/.humanize/flows/slot.py"
FLOW_VALIDATOR = AUTHORING_SKILL / "scripts/validate_project_flows.py"
SLOT_ONE = '''"""Trusted deterministic zero-agent child v1."""
import json
from pathlib import Path

from hmz.flows import flow

@flow
def run(agents: tuple[()], task: str) -> None:
    if agents:
        raise RuntimeError("smoke received an agent")
    path = Path("smoke-result.json")
    result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"task": task, "terminal": "COMPLETE", "versions": []}
    result["versions"].append("v1")
    path.write_text(json.dumps(result), encoding="utf-8")

@flow(name="swap")
def swap(agents: tuple[()], task: str) -> None:
    if agents:
        raise RuntimeError("smoke received an agent")
    path = Path("smoke-result.json")
    result = json.loads(path.read_text(encoding="utf-8"))
    result["versions"].append("v1-start")
    Path(".humanize/flows/smoke-slot.py").write_bytes(
        Path(".humanize/flows/smoke-slot-v2.py").read_bytes()
    )
    result["versions"].append("v1-end")
    path.write_text(json.dumps(result), encoding="utf-8")
'''
SLOT_TWO = SLOT_ONE.replace("child v1", "child v2").replace('append("v1")', 'append("v2")').replace('append("v1-start")', 'append("v2-start")').replace('append("v1-end")', 'append("v2-end")')
FLOW = f'''"""Trusted deterministic zero-agent callable-flow smoke."""
from pathlib import Path

from hmz.flows import flow, load

SLOT_TWO = {SLOT_TWO!r}

@flow
def run(agents: tuple[()], task: str) -> None:
    if agents:
        raise RuntimeError("smoke received an agent")
    held = load("smoke-slot")
    held(agents, task)
    Path(".humanize/flows/smoke-slot.py").write_text(SLOT_TWO, encoding="utf-8")
    held(agents, task)
    load("smoke-slot")(agents, task)
    Path(".humanize/flows/smoke-slot.py").write_bytes(
        Path(".humanize/flows/smoke-slot-v1.py").read_bytes()
    )
    load("smoke-slot:swap")(agents, task)
    load("smoke-slot")(agents, task)
'''
BUNDLED_SLOT_ONE = '''"""Pinned-loader approved child v1; it opens no agent turn."""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

from hmz.flows import Agent, flow


class Agents(NamedTuple):
    executor: Agent
    reviewer: Agent


@flow
def run(agents: Agents, task: str) -> None:
    if len(agents) != 2:
        raise RuntimeError("bundled-default smoke requires two no-turn agents")
    path = Path("bundled-default-result.json")
    result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"task": task, "terminal": "COMPLETE", "versions": []}
    result["versions"].append("approved-v1-start")
    # Simulate a stale in-flight replacement; the launcher must keep using its sealed bytes.
    slot = Path(".humanize/flows/slot.py")
    slot.unlink()
    slot.write_bytes(Path("approved-slot-v2.py").read_bytes())
    result["versions"].append("approved-v1-end")
    path.write_text(json.dumps(result), encoding="utf-8")
'''
BUNDLED_SLOT_TWO = BUNDLED_SLOT_ONE.replace(
    "approved child v1", "approved child v2"
)
BUNDLED_SLOT_TWO = BUNDLED_SLOT_TWO.replace(
    '    result["versions"].append("approved-v1-start")\n'
    '    # Simulate a stale in-flight replacement; the launcher must keep using its sealed bytes.\n'
    '    slot = Path(".humanize/flows/slot.py")\n'
    '    slot.unlink()\n'
    '    slot.write_bytes(Path("approved-slot-v2.py").read_bytes())\n'
    '    result["versions"].append("approved-v1-end")\n',
    '    result["versions"].append("approved-v2")\n',
)
BUNDLED_PROBE = '''from __future__ import annotations
import fcntl
import hashlib
import json
import os
import re
import sys

source, packages, default, approved_slot, approved_digest = sys.argv[1:]
sys.path[:0] = [source, packages]

from hmz.coganchor.agents import AgentBase, AgentConfig
import hmz.flows as _flows
from hmz.runtime.runner import Runner


_original_find = _flows.find
_sealed = re.compile(r"/proc/self/fd/[0-9]+\\Z")


def _sealed_find(named):
    text = str(named)
    at, separator, inside = text.rpartition(":")
    candidate = at if separator and "/" not in inside else text
    if _sealed.fullmatch(candidate) and os.path.isfile(candidate):
        return candidate
    return _original_find(text)


_flows.find = _sealed_find


class NoTurnAgent(AgentBase):
    def __init__(self) -> None:
        super().__init__(AgentConfig(model="zero-turn", effort="none", permission="read-only"))
        self.turns = 0

    def new(self, cwd=None):
        self.turns += 1
        raise RuntimeError("bundled-default smoke must not open a model turn")


agents = [NoTurnAgent(), NoTurnAgent()]
slot_bytes = open(approved_slot, "rb").read()
if hashlib.sha256(slot_bytes).hexdigest() != approved_digest:
    raise RuntimeError("bundled-default smoke slot digest drifted before sealing")
slot_fd = os.memfd_create("bundled-default-smoke-slot", os.MFD_ALLOW_SEALING)
os.write(slot_fd, slot_bytes)
os.lseek(slot_fd, 0, os.SEEK_SET)
fcntl.fcntl(
    slot_fd,
    fcntl.F_ADD_SEALS,
    fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE,
)
os.environ["HUMANIZE_SLOT_SHA256"] = approved_digest
os.environ["HUMANIZE_SLOT_FD"] = str(slot_fd)
try:
    Runner(default, agents).run("bundled default pinned loader smoke")
finally:
    os.close(slot_fd)
turns = sum(agent.turns for agent in agents)
if turns:
    raise RuntimeError("bundled-default smoke unexpectedly opened an agent turn")
print(json.dumps({"turns": turns}, sort_keys=True))
'''
BUNDLED_RESULT = {
    "task": "bundled default pinned loader smoke",
    "terminal": "COMPLETE",
    "versions": ["approved-v1-start", "approved-v1-end", "approved-v2"],
}
SMOKE_CONTRACT = {
    "callableFlow": {
        "savedCallableAfterRewrite": "v2",
        "freshResolution": "v2",
        "inFlightBinding": ["v1-start", "v1-end"],
        "nextCallAfterInFlight": "v2",
    },
    "cycleJournal": "required-nonempty",
    "repository": UPSTREAM_REPOSITORY,
    "result": {
        "task": TASK,
        "terminal": "COMPLETE",
        "versions": ["v1", "v2", "v2", "v1-start", "v1-end", "v2"],
    },
    "revision": UPSTREAM_REVISION,
    "version": HMZ_VERSION,
    "launcher": "validated-real-python-isolated-no-site-no-bytecode-pinned-source",
    "bundledDefault": {
        "approvalSequence": [
            "approve-v1",
            "run-v1-while-slot-is-replaced",
            "approve-v2",
            "run-v2",
        ],
        "cycleCount": 2,
        "loader": "hmz.runner.Runner -> bundled default.py -> hmz.flows.load -> approved slot",
        "modelCalls": 0,
        "result": BUNDLED_RESULT,
        "turns": 0,
    },
}


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def expected_receipt() -> dict[str, object]:
    return {
        "schema": "humanize2-smoke-receipt/v3",
        "integration": True,
        "repository": SMOKE_CONTRACT["repository"],
        "revision": SMOKE_CONTRACT["revision"],
        "version": SMOKE_CONTRACT["version"],
        "status": "ok",
        "cycleCount": 1,
        "modelCalls": 0,
        "terminal": "COMPLETE",
        "result": SMOKE_CONTRACT["result"],
        "bundledDefault": SMOKE_CONTRACT["bundledDefault"],
        "commitments": {
            "flowSha256": digest(FLOW.encode("utf-8")),
            "slotOneSha256": digest(SLOT_ONE.encode("utf-8")),
            "slotTwoSha256": digest(SLOT_TWO.encode("utf-8")),
            "bundledDefaultSha256": digest(BUNDLED_DEFAULT.read_bytes()),
            "bundledSeedSlotSha256": digest(BUNDLED_SEED_SLOT.read_bytes()),
            "approvedSlotOneSha256": digest(BUNDLED_SLOT_ONE.encode("utf-8")),
            "approvedSlotTwoSha256": digest(BUNDLED_SLOT_TWO.encode("utf-8")),
            "bundledProbeSha256": digest(BUNDLED_PROBE.encode("utf-8")),
            "flowValidatorSha256": digest(FLOW_VALIDATOR.read_bytes()),
            "contractSha256": digest(canonical_bytes(SMOKE_CONTRACT)),
        },
    }


def verify_receipt(path: Path) -> dict[str, object]:
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MappingError(f"smoke receipt is unavailable or invalid: {error}") from error
    if not isinstance(receipt, dict):
        raise MappingError("smoke receipt root must be an object")
    expected = expected_receipt()
    commitments = receipt.get("commitments")
    if isinstance(commitments, dict) and commitments != expected["commitments"]:
        raise MappingError("smoke receipt commitments are stale")
    if receipt != expected:
        raise MappingError("smoke receipt does not match the pinned zero-agent contract")
    return receipt


def atomic_write(path: Path, payload: bytes) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def journal_paths(home: Path) -> list[Path]:
    """Find run journals across the current and legacy Humanize layouts."""
    paths = sorted((home / ".humanize/epics").glob("**/epic.jsonl"))
    return paths or sorted((home / ".humanize/cycles").glob("**/cycle.jsonl"))


def install_approved_slot(root: Path, candidate: Path) -> str:
    """Run the authoring validator/installer and return its exact approved digest."""
    completed = subprocess.run(
        [
            sys.executable,
            str(FLOW_VALIDATOR),
            "--root",
            str(root),
            "--trusted-seed",
            "--candidate",
            str(candidate),
            "--install-slot",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise MappingError(
            "bundled-default slot approval failed: " + completed.stderr.strip()
        )
    try:
        receipt = json.loads(completed.stdout)
        approved = receipt["slotSha256"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise MappingError("bundled-default slot approval returned malformed JSON") from error
    if not isinstance(approved, str) or len(approved) != 64:
        raise MappingError("bundled-default slot approval returned a malformed digest")
    return approved


def run_bundled_default_smoke(
    root: Path, home: Path, hmz: dict[str, object]
) -> dict[str, object]:
    """Execute the bundled root through pinned Runner/load with no backend turn."""
    flows = root / ".humanize/flows"
    flows.mkdir(parents=True)
    shutil.copyfile(BUNDLED_DEFAULT, flows / "default.py")
    seed_target = os.readlink(BUNDLED_SEED_SLOT)
    (flows / "versions").mkdir()
    shutil.copy2(
        BUNDLED_SEED_SLOT.parent / seed_target,
        flows / seed_target,
    )
    (flows / "slot.py").symlink_to(seed_target)
    first = root / "approved-slot-v1.py"
    second = root / "approved-slot-v2.py"
    first.write_text(BUNDLED_SLOT_ONE, encoding="utf-8")
    second.write_text(BUNDLED_SLOT_TWO, encoding="utf-8")
    digest_one = install_approved_slot(root, first)

    prefix = hmz.get("argvPrefix")
    if not isinstance(prefix, list) or not all(isinstance(item, str) for item in prefix):
        raise MappingError("validated hmz launcher returned a malformed argvPrefix")
    if len(prefix) < 8:
        raise MappingError("validated hmz launcher omitted pinned source/dependency paths")
    source, packages = prefix[-2:]
    def run_once(approved_slot: Path, approved_digest: str) -> dict[str, object]:
        probe = subprocess.run(
            [
                prefix[0],
                "-I",
                "-S",
                "-B",
                "-c",
                BUNDLED_PROBE,
                source,
                packages,
                str(flows / "default.py"),
                str(approved_slot),
                approved_digest,
            ],
            cwd=root,
            env={
                **os.environ,
                "HOME": str(home),
                "HUMANIZE_HOME": str(home / ".humanize"),
            },
            text=True,
            capture_output=True,
            check=False,
        )
        if probe.returncode != 0:
            raise MappingError(
                "bundled-default pinned Runner/load smoke failed: "
                + probe.stderr.strip()
            )
        try:
            parsed = json.loads(probe.stdout)
        except json.JSONDecodeError as error:
            raise MappingError("bundled-default probe returned malformed JSON") from error
        if not isinstance(parsed, dict):
            raise MappingError("bundled-default probe result must be an object")
        return parsed

    first_probe = run_once(first, digest_one)
    replacement = (flows / "slot.py").read_bytes()
    if digest(replacement) != digest(BUNDLED_SLOT_TWO.encode("utf-8")):
        raise MappingError(
            "bundled-default v1 did not survive the in-flight slot replacement"
        )
    slot = flows / "slot.py"
    slot.unlink()
    slot.symlink_to(seed_target)
    digest_two = install_approved_slot(root, second)
    second_probe = run_once(second, digest_two)
    try:
        result = json.loads(
            (root / "bundled-default-result.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise MappingError("bundled-default smoke produced malformed evidence") from error
    cycles = journal_paths(home)
    if (
        first_probe != {"turns": 0}
        or second_probe != {"turns": 0}
        or result != BUNDLED_RESULT
        or len(cycles) != 2
        or any(not cycle.is_file() or cycle.stat().st_size == 0 for cycle in cycles)
    ):
        raise MappingError(
            "bundled-default smoke evidence mismatch: "
            f"probes={[first_probe, second_probe]!r} result={result!r} cycles={len(cycles)}"
        )
    return {
        "approvalSequence": SMOKE_CONTRACT["bundledDefault"]["approvalSequence"],
        "cycleCount": len(cycles),
        "loader": SMOKE_CONTRACT["bundledDefault"]["loader"],
        "modelCalls": 0,
        "result": result,
        "turns": first_probe["turns"] + second_probe["turns"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hmz", type=Path)
    parser.add_argument(
        "--integration",
        action="store_true",
        help="fail-closed pinned integration mode; requires HUMANIZE2_SMOKE_HMZ",
    )
    parser.add_argument("--receipt-out", type=Path)
    parser.add_argument("--verify-receipt", type=Path)
    args = parser.parse_args()
    try:
        if args.verify_receipt is not None:
            if args.integration or args.hmz is not None or args.receipt_out is not None:
                raise MappingError("--verify-receipt is an exclusive read-only mode")
            receipt = verify_receipt(args.verify_receipt)
            print(canonical_bytes(receipt).decode("utf-8"), end="")
            return 0
        if args.receipt_out is not None and not args.integration:
            raise MappingError("--receipt-out requires --integration")
        if args.integration:
            if args.hmz is not None:
                raise MappingError(
                    "--integration takes HUMANIZE2_SMOKE_HMZ, not --hmz"
                )
            configured = os.environ.get("HUMANIZE2_SMOKE_HMZ")
            if not configured:
                raise MappingError(
                    "--integration requires HUMANIZE2_SMOKE_HMZ"
                )
            requested = Path(configured)
        else:
            if args.hmz is None:
                raise MappingError("--hmz is required outside --integration")
            requested = args.hmz
        hmz = validate_hmz_binary(requested)
    except (OSError, ValueError) as error:
        print(f"humanize-smoke: {error}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="humanize2-safe-smoke-") as directory:
        root = Path(directory)
        cli_home = root / "cli-home"
        cli_home.mkdir()
        flow = root / "smoke.py"
        flow.write_text(FLOW, encoding="utf-8")
        slots = root / ".humanize/flows"
        slots.mkdir(parents=True)
        (slots / "smoke-slot.py").write_text(SLOT_ONE, encoding="utf-8")
        (slots / "smoke-slot-v1.py").write_text(SLOT_ONE, encoding="utf-8")
        (slots / "smoke-slot-v2.py").write_text(SLOT_TWO, encoding="utf-8")
        environment = dict(os.environ)
        environment["HOME"] = str(cli_home)
        environment["HUMANIZE_HOME"] = str(cli_home / ".humanize")
        completed = subprocess.run([*hmz["argvPrefix"], "exec", "-f", str(flow), TASK], cwd=root, env=environment, text=True, capture_output=True, check=False)
        result_path = root / "smoke-result.json"
        # Humanize 0.1.0 records runs as epics; retain the legacy glob for older audited installs.
        cycles = journal_paths(cli_home)
        if (
            completed.returncode != 0
            or not result_path.is_file()
            or len(cycles) != 1
            or any(not cycle.is_file() or cycle.stat().st_size == 0 for cycle in cycles)
        ):
            print(
                "humanize-smoke: zero-agent execution failed: "
                f"rc={completed.returncode} stderr={completed.stderr.strip()}",
                file=sys.stderr,
            )
            return 2
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            print(f"humanize-smoke: malformed result: {error}", file=sys.stderr)
            return 2
        expected_result = SMOKE_CONTRACT["result"]
        if result != expected_result:
            print(
                f"humanize-smoke: unexpected zero-agent result: {result!r}",
                file=sys.stderr,
            )
            return 2
        bundled_root = root / "bundled-default-project"
        bundled_root.mkdir()
        bundled_home = root / "bundled-default-home"
        bundled_home.mkdir()
        try:
            bundled = run_bundled_default_smoke(bundled_root, bundled_home, hmz)
        except (OSError, MappingError) as error:
            print(f"humanize-smoke: {error}", file=sys.stderr)
            return 2
        if bundled != SMOKE_CONTRACT["bundledDefault"]:
            print(
                f"humanize-smoke: unexpected bundled-default result: {bundled!r}",
                file=sys.stderr,
            )
            return 2
        receipt = expected_receipt()
        if args.receipt_out is not None:
            atomic_write(args.receipt_out, canonical_bytes(receipt))
        print(canonical_bytes(receipt).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
