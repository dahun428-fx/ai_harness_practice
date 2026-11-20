from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Literal

import yaml

_SPEC_FIELDS = {"id": str, "description": str, "variables": list, "system": str}


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class Prompt:
    system: str
    messages: tuple[Message, ...]


def build_prompt(system: str, user_input: str) -> Prompt:
    return Prompt(
        system=system,
        messages=(Message(role="user", content=user_input),),
    )


class TemplateError(ValueError):
    pass


def render_template(text: str, variables: dict[str, str]) -> str:
    template = Template(text)
    expected = set(template.get_identifiers())

    missing = expected - variables.keys()
    if missing:
        raise TemplateError(f"Missing variables: {', '.join(sorted(missing))}")
    unused = variables.keys() - expected
    if unused:
        raise TemplateError(f"Unused variables: {', '.join(sorted(unused))}")

    try:
        return template.substitute(variables)
    except ValueError as e:
        raise TemplateError(f"Template substitution error: {e}") from e


@dataclass(frozen=True)
class PromptSpec:
    id: str
    description: str
    variables: tuple[str, ...]
    system: str


def load_prompt_spec(path: str | Path) -> PromptSpec:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise TemplateError(f"{path}: YAML parsing error: {e}") from e
    if not isinstance(data, dict):
        raise TemplateError(f"{path}: expected a YAML mapping, got {type(data).__name__}")

    missing = _SPEC_FIELDS.keys() - data.keys()
    if missing:
        raise TemplateError(f"{path}: missing required keys: {', '.join(sorted(missing))}")
    if unknown := data.keys() - _SPEC_FIELDS.keys():
        raise TemplateError(f"{path}: unknown keys: {', '.join(sorted(map(str, unknown)))}")

    for key, kind in _SPEC_FIELDS.items():
        if not isinstance(data[key], kind):
            raise TemplateError(
                f"{path}: key '{key}' must be of type {kind.__name__}, got {type(data[key]).__name__}"
            )
    if not all(isinstance(v, str) for v in data["variables"]):
        raise TemplateError(f"{path}: all elements in 'variables' must be strings")

    system = data["system"].rstrip()
    declared = set(data["variables"])
    used = set(Template(system).get_identifiers())
    if declared != used:
        raise TemplateError(
            f"{path}: variables mismatch: undeclared {sorted(used - declared)}, unused {sorted(declared - used)}"
        )

    return PromptSpec(
        id=data["id"],
        description=data["description"],
        variables=tuple(data["variables"]),
        system=system,
    )
