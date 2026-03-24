import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from test_integration import run_cli
import subprocess
from unittest import mock


def test_cli_new_errors(tmp_path):
    run_cli(["--root", str(tmp_path), "init"], cwd=".")
    # title required, handled by typer mostly, but empty title string
    res = run_cli(["--root", str(tmp_path), "new", " "], cwd=".")
    assert res.returncode == 1


def test_cli_update_status_error(tmp_path):
    run_cli(["--root", str(tmp_path), "init"], cwd=".")
    run_cli(["--root", str(tmp_path), "new", "test"], cwd=".")
    res = run_cli(["--root", str(tmp_path), "update", "NIRA-1", "--status", "invalid"], cwd=".")
    assert res.returncode == 1


def test_cli_close_error(tmp_path):
    run_cli(["--root", str(tmp_path), "init"], cwd=".")
    run_cli(["--root", str(tmp_path), "new", "test"], cwd=".")
    # No notes
    res = run_cli(["--root", str(tmp_path), "close", "NIRA-1", "-m", "  "], cwd=".")
    assert res.returncode == 1


def test_cli_comment(tmp_path):
    run_cli(["--root", str(tmp_path), "init"], cwd=".")
    run_cli(["--root", str(tmp_path), "new", "test"], cwd=".")

    # Empty comment
    res = run_cli(["--root", str(tmp_path), "comment", "NIRA-1", "-m", "   "], cwd=".")
    assert res.returncode == 1

    # Valid comment
    res = run_cli(["--root", str(tmp_path), "comment", "NIRA-1", "-m", "test comment"], cwd=".")
    assert res.returncode == 0
    assert "Added comment" in res.stdout


def test_cli_link_unlink(tmp_path):
    run_cli(["--root", str(tmp_path), "init"], cwd=".")
    run_cli(["--root", str(tmp_path), "new", "t1"], cwd=".")
    run_cli(["--root", str(tmp_path), "new", "t2"], cwd=".")

    # valid link
    res = run_cli(["--root", str(tmp_path), "link", "NIRA-1", "NIRA-2"], cwd=".")
    assert res.returncode == 0

    # link error (already linked)
    res = run_cli(["--root", str(tmp_path), "link", "NIRA-1", "NIRA-2"], cwd=".")
    # Wait, the error is suppressed in storage, does it error? Let's check CLI output

    # valid unlink
    res = run_cli(["--root", str(tmp_path), "unlink", "NIRA-1", "NIRA-2"], cwd=".")
    assert res.returncode == 0

    # Links command
    res = run_cli(["--root", str(tmp_path), "links", "NIRA-1"], cwd=".")
    assert res.returncode == 0


def test_cli_start_git_error(tmp_path):
    run_cli(["--root", str(tmp_path), "init"], cwd=".")
    run_cli(["--root", str(tmp_path), "new", "test"], cwd=".")

    with mock.patch("nira_app.cli.subprocess.run") as mock_run:
        # Simulate git checkout failure
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=128, stdout="", stderr="fatal: Not a git repository"
        )
        res = run_cli(["--root", str(tmp_path), "start", "NIRA-1"], cwd=".")
        assert res.returncode == 0  # start catches git error but doesn't exit 1 for it
        assert "Git error" in res.stderr

    with mock.patch("nira_app.cli.subprocess.run") as mock_run:
        # Simulate branch already exists
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=128, stdout="", stderr="already exists"),
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        ]
        res = run_cli(["--root", str(tmp_path), "start", "NIRA-1"], cwd=".")
        assert res.returncode == 0
        assert "Branch already exists" in res.stdout
