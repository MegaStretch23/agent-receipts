"""agent-receipts: verify an AI agent's claims about a repo against git, the file system and your test command."""
from .extract import extract_claims, normalize_claims
from .checks import verify_claims
from .receipt import build_receipt, render_markdown

__all__ = ["extract_claims", "normalize_claims", "verify_claims", "build_receipt", "render_markdown"]
__version__ = "0.1.0"
