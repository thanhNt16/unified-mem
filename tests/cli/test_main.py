import subprocess
import sys
from typer.testing import CliRunner
from kg.cli.main import app

runner = CliRunner()


def test_version_flag():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "kg" in result.stdout.lower()


def test_module_entrypoint_runs():
    # `python -m kg --version` must exit 0
    proc = subprocess.run(
        [sys.executable, "-m", "kg", "--version"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0
    assert "kg" in proc.stdout.lower()
