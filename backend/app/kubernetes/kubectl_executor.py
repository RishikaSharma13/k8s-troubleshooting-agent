import json
import os
import subprocess
from dataclasses import dataclass
from typing import Any

from loguru import logger


@dataclass
class KubectlResult:
    command: list[str]
    success: bool
    stdout: str = ""
    stderr: str = ""
    error: str | None = None

    def json(self) -> Any:
        try:
            return json.loads(self.stdout)
        except json.JSONDecodeError:
            return None


class KubectlExecutor:
    """Runs kubectl without a shell and returns safe, structured results."""

    def __init__(self, kubeconfig_path: str = "", context: str | None = None) -> None:
        self.kubeconfig_path = kubeconfig_path
        self.context = context

    def run(self, *args: str, timeout: int = 30) -> KubectlResult:
        command = ["kubectl"]
        if self.context:
            command.extend(["--context", self.context])
        command.extend(args)
        env = os.environ.copy()
        if self.kubeconfig_path:
            env["KUBECONFIG"] = self.kubeconfig_path
        logger.info("Running kubectl command: {}", " ".join(command))
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=env, check=False)
        except FileNotFoundError:
            return KubectlResult(command, False, error="kubectl is not installed or is not on PATH")
        except subprocess.TimeoutExpired:
            return KubectlResult(command, False, error=f"kubectl command timed out after {timeout} seconds")
        if completed.returncode != 0:
            logger.warning("kubectl failed: {}", completed.stderr.strip())
            return KubectlResult(command, False, completed.stdout, completed.stderr, "kubectl returned a non-zero exit code")
        return KubectlResult(command, True, completed.stdout, completed.stderr)

    def contexts(self) -> KubectlResult:
        return self.run("config", "get-contexts", "-o", "name")

    def context_details(self) -> KubectlResult:
        return self.run("config", "view", "--raw", "-o", "json")
