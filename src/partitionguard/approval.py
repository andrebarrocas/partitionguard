"""SSH-signed approval receipts for a separately controlled execution host."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from .models import canonical_json, digest

NAMESPACE = "partitionguard-approval-v1"


class ApprovalError(ValueError):
    """An approval could not be authenticated."""


def _read(path: Path, limit: int, trusted=False) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ApprovalError("Approval inputs must be regular files")
            if trusted and (info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022):
                raise ApprovalError(
                    "Trust file must be owned by root or the runner and not group/world writable"
                )
            contents = stream.read(limit + 1)
    except OSError:
        raise ApprovalError("Cannot read approval input") from None
    if not contents or len(contents) > limit:
        raise ApprovalError("Approval input is empty or exceeds its size limit")
    return contents


class ApprovalPolicy:
    def __init__(self, trust_file: Path):
        self.signers = _read(trust_file, 65536, trusted=True)
        self.identity = sha256(self.signers).hexdigest()

    @classmethod
    def from_environment(cls):
        path = os.environ.get("PARTITIONGUARD_TRUST_FILE")
        return cls(Path(path)) if path else None

    def verify(self, receipt, principal, signature_path):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@+-]{0,127}", principal):
            raise ApprovalError("Invalid reviewer principal")
        if signature_path is None:
            raise ApprovalError("Controlled approval requires a reviewer signature")
        signature = _read(Path(signature_path), 16384)
        message = canonical_json(receipt).encode() + b"\n"
        with TemporaryDirectory() as directory:
            allowed = Path(directory) / "allowed_signers"
            signed = Path(directory) / "receipt.sig"
            allowed.write_bytes(self.signers)
            signed.write_bytes(signature)
            try:
                result = subprocess.run(
                    [
                        "/usr/bin/ssh-keygen",
                        "-Y",
                        "verify",
                        "-f",
                        str(allowed),
                        "-I",
                        principal,
                        "-n",
                        NAMESPACE,
                        "-s",
                        str(signed),
                    ],
                    input=message,
                    env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
            except (OSError, subprocess.TimeoutExpired):
                raise ApprovalError("OpenSSH approval verification is unavailable") from None
        if result.returncode:
            raise ApprovalError("Reviewer signature does not authorize this exact prepared scope")
        return {
            "principal": principal,
            "receipt_sha256": sha256(message).hexdigest(),
            "signature_sha256": sha256(signature).hexdigest(),
            "trust_sha256": self.identity,
        }


def receipt_for(run, principal, continuation=False):
    valid = {"CANARY_PAUSED"} if continuation else {"PREPARED", "APPROVED"}
    if run["status"] not in valid or not run["approval_policy"]:
        raise ApprovalError("No reviewable controlled approval at this stage")
    return {
        "namespace": NAMESPACE,
        "principal": principal,
        "kind": "continuation" if continuation else "initial",
        "run_id": run["run_id"],
        "plan_digest": run["migration_id"],
        "target_identity": run["target_identity"],
        "baseline_digest": digest(run["baseline"]),
        "adapter": run["plan"]["adapter"],
        "table": run["plan"]["current"]["table"],
        "scope": run["plan"]["migration"]["bounds"],
        "canary_review": run["control"]["canary_review_hash"] if continuation else None,
        "trust_sha256": run["approval_policy"],
    }
