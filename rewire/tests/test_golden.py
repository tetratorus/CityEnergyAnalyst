"""Golden tests through the spine runner (docker runtime). Skipped when docker or the rewire image is missing."""
import shutil
import subprocess
from pathlib import Path

import pytest

spine_golden = pytest.importorskip("spine.golden")
from spine.transform import load_descriptor

REWIRE = Path(__file__).resolve().parents[1]
IMAGE = "cea-rewire:local"


def _have_image() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, check=False).returncode == 0


@pytest.mark.skipif(not _have_image(), reason=f"needs docker image {IMAGE} (see rewire/README.md)")
@pytest.mark.parametrize("tid", ["cea.building_demand", "cea.solar_potential"])
def test_golden(tid):
    results = spine_golden.run_golden(load_descriptor(REWIRE / tid / "transform.yaml"))
    assert results and all(r.passed for r in results), [r.messages for r in results]
