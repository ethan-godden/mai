"""Prompt templates.

Prompts live in Markdown files next to this module so they can be read,
reviewed and versioned as text rather than buried in code. Placeholders use
``$name`` (``string.Template``), which leaves the braces of any JSON in a
prompt alone.
"""

from __future__ import annotations

from functools import cache
from importlib import resources
from string import Template


@cache
def _template(name: str) -> Template:
    return Template(resources.files(__name__).joinpath(f"{name}.md").read_text().strip())


def render(name: str, **values: object) -> str:
    """Fill the template ``name``. Raises KeyError if a placeholder has no value."""
    return _template(name).substitute({key: str(value) for key, value in values.items()})
