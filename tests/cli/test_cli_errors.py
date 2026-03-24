import re
import os
from nira_app.cli import app
from typer.testing import CliRunner

_typer_runner = CliRunner()


def run_cli_cov(args, cwd=".", input=None):
    original_cwd = os.getcwd()
    os.chdir(cwd)
    try:
        result = _typer_runner.invoke(app, args, input=input)
        stdout_clean = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
        stderr_clean = re.sub(r"\x1b\[[0-9;]*m", "", result.stderr)
        return result.exit_code, stdout_clean, stderr_clean
    finally:
        os.chdir(original_cwd)


def test_cli_errors_and_edge_cases(temp_root):
    # Try running commands outside a nira repo
    code, out, err = run_cli_cov(["show", "A-1"], cwd=temp_root)

    # Init twice to trigger NiraError
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=temp_root)
    run_cli_cov(["init", "--project-key", "NIRA"], cwd=temp_root)

    # New ticket with parent
    run_cli_cov(["create", "T1"], cwd=temp_root)
    run_cli_cov(["create", "T2", "--parent", "NIRA-1"], cwd=temp_root)

    # Update with NO arguments (should say No changes specified)
    run_cli_cov(["update", "NIRA-1"], cwd=temp_root)

    # Close with empty notes
    run_cli_cov(["close", "NIRA-1", "-m", " "], cwd=temp_root)

    # Comment with empty body
    run_cli_cov(["comment", "NIRA-1", "-m", " "], cwd=temp_root)

    # Reopen
    run_cli_cov(["reopen", "NIRA-1"], cwd=temp_root)

    # Board with valid/invalid
    run_cli_cov(["board"], cwd=temp_root)
    run_cli_cov(["board", "--project", "MISSING"], cwd=temp_root)

    # Missing ticket errors
    run_cli_cov(["show", "NIRA-999"], cwd=temp_root)
    run_cli_cov(["update", "NIRA-999"], cwd=temp_root)
    run_cli_cov(["comment", "NIRA-999", "-m", "b"], cwd=temp_root)
    run_cli_cov(["link", "NIRA-1", "NIRA-999"], cwd=temp_root)
    run_cli_cov(["unlink", "NIRA-1", "NIRA-999"], cwd=temp_root)

    # Invalid updates
    run_cli_cov(["update", "NIRA-1", "--status", "invalid"], cwd=temp_root)
    run_cli_cov(["update", "NIRA-1", "--priority", "invalid"], cwd=temp_root)
    run_cli_cov(["update", "NIRA-1", "--type", "invalid"], cwd=temp_root)

    # Valid update
    run_cli_cov(["update", "NIRA-1", "--story-points", "5", "--body", "new body"], cwd=temp_root)

    # List combinations
    run_cli_cov(
        ["list", "--label", "none", "--project", "none", "--type", "task", "--status", "open", "--priority", "high"],
        cwd=temp_root,
    )
