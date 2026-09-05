"""Load maintained workflow instructions without caching or database access."""
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel


WorkflowName = Literal["adapt-reel", "performance-audit"]
WORKFLOW_FILES = {
    "adapt-reel": "adapt-reel.md",
    "performance-audit": "performance-audit.md",
}
WORKFLOW_DIRECTORY = Path(__file__).resolve().parent / "workflows"


class WorkflowOutput(BaseModel):
    name: WorkflowName
    version: str
    instructions: str


def load_workflow(name: str) -> WorkflowOutput:
    """Read only an allowlisted guide; its digest identifies the exact revision."""
    filename = WORKFLOW_FILES.get(name)
    if filename is None:
        raise ValueError(
            f"Unknown workflow {name!r}. Available workflows: {', '.join(WORKFLOW_FILES)}."
        )
    try:
        instructions = (WORKFLOW_DIRECTORY / filename).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"Workflow {name!r} is unavailable. Check the deployed workflow files.") from exc
    if not instructions.strip():
        raise RuntimeError(f"Workflow {name!r} is empty. Check the deployed workflow files.")
    return WorkflowOutput(
        name=name,
        version=sha256(instructions.encode("utf-8")).hexdigest(),
        instructions=instructions,
    )
