# CONFIG-CONTROLLED — edit the master under jswarm/host/deploy/ (COM-176).
"""COM-176 shared layer loader for the controlled-config one-home model.

Parses ``docs/_CONTROLLED_CONFIG/dotclaude/_layers.yaml`` once and shares the
result across deploy, verify, adopt, and migration. Each *context* maps to
exactly one physical deploy target:

    docs/_CONTROLLED_CONFIG/dotclaude/<context>/<target_relpath>
      -> deployed only to <context>.deploy_target/<target_relpath>

This module is a registry of physical contexts and target roots. It is NOT a
precedence registry: runtime precedence (enterprise > user > repo) is Claude
Code's own cascade, never a deploy-engine concern. Any precedence / rank /
masking / multi-target field in ``_layers.yaml`` is rejected on purpose to keep
the rejected multi-target model from reappearing.

Spec: COM-176.implementation-spec.md §2 (_layers.yaml) and §4 (this loader).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

try:  # ruamel preserves formatting elsewhere; for read we just need a parser.
    from ruamel.yaml import YAML

    def _yaml_load(text: str) -> Any:
        return YAML(typ="safe").load(text)
except Exception:  # pragma: no cover - ruamel is a hard dependency in this repo
    import yaml as _pyyaml

    def _yaml_load(text: str) -> Any:
        return _pyyaml.safe_load(text)


SCHEMA = "com176.layers.v1-one-home"

# A controlled-config context id. `repo.*` / `team.* `/ `machine.*` allow future
# layers without code changes; only the records present in _layers.yaml are real.
CONTEXT_RE = re.compile(
    r"^(enterprise|user|repo\.[A-Za-z0-9_.-]+|team\.[A-Za-z0-9_.-]+|machine\.[A-Za-z0-9_.-]+)$"
)

# Deploy CLI root-argument names a `root_arg` deploy_target may reference.
KNOWN_ROOT_ARGS = {"common_root", "home_root"}

# Fields that would reintroduce the rejected precedence/masking/multi-target
# model. Their presence anywhere in _layers.yaml is a hard validation error.
FORBIDDEN_FIELDS = {
    "rank",
    "precedence",
    "may_target",
    "target_contexts",
    "targets",
    "mask",
    "masked_contexts",
}


class LayersError(ValueError):
    """Raised for any invalid `_layers.yaml` or bad context lookup."""


@dataclass(frozen=True)
class DeployTarget:
    kind: str  # "root_arg" | "path"
    value: str  # arg name (root_arg) or path string (path)


@dataclass(frozen=True)
class Layer:
    context: str
    enabled: bool
    dir: Path  # relative to the dotclaude root
    deploy_target: DeployTarget
    description: str = ""


@dataclass(frozen=True)
class LayersRegistry:
    schema: str
    dotclaude_root: Path
    layers: dict[str, Layer] = field(default_factory=dict)

    # -- lookups -------------------------------------------------------------- #
    def require_context(self, context: str, *, include_disabled: bool = False) -> Layer:
        layer = self.layers.get(context)
        if layer is None:
            known = ", ".join(sorted(self.layers)) or "(none)"
            raise LayersError(f"unknown context {context!r}; known contexts: {known}")
        if not layer.enabled and not include_disabled:
            raise LayersError(
                f"context {context!r} is disabled in _layers.yaml; pass include_disabled "
                f"for diagnostics only (apply/verify/adopt/retire fail closed on it)"
            )
        return layer

    def master_path(self, context: str, target_relpath: str) -> Path:
        """Layered master path. Allows disabled contexts (a master may exist for a
        not-yet-provisioned enterprise layer); only the *target* side fails closed."""
        layer = self.layers.get(context)
        if layer is None:
            known = ", ".join(sorted(self.layers)) or "(none)"
            raise LayersError(f"unknown context {context!r}; known contexts: {known}")
        return self.dotclaude_root / layer.dir / target_relpath

    def target_root(
        self,
        context: str,
        *,
        root_args: Mapping[str, Path],
        include_disabled: bool = False,
    ) -> Path:
        layer = self.require_context(context, include_disabled=include_disabled)
        dt = layer.deploy_target
        if dt.kind == "root_arg":
            try:
                return Path(root_args[dt.value])
            except KeyError:
                raise LayersError(
                    f"context {context!r} deploy_target root_arg {dt.value!r} not supplied; "
                    f"got root_args={sorted(root_args)}"
                ) from None
        if dt.kind == "path":
            return Path(dt.value).expanduser()
        raise LayersError(f"context {context!r} has invalid deploy_target.kind {dt.kind!r}")

    def target_path(
        self,
        context: str,
        target_relpath: str,
        *,
        root_args: Mapping[str, Path],
        include_disabled: bool = False,
    ) -> Path:
        return self.target_root(
            context, root_args=root_args, include_disabled=include_disabled
        ) / target_relpath


def _require_no_forbidden(node: Any, *, where: str) -> None:
    if isinstance(node, Mapping):
        for key in node:
            if key in FORBIDDEN_FIELDS:
                raise LayersError(
                    f"{where}: forbidden field {key!r} — COM-176 is one-home/one-target; "
                    f"precedence/masking/multi-target metadata is rejected by design"
                )


def _parse_deploy_target(context: str, raw: Any) -> DeployTarget:
    if not isinstance(raw, Mapping):
        raise LayersError(f"context {context!r}: deploy_target must be a mapping")
    kind = raw.get("kind")
    if kind == "root_arg":
        arg = raw.get("arg")
        if not isinstance(arg, str) or not arg:
            raise LayersError(f"context {context!r}: root_arg deploy_target needs a non-empty 'arg'")
        if arg not in KNOWN_ROOT_ARGS:
            raise LayersError(
                f"context {context!r}: unknown root_arg {arg!r}; known: {sorted(KNOWN_ROOT_ARGS)}"
            )
        return DeployTarget(kind="root_arg", value=arg)
    if kind == "path":
        path = raw.get("path")
        if not isinstance(path, str) or not path:
            raise LayersError(f"context {context!r}: path deploy_target needs a non-empty 'path'")
        return DeployTarget(kind="path", value=path)
    raise LayersError(
        f"context {context!r}: deploy_target.kind must be 'root_arg' or 'path', got {kind!r}"
    )


def _parse_layer(context: str, raw: Any) -> Layer:
    if not isinstance(raw, Mapping):
        raise LayersError(f"context {context!r}: layer entry must be a mapping")
    _require_no_forbidden(raw, where=f"context {context!r}")
    if not CONTEXT_RE.match(context):
        raise LayersError(
            f"invalid context id {context!r}; must match {CONTEXT_RE.pattern}"
        )
    enabled = bool(raw.get("enabled", False))

    rel = raw.get("dir")
    if not isinstance(rel, str) or not rel:
        raise LayersError(f"context {context!r}: 'dir' must be a non-empty string")
    dir_path = Path(rel)
    if dir_path.is_absolute():
        raise LayersError(f"context {context!r}: dir {rel!r} must be relative, not absolute")
    if ".." in dir_path.parts:
        raise LayersError(f"context {context!r}: dir {rel!r} must not contain '..'")

    deploy_target = _parse_deploy_target(context, raw.get("deploy_target"))
    description = str(raw.get("description") or "")
    return Layer(
        context=context,
        enabled=enabled,
        dir=dir_path,
        deploy_target=deploy_target,
        description=description,
    )


def load_layers(
    *,
    dotclaude_root: str | Path,
    layers_path: str | Path | None = None,
) -> LayersRegistry:
    dotclaude_root = Path(dotclaude_root)
    path = Path(layers_path) if layers_path is not None else dotclaude_root / "_layers.yaml"
    if not path.is_file():
        raise LayersError(f"_layers.yaml not found at {path}")

    try:
        data = _yaml_load(path.read_text(encoding="utf-8"))
    except LayersError:
        raise
    except Exception as exc:  # pragma: no cover - parser-specific
        raise LayersError(f"{path}: YAML parse error: {exc}") from exc

    if not isinstance(data, Mapping):
        raise LayersError(f"{path}: top-level must be a mapping")
    _require_no_forbidden(data, where=str(path))

    schema = data.get("schema")
    if schema != SCHEMA:
        raise LayersError(f"{path}: schema must be {SCHEMA!r}, got {schema!r}")

    raw_layers = data.get("layers")
    if not isinstance(raw_layers, Mapping) or not raw_layers:
        raise LayersError(f"{path}: 'layers' must be a non-empty mapping")

    layers: dict[str, Layer] = {}
    for context, raw in raw_layers.items():
        layers[str(context)] = _parse_layer(str(context), raw)

    # Two enabled contexts must not share a dir or a deploy-target root.
    seen_dirs: dict[str, str] = {}
    seen_targets: dict[str, str] = {}
    for context, layer in layers.items():
        if not layer.enabled:
            continue
        dkey = str(layer.dir)
        if dkey in seen_dirs:
            raise LayersError(
                f"contexts {seen_dirs[dkey]!r} and {context!r} share dir {dkey!r}"
            )
        seen_dirs[dkey] = context
        tkey = f"{layer.deploy_target.kind}:{layer.deploy_target.value}"
        if tkey in seen_targets:
            raise LayersError(
                f"contexts {seen_targets[tkey]!r} and {context!r} share deploy target {tkey!r}"
            )
        seen_targets[tkey] = context

    return LayersRegistry(schema=schema, dotclaude_root=dotclaude_root, layers=layers)
