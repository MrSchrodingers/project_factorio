from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/"scripts"/"capture_dashboard.sh"


def test_capture_script_uses_project_scoped_tmp_and_cleans_profile(tmp_path) -> None:
    fake=tmp_path/"fake-chromium"
    fake.write_text(
        """#!/usr/bin/env python3
from pathlib import Path
import sys

for arg in sys.argv[1:]:
    if arg.startswith("--screenshot="):
        Path(arg.split("=",1)[1]).write_bytes(b"fake-png")
"""
    )
    fake.chmod(0o755)
    out=tmp_path/"audit.png"
    env=os.environ.copy()
    env.update({
        "FACTORIO_AI_STATE_ROOT":str(tmp_path),
        "FACTORIO_AI_CHROMIUM_BIN":str(fake),
        "FACTORIO_AI_CAPTURE_HOME":str(tmp_path),
        "FACTORIO_AI_CAPTURE_TIMEOUT_SECONDS":"3",
    })

    completed=subprocess.run(
        [str(SCRIPT),str(out),"http://example.invalid/"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert out.read_bytes() == b"fake-png"
    assert str(out) in completed.stdout
    assert list((tmp_path/"runs").glob("chromium-capture.*")) == []


def test_dashboard_deploy_preflight_refuses_critically_low_tmp_space() -> None:
    env=os.environ.copy()
    env.update({
        "FACTORIO_AI_TMP_PATH":"/tmp",
        "FACTORIO_AI_MIN_TMP_FREE_BYTES":str(10**15),
    })
    completed=subprocess.run(
        [str(ROOT/"scripts"/"deploy_dashboard.sh"),"HEAD"],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 65
    assert "insufficient temp space" in completed.stderr
