"""exoself: an unchosen, verifiable temperament for software agents."""

from .core import (  # noqa: F401
    SPEC_VERSION,
    agent_id,
    bind_beacon,
    born,
    develop,
    load_registry,
    new_state,
    phenotype,
    verify_certificate,
)
from .render import render_prompt, render_soul  # noqa: F401

__version__ = "0.1.0"
