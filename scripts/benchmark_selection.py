"""Caller-owned benchmark metadata, selection, and execution profiles.

This module deliberately has no knowledge of repository scopes or PR labels.
"""

from __future__ import annotations

import copy
import shlex


def strings(value, name):
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{name} must be an array of nonempty strings")
    return list(dict.fromkeys(value))


def target_id(spec):
    manifest = spec.get("manifest_path", "Cargo.toml")
    return f"{spec['backend']}:{manifest}:{spec.get('bench', spec.get('name', 'benchmark'))}"


def execution_options(value, name):
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    unknown = value.keys() - {"criterion_args", "gungraun_args"}
    if unknown:
        raise ValueError(f"Unknown {name} settings: {', '.join(sorted(unknown))}")
    for key, args in value.items():
        if not isinstance(args, str):
            raise ValueError(f"{name}.{key} must be a string")
        shlex.split(args)
    return value


def select(benchmarks, config=None, *, tags=None, targets=None, profile=""):
    """Return selected specs, the complete build catalog, and selection evidence."""
    config = {} if config is None else config
    if not isinstance(config, dict) or config.get("version", 1) != 1:
        raise ValueError("benchmark_config_json must be a version 1 object")
    unknown = config.keys() - {"version", "targets", "profiles"}
    if unknown:
        raise ValueError(f"Unknown benchmark configuration fields: {', '.join(sorted(unknown))}")
    metadata = config.get("targets", {})
    profiles = config.get("profiles", {})
    if not isinstance(metadata, dict) or not isinstance(profiles, dict):
        raise ValueError("targets and profiles must be objects")
    for name, options in profiles.items():
        if not isinstance(name, str) or not name:
            raise ValueError("profile names must be nonempty strings")
        execution_options(options, f"profiles.{name}")
    if profile and profile not in profiles:
        raise ValueError(f"Unknown benchmark profile: {profile}")
    tags = strings([] if tags is None else tags, "select_tags_json")
    targets = strings([] if targets is None else targets, "select_targets_json")
    catalog = copy.deepcopy(benchmarks)
    known_ids = {target_id(spec) for spec in catalog}
    # Metadata keys use full identities; discovery remains the target inventory.
    for identity in metadata:
        if identity not in known_ids:
            raise ValueError(f"Metadata references unknown benchmark target: {identity}")
    matched_targets = set()
    available_tags = set()
    for spec in catalog:
        identity = target_id(spec)
        extra = metadata.get(identity, {})
        if not isinstance(extra, dict) or extra.keys() - {"tags", "profiles"}:
            raise ValueError(f"Invalid metadata for {identity}")
        spec_tags = strings(spec.get("tags", []), f"{identity}.tags")
        spec_tags += strings(extra.get("tags", []), f"{identity}.tags")
        spec["tags"] = sorted(set(spec_tags))
        available_tags.update(spec_tags)
        spec["target_id"] = identity
        if not isinstance(spec.get("profiles", {}), dict) or not isinstance(extra.get("profiles", {}), dict):
            raise ValueError(f"Target profiles must be an object: {identity}")
        overrides = {**spec.get("profiles", {}), **extra.get("profiles", {})}
        for name, options in overrides.items():
            if name not in profiles:
                raise ValueError(f"Unknown target profile {name!r} for {identity}")
            execution_options(options, f"{identity}.profiles.{name}")
        options = {**profiles.get(profile, {}), **overrides.get(profile, {})}
        if options and (spec.get("command") or spec.get("head_command") or spec.get("base_command")):
            raise ValueError(f"Profiles require a Cargo benchmark specification: {identity}")
        # Apply execution settings equally to moved base and current head specs.
        for side in (spec, spec.get("head"), spec.get("base")):
            if isinstance(side, dict):
                side.update(options)
        spec["profile"] = profile
        for selector in targets:
            if selector == identity:
                matched_targets.add(selector)
    unknown_tags = set(tags) - available_tags
    if unknown_tags:
        raise ValueError(f"Unknown benchmark tags: {', '.join(sorted(unknown_tags))}")
    if set(targets) - matched_targets:
        raise ValueError(f"Unknown benchmark targets: {', '.join(sorted(set(targets) - matched_targets))}")
    selected = []
    evidence = []
    for spec in catalog:
        reasons = []
        if spec["target_id"] in targets:
            reasons.append("explicit target")
        reasons.extend(f"tag:{tag}" for tag in tags if tag in spec["tags"])
        if not tags and not targets:
            reasons.append("all configured targets")
        if reasons:
            selected.append(spec)
        evidence.append({
            "target_id": spec["target_id"], "name": spec.get("name", spec.get("bench")),
            "tags": spec["tags"], "selected": bool(reasons),
            "reasons": reasons or ["outside requested selection"],
            "profile": profile,
            "notes": spec.get("profile_notes", []),
            "execution": {key: spec[key] for key in ("criterion_args", "gungraun_args") if key in spec},
        })
    return selected, catalog, {
        "version": 1, "profile": profile, "requested_tags": tags,
        "requested_targets": targets, "targets": evidence,
    }
