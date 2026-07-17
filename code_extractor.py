#!/usr/bin/env python3
"""
Code Extractor & Sanitizer (Spring Boot Java / React / Angular)
----------------------------------------------------------------
Traces dependencies from an entry source file, sanitizes sensitive package/path
and class/variable names, and produces a reversal script to undo mappings on
generated test files.

Usage:
  python code_extractor.py trace   --entry path/to/MyService.java --base com.mycompany --src src/main/java --out ./extracted
  python code_extractor.py trace   --entry src/components/MyWidget.tsx --lang react --src src --out ./extracted
  python code_extractor.py trace   --entry src/app/widget/widget.component.ts --lang angular --src src --out ./extracted
  python code_extractor.py reverse --mapping mapping.json --dir ./generated-tests
  python code_extractor.py audit   --dir ./extracted
  python code_extractor.py approve --dir ./extracted --hash <audit-hash>
  python code_extractor.py         (interactive menu)

Requirements: Python 3.9+, no third-party packages needed.
"""

from __future__ import annotations

import os
import re
import sys
import json
import shutil
import hashlib
import argparse
import functools
from datetime import datetime
from pathlib import Path


# ─────────────────────────────────────────────
#  ANSI colours (disabled on Windows if needed)
# ─────────────────────────────────────────────
USE_COLOR = sys.platform != "win32" or os.environ.get("TERM")

def c(text, code):
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text

def green(t):  return c(t, "32")
def yellow(t): return c(t, "33")
def cyan(t):   return c(t, "36")
def red(t):    return c(t, "31")
def bold(t):   return c(t, "1")
def dim(t):    return c(t, "2")


# ─────────────────────────────────────────────
#  Java import / class parsing
# ─────────────────────────────────────────────

IMPORT_RE   = re.compile(r"^import\s+(?:static\s+)?([a-zA-Z0-9_.]+);", re.MULTILINE)
PACKAGE_RE  = re.compile(r"^package\s+([a-zA-Z0-9_.]+);", re.MULTILINE)
CLASS_RE    = re.compile(r"(?:public\s+)?(?:class|interface|enum|@interface)\s+(\w+)")


def fqn_to_relative_path(fqn: str) -> str:
    return fqn.replace(".", os.sep) + ".java"


def find_source_file(fqn: str, src_root: Path) -> Path | None:
    """Search src_root for the .java file matching fqn."""
    rel = fqn_to_relative_path(fqn)
    candidate = src_root / rel
    if candidate.exists():
        return candidate
    # Fallback: glob by filename only
    class_name = fqn.split(".")[-1] + ".java"
    matches = list(src_root.rglob(class_name))
    return matches[0] if matches else None


def java_module_id(source: str, file_path: Path, src_root: Path) -> str:
    """Module identity for Java: the fully-qualified class name."""
    pkg_m = PACKAGE_RE.search(source)
    cls_m = CLASS_RE.search(source)
    pkg   = pkg_m.group(1) if pkg_m else ""
    cls   = cls_m.group(1) if cls_m else file_path.stem
    return f"{pkg}.{cls}" if pkg else cls


def java_find_deps(source: str, file_path: Path, scope: str, src_root: Path) -> list:
    """Return (fqn, resolved_path|None) for imports within the base package (scope)."""
    deps = []
    for m in IMPORT_RE.finditer(source):
        fqn = m.group(1)
        if not fqn.startswith(scope + "."):  # dot boundary: com.myco must not capture com.myco2.*
            continue
        class_name = fqn.split(".")[-1]
        if class_name == "*" or not class_name[0].isupper():
            continue
        deps.append((fqn, find_source_file(fqn, src_root)))
    return deps


def classify_java(module_id: str, source: str) -> str:
    lower = module_id.lower()
    if any(k in lower for k in ("repository", "repo", "dao")):    return "repository"
    if any(k in lower for k in ("service",)):                      return "service"
    if any(k in lower for k in ("controller", "rest", "resource")): return "controller"
    if any(k in lower for k in ("enum",)):                         return "enum"
    if any(k in lower for k in ("util", "helper", "config", "configuration")): return "util"
    return "model"


# ─────────────────────────────────────────────
#  React import / module parsing
# ─────────────────────────────────────────────

REACT_EXTS = [".tsx", ".ts", ".jsx", ".js"]

REACT_ASSET_EXTS = {
    ".css", ".scss", ".sass", ".less", ".styl",
    ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp",
    ".json", ".md", ".txt", ".yaml", ".yml",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp3", ".mp4", ".wav", ".webm",
}

# The clause before `from` may span lines (Prettier wraps named-import lists),
# so it admits newlines but not quotes or `;` — it can never skip a statement.
REACT_IMPORT_RES = [
    # import X from '...'; import { A,\n B,\n } from '...'; import '...'; import type X from '...'
    re.compile(r"^[ \t]*import\s+(?:type\s+)?(?:[^'\";]*?\bfrom\s*)?['\"]([^'\"\n]+)['\"]", re.MULTILINE),
    # export { A } from '...'; export * from '...'; multiline export { ... } from '...'
    re.compile(r"^[ \t]*export\s+(?:type\s+)?[^'\";]*?\bfrom\s*['\"]([^'\"\n]+)['\"]", re.MULTILINE),
    # require('...'), dynamic import('...')
    re.compile(r"\b(?:require|import)\(\s*['\"]([^'\"\n]+)['\"]\s*\)"),
]

# Sentinel: import points outside the project (bare module or asset) — skipped.
EXTERNAL = object()


def resolve_react_import(spec: str, importing_file: Path, src_root: Path, alias: str = "@"):
    """
    Resolve an import specifier to a source file.
    Returns a Path, None (in-project but not found), or EXTERNAL (bare module / asset).
    """
    if spec.startswith("."):
        base = importing_file.parent / spec
    elif alias and (spec == alias or spec.startswith(alias + "/")):
        base = src_root / spec[len(alias):].lstrip("/")
    else:
        return EXTERNAL  # bare module: react, axios, @myco/ui, ...

    if Path(spec).suffix.lower() in REACT_ASSET_EXTS:
        return EXTERNAL

    base = Path(os.path.normpath(str(base)))
    candidates = []
    if base.suffix.lower() in (".js", ".jsx", ".ts", ".tsx"):
        candidates.append(base)
        # NodeNext-style: './foo.js' in source may be foo.ts/foo.tsx on disk
        if base.suffix.lower() in (".js", ".jsx"):
            candidates.append(base.with_suffix(".ts"))
            candidates.append(base.with_suffix(".tsx"))
    else:
        for ext in REACT_EXTS:
            candidates.append(base.with_name(base.name + ext))
        for ext in REACT_EXTS:
            candidates.append(base / ("index" + ext))

    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def react_module_id(source: str, file_path: Path, src_root: Path) -> str:
    """Module identity for React: the src-root-relative path in posix form."""
    try:
        return file_path.resolve().relative_to(src_root.resolve()).as_posix()
    except ValueError:
        return file_path.name


def react_find_deps(source: str, file_path: Path, scope: str, src_root: Path) -> list:
    """Return (module_id, resolved_path|None) for in-project imports. scope = path alias."""
    specs = []
    for rx in REACT_IMPORT_RES:
        for m in rx.finditer(source):
            spec = m.group(1)
            if spec not in specs:
                specs.append(spec)

    deps = []
    for spec in specs:
        resolved = resolve_react_import(spec, file_path, src_root, alias=scope or "@")
        if resolved is EXTERNAL:
            continue
        if resolved is None:
            # In-project but unresolvable: keep a normalized id so it shows as ✗
            if spec.startswith("."):
                guess = Path(os.path.normpath(str(file_path.parent / spec)))
                try:
                    dep_id = guess.resolve().relative_to(src_root.resolve()).as_posix()
                except ValueError:
                    dep_id = spec
            else:
                dep_id = spec
            deps.append((str(dep_id), None))
        else:
            deps.append((react_module_id("", resolved, src_root), resolved))
    return deps


def classify_react(module_id: str, source: str) -> str:
    p = module_id.lower()
    parts = p.split("/")
    stem = Path(module_id).stem
    if re.match(r"use[A-Z]", stem) or "hooks" in parts:
        return "hook"
    if "context" in p or "provider" in p:
        return "context"
    if p.endswith(".d.ts") or any(seg in ("types", "interfaces", "models") for seg in parts):
        return "types"
    if any(seg in ("api", "apis", "services", "clients") for seg in parts):
        return "api"
    if any(seg in ("utils", "util", "helpers", "lib") for seg in parts):
        return "util"
    if p.endswith((".tsx", ".jsx")) or "components" in parts or re.search(r"<[A-Z][A-Za-z0-9]*[\s/>]", source):
        return "component"
    if re.search(r"\b(axios|fetch)\s*[.(]", source):
        return "api"
    return "module"


# ─────────────────────────────────────────────
#  Angular import / resource parsing
# ─────────────────────────────────────────────

ANGULAR_RESOURCE_EXTS = {".html", ".css", ".scss", ".sass", ".less", ".styl"}

# Component resources are not TypeScript imports, so Angular needs an
# additional pass over @Component metadata. These expressions intentionally
# accept only literal paths; computed metadata cannot be resolved statically.
ANGULAR_RESOURCE_SCALAR_RE = re.compile(
    r"\b(?:templateUrl|styleUrl)\s*:\s*['\"]([^'\"\n]+)['\"]"
)
ANGULAR_STYLE_URLS_RE = re.compile(r"\bstyleUrls\s*:\s*\[(.*?)\]", re.DOTALL)
ANGULAR_QUOTED_VALUE_RE = re.compile(r"['\"]([^'\"\n]+)['\"]")


def angular_module_id(source: str, file_path: Path, src_root: Path) -> str:
    """Module identity for Angular: the source-root-relative path."""
    return react_module_id(source, file_path, src_root)


def _angular_resource_specs(source: str) -> list[str]:
    """Return literal template/style paths referenced by Angular metadata."""
    specs = [m.group(1) for m in ANGULAR_RESOURCE_SCALAR_RE.finditer(source)]
    for array_match in ANGULAR_STYLE_URLS_RE.finditer(source):
        specs.extend(m.group(1) for m in ANGULAR_QUOTED_VALUE_RE.finditer(array_match.group(1)))
    # Preserve declaration order while avoiding duplicate work.
    return list(dict.fromkeys(specs))


def _angular_resource_dependency(spec: str, importing_file: Path,
                                 src_root: Path) -> tuple[str, Path | None] | None:
    """Resolve a relative Angular template/style reference below src_root."""
    if not spec.startswith(".") or Path(spec).suffix.lower() not in ANGULAR_RESOURCE_EXTS:
        return None

    candidate = (importing_file.parent / spec).resolve()
    try:
        module_id = candidate.relative_to(src_root.resolve()).as_posix()
    except ValueError:
        # Referenced outside the source root: we deliberately don't extract it,
        # but keep it visible as an unresolved (✗) dependency rather than
        # dropping it silently — mirroring how out-of-project TS imports are
        # surfaced. The sanitized component still references this path.
        return spec, None
    return module_id, candidate if candidate.is_file() else None


def angular_find_deps(source: str, file_path: Path, scope: str,
                      src_root: Path) -> list:
    """Trace Angular TypeScript imports plus @Component template/style files."""
    if file_path.suffix.lower() != ".ts":
        return []

    deps = react_find_deps(source, file_path, scope, src_root)
    known_ids = {module_id for module_id, _ in deps}
    for spec in _angular_resource_specs(source):
        dependency = _angular_resource_dependency(spec, file_path, src_root)
        if dependency is not None and dependency[0] not in known_ids:
            deps.append(dependency)
            known_ids.add(dependency[0])
    return deps


def classify_angular(module_id: str, source: str) -> str:
    """Classify Angular source and component-resource files for the checklist."""
    path = module_id.lower()
    # Any HTML/stylesheet resource is a template/style — including shared ones
    # referenced by more than one component — so a plain .scss is never
    # mistaken for a module (and listed as a mock collaborator in the prompt).
    if path.endswith(".html"):
        return "template"
    if any(path.endswith(ext) for ext in ANGULAR_RESOURCE_EXTS - {".html"}):
        return "style"
    if path.endswith((".routing.module.ts", ".routes.ts")):
        return "routing"
    for suffix, kind in (
        (".component.ts", "component"),
        (".service.ts", "service"),
        (".module.ts", "ngmodule"),
        (".directive.ts", "directive"),
        (".pipe.ts", "pipe"),
        (".guard.ts", "guard"),
        (".resolver.ts", "resolver"),
        (".interceptor.ts", "interceptor"),
    ):
        if path.endswith(suffix):
            return kind
    if path.endswith((".actions.ts", ".reducer.ts", ".effects.ts", ".selectors.ts")):
        return "state"
    if path.endswith((".model.ts", ".models.ts", ".types.ts", ".d.ts")):
        return "types"
    if any(segment in path.split("/") for segment in ("utils", "util", "helpers")):
        return "util"
    return "module"


# ─────────────────────────────────────────────
#  Variable/Class name mapping
# ─────────────────────────────────────────────

def to_snake_case(name: str) -> str:
    """Convert CamelCase to snake_case (Ingredient → ingredient, IngredientService → ingredient_service)"""
    # Insert underscore before capitals (except first char)
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def to_upper_snake_case(name: str) -> str:
    """Convert to UPPER_SNAKE_CASE (Ingredient → INGREDIENT)"""
    return to_snake_case(name).upper()


def to_kebab_case(name: str) -> str:
    """Convert to kebab-case (IngredientRow → ingredient-row) — common for React file names"""
    return to_snake_case(name).replace("_", "-")


def to_pascal_case(name: str) -> str:
    """Convert snake_case or other to PascalCase (ingredient → Ingredient, ingredient_service → IngredientService)"""
    # Handle snake_case
    if "_" in name:
        return "".join(w.capitalize() for w in name.split("_"))
    # Already PascalCase or needs first letter capitalized
    return name[0].upper() + name[1:] if name else name


def to_camel_case(name: str) -> str:
    """Convert to camelCase (Ingredient → ingredient, IngredientService → ingredientService)"""
    if name.isupper():
        return name.lower()  # acronyms: API → api, not aPI
    pascal = to_pascal_case(name)
    return pascal[0].lower() + pascal[1:] if pascal else pascal


# Heuristic identifier plural/singular pairs — this is for generating name
# variations of code identifiers, not a linguistics library. Unknown words
# fall through to the simple suffix rules; truly unknown forms stay unchanged.
IRREGULAR_PLURALS = {
    "child": "children", "person": "people", "status": "statuses",
    "index": "indices", "matrix": "matrices", "analysis": "analyses",
    "criterion": "criteria", "datum": "data",
}
IRREGULAR_SINGULARS = {v: k for k, v in IRREGULAR_PLURALS.items()}


def _match_case(pattern_word: str, replacement: str) -> str:
    """Re-apply pattern_word's case shape (UPPER / Capitalized / lower) to replacement."""
    if pattern_word.isupper():
        return replacement.upper()
    if pattern_word[:1].isupper():
        return replacement.capitalize()
    return replacement


def pluralize(word: str) -> str:
    """Heuristic English pluralization for identifiers."""
    lower = word.lower()
    if lower in IRREGULAR_PLURALS:
        return _match_case(word, IRREGULAR_PLURALS[lower])

    def suffix(s: str) -> str:
        return s.upper() if word.isupper() else s

    # Simple rules
    if lower.endswith(("s", "x", "z", "ch", "sh")):
        return word + suffix("es")
    elif lower.endswith("y") and len(word) > 1 and word[-2].lower() not in "aeiou":
        return word[:-1] + suffix("ies")
    else:
        return word + suffix("s")


def singularize(word: str) -> str:
    """Heuristic English singularization for identifiers."""
    lower = word.lower()
    if lower in IRREGULAR_SINGULARS:
        return _match_case(word, IRREGULAR_SINGULARS[lower])

    # Simple rules
    if lower.endswith("ies") and len(word) > 3 and word[-4].lower() not in "aeiou":
        return word[:-3] + ("Y" if word.isupper() else "y")
    elif lower.endswith(("ches", "shes", "xes", "zes", "sses")):
        return word[:-2]
    elif lower.endswith("s") and len(word) > 1 and not lower.endswith(("ss", "us", "is")):
        return word[:-1]
    else:
        return word


def _tagged_name_variations(base_name: str) -> list:
    """
    (variation, number_tag) pairs across all case families, where the tag
    records HOW the variation was derived: 'as_is' (the name as given),
    'plural' (pluralize applied), or 'singular' (singularize applied).

    Tagging at generation time is what keeps round trips exact: re-deriving
    plurality later via singularize() misfires on names its heuristics refuse
    (Menus, Data, APIs), which made forward and reverse mappings disagree.
    """
    tagged = {}

    def add(form, tag):
        if form and form not in tagged:  # first derivation wins; as_is comes first per family
            tagged[form] = tag

    for case_fn in (to_pascal_case, to_camel_case, to_upper_snake_case,
                    to_snake_case, to_kebab_case):
        base = case_fn(base_name)
        add(base, "as_is")
        add(pluralize(base), "plural")
        add(singularize(base), "singular")
    return list(tagged.items())


def generate_name_variations(base_name: str) -> list:
    """
    Generate common variations of a class/variable name.
    E.g., 'Ingredient' → ['Ingredient', 'ingredient', 'ingredients', 'INGREDIENT', 'INGREDIENTS']
    """
    return sorted(form for form, _ in _tagged_name_variations(base_name))


def variable_mapping_patterns(from_name: str, to_name: str) -> list:
    """
    Expand one variable mapping into (regex_pattern, replacement) pairs
    covering all name variations, word boundaries, and compound identifiers.
    """
    pairs = []
    for from_var, number_tag in _tagged_name_variations(from_name):
        if from_var.isupper():
            to_var = to_upper_snake_case(to_name)
        elif "-" in from_var:
            to_var = to_kebab_case(to_name)
        elif "_" in from_var and not from_var[0].isupper():
            to_var = to_snake_case(to_name)
        elif from_var[0].isupper():
            to_var = to_pascal_case(to_name)
        else:
            to_var = to_camel_case(to_name)
        if number_tag == "plural":
            to_var = pluralize(to_var)
        elif number_tag == "singular":
            to_var = singularize(to_var)

        # UPPER_SNAKE_CASE as prefix in compound names (INGREDIENT in INGREDIENT_TYPE)
        if from_var.isupper() and "_" not in from_var:
            pairs.append((re.escape(from_var) + r"(?=_[A-Z])", to_var))

        # Standalone, with word boundaries (underscore excluded from the lookahead
        # for consistency with compound names)
        pairs.append((r"(?<![a-zA-Z0-9_])" + re.escape(from_var) + r"(?![a-zA-Z0-9])", to_var))

        # PascalCase compounds (IngredientService → ItemService, myIngredient → myItem)
        if "_" not in from_var and "-" not in from_var and from_var[0].isupper() and len(from_var) > 1:
            pairs.append((re.escape(from_var) + r"(?=[A-Z])", to_var))
            pairs.append((r"(?<=[a-z])" + re.escape(from_var), to_var))

        # camelCase prefixes (ingredientId → itemId)
        if from_var and from_var[0].islower() and "-" not in from_var and len(from_var) > 1:
            pairs.append((re.escape(from_var) + r"(?=[A-Z])", to_var))

    return pairs


# ─────────────────────────────────────────────
#  Single-pass substitution engine
# ─────────────────────────────────────────────

_LOOKAROUND_RE = re.compile(r"\(\?<?[=!][^)]*\)")


def _pattern_core_len(pattern: str) -> int:
    """Length of a pattern's literal core, ignoring zero-width lookarounds."""
    return len(_LOOKAROUND_RE.sub("", pattern))


class Renamer:
    """
    Single-pass, order-independent substitution engine.

    All (pattern, replacement) rules are compiled into ONE alternation regex and
    applied with a single re.sub, so a replacement is never re-scanned by another
    rule. That makes application idempotent and independent of mapping order —
    provided no 'to' value collides with another mapping's 'from' variations
    (validate_mappings warns about that).

    Rules are sorted longest-literal-core-first so that at any position the most
    specific rule wins (OrderItem beats Order). Replacements are returned from a
    callback, so they are always literal — '\\' and '$' in names are safe.
    """

    def __init__(self, rules):
        self._replacements = {}
        parts = []
        seen = set()
        ordered = sorted(enumerate(rules),
                         key=lambda t: (-_pattern_core_len(t[1][0]), t[0]))
        for _, (pattern, replacement) in ordered:
            if pattern in seen:
                continue  # first (most specific / earliest) rule wins
            seen.add(pattern)
            group = f"g{len(self._replacements)}"
            self._replacements[group] = replacement
            parts.append(f"(?P<{group}>{pattern})")
        self._regex = re.compile("|".join(parts)) if parts else None

    def apply(self, text: str) -> str:
        return self.apply_count(text)[0]

    def apply_count(self, text: str) -> tuple[str, int]:
        """Like apply, but also returns the number of substitutions made."""
        if self._regex is None:
            return text, 0
        count = 0

        def counting_lookup(match):
            nonlocal count
            count += 1
            return self._replacements[match.lastgroup]

        return self._regex.sub(counting_lookup, text), count


_cached_renamer = functools.lru_cache(maxsize=None)(Renamer)


def _valid_mappings(mappings: list) -> list:
    return [m for m in mappings if m.get("from") and m.get("to")]


def package_mapping_patterns(frm: str, to: str) -> list:
    """
    Boundary-guarded pattern for a package / path-segment mapping.
    '.', '/', quotes and whitespace all count as boundaries, so com.myco still
    matches inside com.myco.service and "com.myco", but not inside com.myco2.
    """
    return [(r"(?<![A-Za-z0-9_])" + re.escape(frm) + r"(?![A-Za-z0-9_])", to)]


def build_package_rules(pkg_mappings: list) -> tuple:
    rules = []
    for m in _valid_mappings(pkg_mappings):
        rules.extend(package_mapping_patterns(m["from"], m["to"]))
    return tuple(rules)


def build_variable_rules(var_mappings: list) -> tuple:
    rules = []
    for m in _valid_mappings(var_mappings):
        rules.extend(variable_mapping_patterns(m["from"], m["to"]))
    return tuple(rules)


def build_content_rules(mapping_dict: dict) -> tuple:
    """Rules for file contents: package mappings + variable mappings.

    Names are intentionally replaced inside strings and comments too — a
    sensitive name must not survive anywhere in the sanitized output.
    """
    return (build_package_rules(mapping_dict.get("package", []))
            + build_variable_rules(mapping_dict.get("variable", [])))


def build_path_rules(mapping_dict: dict, lang: dict) -> tuple:
    """Rules for file paths: language-specific package path variants + variable mappings."""
    rules = []
    for m in _valid_mappings(mapping_dict.get("package", [])):
        for f_variant, t_variant in lang["pkg_path_variants"](m["from"], m["to"]):
            rules.extend(package_mapping_patterns(f_variant, t_variant))
    return tuple(rules) + build_variable_rules(mapping_dict.get("variable", []))


def validate_mappings(mapping_dict: dict) -> list:
    """
    Return human-readable warnings for mapping sets that cannot behave predictably:
    duplicate 'from' values, from == to, and collisions where one mapping's 'to'
    would be matched by another mapping's 'from' (the engine never re-scans
    replacements, so such cascades no longer happen — warn instead).
    """
    warnings = []
    pkg = _valid_mappings(mapping_dict.get("package", []))
    var = _valid_mappings(mapping_dict.get("variable", []))

    for kind, mappings in (("package", pkg), ("variable", var)):
        seen = {}
        for m in mappings:
            if m["from"] == m["to"]:
                warnings.append(f"{kind} mapping '{m['from']}' maps to itself")
            if m["from"] in seen and seen[m["from"]] != m["to"]:
                warnings.append(f"duplicate {kind} mapping for '{m['from']}' "
                                f"('{seen[m['from']]}' vs '{m['to']}') — the first one wins")
            seen.setdefault(m["from"], m["to"])

    for m1 in pkg:
        for m2 in pkg:
            if m1 is not m2 and m2["from"] in m1["to"]:
                warnings.append(f"package mapping '{m2['from']}' → '{m2['to']}' would have "
                                f"cascaded onto the output of '{m1['from']}' → '{m1['to']}'; "
                                f"replacements are applied in a single pass, so it will not")

    var_variations = [(m, set(generate_name_variations(m["from"])),
                       set(generate_name_variations(m["to"]))) for m in var]
    for m1, _, to_vars1 in var_variations:
        for m2, from_vars2, _ in var_variations:
            if m1 is not m2 and to_vars1 & from_vars2:
                warnings.append(f"variable mapping '{m2['from']}' → '{m2['to']}' overlaps the "
                                f"output of '{m1['from']}' → '{m1['to']}' "
                                f"({', '.join(sorted(to_vars1 & from_vars2))}); "
                                f"replacements are applied in a single pass, so it will not cascade")
    return warnings


def apply_variable_mappings(source: str, var_mappings: list) -> str:
    """
    Apply variable/class name mappings with word boundary awareness.
    Handles both standalone names and compound names (e.g., IngredientService, INGREDIENT_TYPE).
    Preserves plural/singular forms during mapping.
    """
    return _cached_renamer(build_variable_rules(var_mappings)).apply(source)


# ─────────────────────────────────────────────
#  Recursive dependency tracer
# ─────────────────────────────────────────────

def trace(entry_path: Path, scope: str, src_root: Path, lang: dict) -> dict:
    """
    BFS from entry_path, following in-project imports.
    scope: base package (spring) or path alias (react).
    Returns {module_id: {"path": Path|None, "type": str, "source": str}}
    """
    visited = {}
    queue   = [entry_path]

    while queue:
        current = queue.pop(0)
        try:
            source = read_source(current)
        except Exception as e:
            print(red(f"  [!] Cannot read {current}: {e}"))
            continue

        module_id = lang["module_id"](source, current, src_root)
        if module_id in visited:
            continue

        visited[module_id] = {
            "path":   current,
            "type":   lang["classify"](module_id, source),
            "source": source,
        }

        for dep_id, dep_path in lang["find_deps"](source, current, scope, src_root):
            if dep_id not in visited:
                if dep_path:
                    queue.append(dep_path)
                else:
                    visited[dep_id] = {
                        "path":   None,
                        "type":   lang["classify"](dep_id, ""),
                        "source": "",
                    }

    return visited


# ─────────────────────────────────────────────
#  Sanitization
# ─────────────────────────────────────────────

MAPPING_SCHEMA_VERSION = 2


def load_mappings(mapping_file: Path) -> dict:
    """
    Load mappings and normalize to the v2 schema:
      {"version": 2, "language": ..., "package": [...], "variable": [...], "strings": {...}}
    Accepts the legacy bare-list format (package mappings only) and v1 dicts
    (no 'version'/'strings' keys).
    """
    with open(mapping_file, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        data = {"package": data}
    data.setdefault("package", [])
    data.setdefault("variable", [])
    data.setdefault("strings", {})
    data["version"] = MAPPING_SCHEMA_VERSION
    return data


def save_mappings(mappings: dict, mapping_file: Path):
    """Save mappings in the v2 schema (see load_mappings)."""
    mappings = {**mappings, "version": MAPPING_SCHEMA_VERSION}
    with open(mapping_file, "w", encoding="utf-8") as f:
        json.dump(mappings, f, indent=2)
    print(green(f"  Mappings saved → {mapping_file}"))


def get_language(mapping_dict: dict, cli_flag: str | None = None) -> str:
    """Language precedence: explicit CLI flag > mapping.json 'language' > default (spring)."""
    key = cli_flag or mapping_dict.get("language") or DEFAULT_LANG
    return key if key in LANGUAGES else DEFAULT_LANG


def apply_all_mappings(source: str, mapping_dict: dict) -> str:
    """Apply both package and variable mappings to source code in a single pass."""
    return _cached_renamer(build_content_rules(mapping_dict)).apply(source)


def apply_all_mappings_count(source: str, mapping_dict: dict) -> tuple[str, int]:
    """Like apply_all_mappings, but also returns the substitution count (for dry runs)."""
    return _cached_renamer(build_content_rules(mapping_dict)).apply_count(source)


def apply_path_mappings(path_value, mapping_dict: dict, lang: dict) -> Path:
    """Apply package/path and variable mappings to a path string or Path."""
    return Path(_cached_renamer(build_path_rules(mapping_dict, lang)).apply(str(path_value)))


def rename_source_path(file_path: Path, mapping_dict: dict, lang: dict,
                       base_dir: Path | None = None) -> Path:
    """
    Rename a source file path, including parent folders, via path mappings.
    With base_dir set, only the part below base_dir is eligible — a mapping
    name occurring in an ancestor segment must never move files out of the
    directory being processed.
    """
    if base_dir is not None:
        renamed_path = base_dir / apply_path_mappings(file_path.relative_to(base_dir),
                                                      mapping_dict, lang)
    else:
        renamed_path = apply_path_mappings(file_path, mapping_dict, lang)
    if renamed_path.name == file_path.name and renamed_path.parent == file_path.parent:
        return file_path
    return renamed_path


def java_output_rel_path(module_id: str, mapping_dict: dict) -> Path:
    """Output path for a Java class: sanitized FQN → package-directory path."""
    var_mappings = mapping_dict.get("variable", [])
    sanitized_fqn = _cached_renamer(build_package_rules(mapping_dict.get("package", []))).apply(module_id)

    pkg_name, sep, class_name = sanitized_fqn.rpartition(".")
    if sep:
        class_name = apply_variable_mappings(class_name, var_mappings)
        sanitized_fqn = f"{pkg_name}.{class_name}"
    else:
        sanitized_fqn = apply_variable_mappings(sanitized_fqn, var_mappings)

    return apply_path_mappings(Path(fqn_to_relative_path(sanitized_fqn)), mapping_dict, LANGUAGES["spring"])


def react_output_rel_path(module_id: str, mapping_dict: dict) -> Path:
    """Output path for a React module: the relative path with mappings applied."""
    return apply_path_mappings(Path(module_id), mapping_dict, LANGUAGES["react"])


# Matches a string literal (group 1, kept) or a comment (removed), so '//' or
# '/*' inside a string literal — e.g. a URL — never truncates code.
JAVA_COMMENT_OR_STRING_RE = re.compile(
    r'("[^"\\\n]*(?:\\.[^"\\\n]*)*")|(?:/\*.*?\*/|//[^\n]*)', re.DOTALL)


def strip_comments_java(source: str) -> str:
    source = JAVA_COMMENT_OR_STRING_RE.sub(lambda m: m.group(1) or "", source)
    # Collapse excessive blank lines
    source = re.sub(r"\n{3,}", "\n\n", source)
    return source


def strip_javadoc_tags(source: str) -> str:
    return re.sub(r"@(author|since|version|see)\b[^\n]*", "", source, flags=re.IGNORECASE)


class StringMaskRegistry:
    """
    Run-wide registry of masked string literals, so STR_n tokens are unique
    across all files of a run and can be restored on reversal.

    Stores the FULL original literal including its quote characters; identical
    literals share one token. Seed with a previously saved mapping's 'strings'
    section so re-runs never reuse an existing index for a different literal.
    """

    def __init__(self, existing: dict | None = None):
        self._by_token = dict(existing or {})     # "STR_0" -> '"literal"'
        self._by_literal = {v: k for k, v in self._by_token.items()}
        self._next = 0
        for token in self._by_token:
            m = re.fullmatch(MASK_PREFIX + r"_(\d+)", token)
            if m:
                self._next = max(self._next, int(m.group(1)) + 1)

    def add(self, literal: str) -> str:
        """Register a literal (quotes included) and return its bare token, e.g. 'STR_7'."""
        token = self._by_literal.get(literal)
        if token is None:
            token = f"{MASK_PREFIX}_{self._next}"
            self._next += 1
            self._by_token[token] = literal
            self._by_literal[literal] = token
        return token

    def to_dict(self) -> dict:
        return dict(self._by_token)


# Linear-time string-literal regex (unrolled loop — no nested quantifiers, so no
# catastrophic backtracking on pathological input). Known limits: Java text
# blocks (\"\"\") and char literals are not masked.
JAVA_STRING_RE = re.compile(r'"[^"\\\n]*(?:\\.[^"\\\n]*)*"')

# Mask-token shape is owned here; everything else derives from MASK_PREFIX.
MASK_PREFIX = "STR"
MASK_TOKEN_RE = re.compile(r"([\"'`])" + MASK_PREFIX + r"_(\d+)\1")
BARE_MASK_TOKEN_RE = re.compile(r"\b" + MASK_PREFIX + r"_\d+\b")


def mask_strings_java(source: str, registry: StringMaskRegistry) -> str:
    def replacer(m):
        return f'"{registry.add(m.group(0))}"'
    return JAVA_STRING_RE.sub(replacer, source)


def unmask_strings(text: str, strings: dict) -> tuple[str, int]:
    """
    Restore masked literals: any quoted STR_n token ("STR_1", 'STR_1' or `STR_1`,
    regardless of which quote style the original had) becomes the recorded
    original literal, verbatim. Returns (text, restored_count).
    """
    if not strings:
        return text, 0
    count = 0

    def replacer(m):
        nonlocal count
        original = strings.get(f"{MASK_PREFIX}_{m.group(2)}")
        if original is None:
            return m.group(0)  # unknown token — leave untouched
        count += 1
        return original

    return MASK_TOKEN_RE.sub(replacer, text), count


def strip_loggers_java(source: str) -> str:
    return re.sub(r"[ \t]*log\.(debug|info|warn|error|trace)\([^;]+\);\n?", "\n", source)


# ── JS/TS scanner-based passes ──
#
# Regex-only stripping is unsafe for JS ('//' inside URLs, template literals),
# so a small character scanner tokenizes the source first.
# Known limitation: regex literals (/foo\/bar/) are not tracked.

def _scan_js(source: str) -> list:
    """
    Tokenize JS/TS source into (kind, text) segments.
    kind ∈ {"code", "line_comment", "block_comment", "string", "template"}.
    """
    segments = []
    n = len(source)
    i = 0
    code_start = 0

    def emit_code(upto):
        if upto > code_start:
            segments.append(("code", source[code_start:upto]))

    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            end = source.find("\n", i)
            end = n if end == -1 else end
            emit_code(i)
            segments.append(("line_comment", source[i:end]))
            i = end
            code_start = i
        elif ch == "/" and nxt == "*":
            end = source.find("*/", i + 2)
            end = n if end == -1 else end + 2
            emit_code(i)
            segments.append(("block_comment", source[i:end]))
            i = end
            code_start = i
        elif ch in ("'", '"'):
            j = i + 1
            closed = False
            while j < n:
                cj = source[j]
                if cj == "\\":
                    j += 2
                    continue
                if cj == ch:
                    closed = True
                    break
                if cj == "\n":
                    break
                j += 1
            if closed:
                emit_code(i)
                segments.append(("string", source[i:j + 1]))
                i = j + 1
                code_start = i
            else:
                i += 1  # unterminated — leave as code
        elif ch == "`":
            j = i + 1
            depth = 0  # ${...} nesting
            closed = False
            while j < n:
                cj = source[j]
                if cj == "\\":
                    j += 2
                    continue
                if cj == "$" and j + 1 < n and source[j + 1] == "{":
                    depth += 1
                    j += 2
                    continue
                if cj == "}" and depth > 0:
                    depth -= 1
                elif cj == "`" and depth == 0:
                    closed = True
                    break
                j += 1
            if closed:
                emit_code(i)
                segments.append(("template", source[i:j + 1]))
                i = j + 1
                code_start = i
            else:
                i += 1
        else:
            i += 1

    emit_code(n)
    return segments


def strip_comments_react(source: str) -> str:
    # JSX comments: {/* ... */} — remove the braces too, not just the comment
    source = re.sub(r"\{\s*/\*.*?\*/\s*\}", "", source, flags=re.DOTALL)
    parts = [text for kind, text in _scan_js(source) if kind not in ("line_comment", "block_comment")]
    result = "".join(parts)
    result = re.sub(r"[ \t]+\n", "\n", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result


# A string is a module specifier iff the code immediately before it ends with
# `from`, a bare `import` (side-effect import), or an open `import(`/`require(`.
_SPECIFIER_CONTEXT_RE = re.compile(r"(?:\bfrom|\bimport)\s*$|\b(?:require|import)\s*\(\s*$")


def mask_strings_react(source: str, registry: StringMaskRegistry) -> str:
    """
    Mask string literals — but never module specifiers, and never template
    literals containing ${...} interpolation.

    Specifiers are detected from the CODE context directly preceding the
    string (spanning newlines), not the line prefix — an `export const X =
    '...'` must be masked, and a multiline `import(\n'...')` must not be.
    """
    out = []
    code_tail = ""  # trailing slice of the most recently emitted CODE

    for kind, text in _scan_js(source):
        if kind == "string":
            if _SPECIFIER_CONTEXT_RE.search(code_tail):
                out.append(text)
            else:
                q = text[0]
                out.append(f"{q}{registry.add(text)}{q}")
        elif kind == "template" and "${" not in text:
            out.append(f"`{registry.add(text)}`")
        else:
            out.append(text)
            if kind == "code":
                code_tail = (code_tail + text)[-200:]

    return "".join(out)


def strip_loggers_react(source: str) -> str:
    return re.sub(r"[ \t]*console\.(log|info|warn|error|debug|trace)\([^;\n]*\);?\n?", "\n", source)


HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
STYLESHEET_COMMENT_OR_STRING_RE = re.compile(
    r'("[^"\\]*(?:\\.[^"\\]*)*")'
    r"|('[^'\\]*(?:\\.[^'\\]*)*')"
    r"|(/\*.*?\*/)"
    # SCSS/Sass/Less `//` line comments — both full-line and trailing
    # (`color: red; // note`). Only fire when the `//` starts a line or is
    # preceded by whitespace/`;`/`{`/`}`, so URL schemes like `http://` inside
    # an unquoted `url(...)` are left intact.
    r"|(^[ \t]*//[^\n]*|(?<=[ \t;{}])//[^\n]*)",
    re.DOTALL | re.MULTILINE,
)

# Angular metadata strings carry structural information needed to understand
# and compile the extracted feature. They are renamed by the normal mapping
# pass, but deliberately not replaced with opaque STR_n placeholders.
#
# CRITICAL: these key-based exemptions are only safe when the string is
# genuinely inside an Angular metadata decorator argument (@Component(...),
# @Directive(...), @Pipe(...), @Injectable(...), @NgModule(...)). Applied to
# arbitrary code they would fail open — `{ name: 'secret' }`, `db.query('...')`
# and `cond ? name : 'secret'` all superficially match these key/call shapes.
# `mask_strings_angular_typescript` gates every exemption below on real
# decorator context tracked by `_AngularDecoratorContext`.
ANGULAR_STRUCTURAL_SCALAR_RE = re.compile(
    r"\b(?:selector|template|templateUrl|styleUrl|path|redirectTo|outlet|"
    r"providedIn|name|alias)\s*:\s*$"
)
# Only strings that are direct arguments to an Angular property decorator,
# e.g. @Input('alias') — this shape is self-anchored to the `@Decorator(`
# token, so it is safe to honour regardless of the surrounding span.
ANGULAR_DECORATOR_STRING_RE = re.compile(
    r"@(?:Input|Output|HostBinding|HostListener|Attribute)\s*\(\s*$"
)
# Angular animation DSL calls (trigger/state/transition/query). These only
# carry structural meaning inside an @Component animations: array, so the
# masker requires an enclosing decorator span before honouring them.
ANGULAR_ANIMATION_STRING_RE = re.compile(
    r"\b(?:trigger|state|transition|query)\s*\(\s*$"
)
# Metadata decorators whose argument object is the only place the key-based
# exemptions above are trusted.
ANGULAR_METADATA_DECORATOR_RE = re.compile(
    r"@(?:Component|Directive|Pipe|Injectable|NgModule)\s*$"
)
# A `styles`/`styleUrls` key immediately preceding an opening `[`.
ANGULAR_STYLE_ARRAY_KEY_RE = re.compile(r"\b(?:styles|styleUrls)\s*:\s*$")


class _AngularDecoratorContext:
    """
    Tracks, while walking a file's JS/TS *code* segments left-to-right,
    whether the current position sits inside an Angular metadata decorator
    argument (``@Component(...)`` and friends) and, more precisely, inside a
    ``styles``/``styleUrls`` array within one.

    String-masking exemptions are scoped to these spans so that ordinary
    TypeScript — generic object literals, method calls, ternaries — is masked
    normally instead of leaking through key/call-shaped regexes.
    """

    def __init__(self):
        self._paren_depth = 0
        self._decorator_paren_depth = None  # paren depth of the open decorator arg
        self._bracket_base = 0              # bracket-stack size when the span opened
        self._bracket_stack = []            # one bool per open '[': True = style array
        self._tail = ""                     # recent code, for token lookbehind

    @property
    def in_decorator(self) -> bool:
        return self._decorator_paren_depth is not None

    @property
    def in_style_array(self) -> bool:
        return self.in_decorator and any(self._bracket_stack)

    def feed_code(self, text: str):
        """Advance the context state over one CODE segment."""
        for ch in text:
            if ch == "(":
                self._paren_depth += 1
                if (self._decorator_paren_depth is None
                        and ANGULAR_METADATA_DECORATOR_RE.search(self._tail)):
                    self._decorator_paren_depth = self._paren_depth
                    self._bracket_base = len(self._bracket_stack)
            elif ch == ")":
                if (self._decorator_paren_depth is not None
                        and self._paren_depth == self._decorator_paren_depth):
                    # Leaving the decorator argument — fail closed on any
                    # style arrays that were still open inside it.
                    self._decorator_paren_depth = None
                    del self._bracket_stack[self._bracket_base:]
                self._paren_depth = max(0, self._paren_depth - 1)
            elif ch == "[":
                is_style = bool(self.in_decorator
                                and ANGULAR_STYLE_ARRAY_KEY_RE.search(self._tail))
                self._bracket_stack.append(is_style)
            elif ch == "]":
                if self._bracket_stack:
                    self._bracket_stack.pop()
            self._tail = (self._tail + ch)[-64:]


def strip_comments_angular_html(source: str) -> str:
    """Remove HTML comments while leaving Angular bindings untouched."""
    result = HTML_COMMENT_RE.sub("", source)
    result = re.sub(r"[ \t]+\n", "\n", result)
    return re.sub(r"\n{3,}", "\n\n", result)


def strip_comments_stylesheet(source: str) -> str:
    """Remove CSS/SCSS comments without treating URL text as a comment."""
    result = STYLESHEET_COMMENT_OR_STRING_RE.sub(
        lambda match: match.group(1) or match.group(2) or "", source
    )
    result = re.sub(r"[ \t]+\n", "\n", result)
    return re.sub(r"\n{3,}", "\n\n", result)


def mask_strings_angular_typescript(source: str,
                                    registry: StringMaskRegistry) -> str:
    """
    Mask Angular TypeScript literals while preserving imports and framework
    metadata that links components, templates, styles, and routes.
    """
    out = []
    code_tail = ""
    ctx = _AngularDecoratorContext()

    def is_structural() -> bool:
        # Style arrays are self-identifying; scalar keys and animation calls
        # are only trusted inside a real @Component/@Directive/... span, so
        # generic `name:`/`path:`/`.query(` in ordinary code still gets masked.
        if ctx.in_style_array:
            return True
        return ctx.in_decorator and (
            ANGULAR_STRUCTURAL_SCALAR_RE.search(code_tail) or
            ANGULAR_ANIMATION_STRING_RE.search(code_tail))

    for kind, segment in _scan_js(source):
        if kind == "string":
            if (_SPECIFIER_CONTEXT_RE.search(code_tail)
                    or ANGULAR_DECORATOR_STRING_RE.search(code_tail)
                    or is_structural()):
                out.append(segment)
            else:
                quote = segment[0]
                out.append(f"{quote}{registry.add(segment)}{quote}")
        elif kind == "template":
            if "${" in segment or is_structural():
                out.append(segment)
            else:
                out.append(f"`{registry.add(segment)}`")
        else:
            out.append(segment)
            if kind == "code":
                code_tail = (code_tail + segment)[-500:]
                ctx.feed_code(segment)
    return "".join(out)


def sanitize_angular(source: str, mapping_dict: dict, options: dict,
                     registry: StringMaskRegistry | None,
                     source_path: Path | None) -> str:
    """Sanitize Angular TypeScript, templates, and component styles by file type."""
    if options.get("mask_strings") and registry is None:
        raise ValueError("mask_strings requires a StringMaskRegistry — "
                         "masking without recording the originals is irreversible")

    source = apply_all_mappings(source, mapping_dict)
    suffix = source_path.suffix.lower() if source_path else ".ts"
    if suffix == ".html":
        if options.get("strip_comments"):
            source = strip_comments_angular_html(source)
        # HTML attributes and text are not programming-language string
        # literals. Known protected terms are still replaced by mappings.
    elif suffix in ANGULAR_RESOURCE_EXTS:
        if options.get("strip_comments"):
            source = strip_comments_stylesheet(source)
    else:
        if options.get("strip_comments"):
            source = strip_comments_react(source)
        if options.get("strip_javadoc"):
            source = strip_javadoc_tags(source)
        if options.get("mask_strings"):
            source = mask_strings_angular_typescript(source, registry)
        if options.get("strip_loggers"):
            source = strip_loggers_react(source)
    return source.strip()


def sanitize(source: str, mapping_dict: dict, options: dict, lang: dict,
             registry: StringMaskRegistry | None = None,
             source_path: Path | None = None) -> str:
    if lang.get("sanitize_source"):
        return lang["sanitize_source"](source, mapping_dict, options,
                                       registry, source_path)
    # Masking runs after renaming, so recorded literals contain sanitized names.
    # Reversal unmasks first, then un-renames — restoring the originals exactly.
    if options.get("mask_strings") and registry is None:
        raise ValueError("mask_strings requires a StringMaskRegistry — "
                         "masking without recording the originals is irreversible")
    source = apply_all_mappings(source, mapping_dict)
    if options.get("strip_comments"):   source = lang["strip_comments"](source)
    if options.get("strip_javadoc"):    source = lang["strip_doc_tags"](source)
    if options.get("mask_strings"):     source = lang["mask_strings"](source, registry)
    if options.get("strip_loggers"):    source = lang["strip_loggers"](source)
    return source.strip()


# ─────────────────────────────────────────────
#  Reversal
# ─────────────────────────────────────────────

def reverse_mappings(mapping_dict: dict) -> dict:
    """Reverse both package and variable mappings for reversal."""
    reversed_dict = {
        "package": [{"from": m["to"], "to": m["from"]} for m in mapping_dict.get("package", []) if m.get("from") and m.get("to")],
        "variable": [{"from": m["to"], "to": m["from"]} for m in mapping_dict.get("variable", []) if m.get("from") and m.get("to")]
    }
    return reversed_dict


def _glob_sources(target_dir: Path, lang: dict) -> list:
    """All source files for the language, deepest paths first."""
    files = []
    for ext in lang["extensions"]:
        files.extend(target_dir.rglob(f"*{ext}"))
    return sorted(files, key=lambda path: len(path.parts), reverse=True)


REVERSAL_MARKER = ".code_extractor_reversed.json"


def read_source(path: Path) -> str:
    """Read a source file as strict UTF-8; fall back with a loud warning instead of silent corruption."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="replace")
        print(yellow(f"  [!] {path}: not valid UTF-8 — {text.count(chr(0xFFFD))} character(s) replaced. "
                     f"Reversal may not restore this file exactly."))
        return text


def _mapping_fingerprint(mapping_dict: dict) -> str:
    canonical = json.dumps(mapping_dict, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def apply_reversal(target_dir: Path, mapping_dict: dict, lang: dict,
                   dry_run: bool = False, backup: bool = True, force: bool = False) -> dict:
    """
    Restore original names in all source files under target_dir:
    unmask string literals first, then un-rename identifiers/packages, then
    rename file paths. Refuses to run twice on the same directory with the
    same mapping (marker file) unless force=True.
    """
    result = {"renamed": [], "changed": [], "warnings": [], "strings_restored": 0,
              "refused": False}
    reversed_maps = reverse_mappings(mapping_dict)
    strings = mapping_dict.get("strings", {})
    fingerprint = _mapping_fingerprint(mapping_dict)

    marker_file = target_dir / REVERSAL_MARKER
    if marker_file.exists() and not force and not dry_run:
        try:
            marker = json.loads(marker_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            marker = {}
        if marker.get("mapping_sha256") == fingerprint:
            print(red(f"  This directory was already reversed with this mapping on "
                      f"{marker.get('timestamp', 'an earlier run')}."))
            print(red("  Re-running could corrupt names. Use --force to override."))
            result["refused"] = True
            return result

    source_files = _glob_sources(target_dir, lang)
    if not source_files:
        print(yellow("  No matching source files found in target directory."))
        return result

    if backup and not dry_run:
        backup_dir = target_dir.with_name(
            target_dir.name + ".backup-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        shutil.copytree(target_dir, backup_dir)
        print(green(f"  Backup → {backup_dir}"))

    # Content first (on stable paths), then file renames.
    for sf in source_files:
        original = read_source(sf)
        unmasked, restored = unmask_strings(original, strings)
        updated, substitutions = apply_all_mappings_count(unmasked, reversed_maps)
        leftover = sorted(set(BARE_MASK_TOKEN_RE.findall(updated)))
        if leftover:
            reason = ("unrestorable placeholder(s) left in place" if strings else
                      "placeholder(s) found but the mapping has no 'strings' section "
                      "(predates string masking support?) — left as-is")
            result["warnings"].append(f"{sf}: {reason}: {', '.join(leftover)}")
        if updated != original:
            result["changed"].append(sf)
            result["strings_restored"] += restored
            if dry_run:
                print(f"  Would update: {sf}  "
                      f"({substitutions} substitution(s), {restored} string(s) restored)")
            else:
                sf.write_text(updated, encoding="utf-8")

    for sf in source_files:
        renamed_path = rename_source_path(sf, reversed_maps, lang, base_dir=target_dir)
        if renamed_path != sf:
            if renamed_path.exists():
                print(yellow(f"  [skip] file rename collision: {sf.name} -> {renamed_path.name}"))
                result["warnings"].append(f"rename collision: {sf} -> {renamed_path}")
                continue
            result["renamed"].append((sf, renamed_path))
            if dry_run:
                print(f"  Would rename: {sf} -> {renamed_path}")
            else:
                renamed_path.parent.mkdir(parents=True, exist_ok=True)
                sf.rename(renamed_path)

    for w in result["warnings"]:
        print(yellow(f"  [!] {w}"))

    if dry_run:
        print(bold(f"  {len(result['renamed'])} file(s) to rename, "
                   f"{len(result['changed'])} file(s) to update. No changes written (--dry-run)."))
    else:
        marker_file.write_text(json.dumps({
            "mapping_sha256": fingerprint,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "renamed": len(result["renamed"]),
            "changed": len(result["changed"]),
        }, indent=2), encoding="utf-8")
        print(green(f"  Reversal complete — {len(result['renamed'])} file(s) renamed, "
                    f"{len(result['changed'])}/{len(source_files)} files updated."))
    return result


# ─────────────────────────────────────────────
#  Prompt templates
# ─────────────────────────────────────────────

def java_prompt_template(deps: dict, mapping_dict: dict, test_framework: str = None) -> str:
    var_mappings = mapping_dict.get("variable", [])
    dep_classes = [
        apply_variable_mappings(fqn.split(".")[-1], var_mappings)
        for fqn, i in deps.items() if i["type"] != "controller"
    ]
    return f"""I have a Spring Boot service I need unit tests for.

Please generate comprehensive JUnit 5 unit tests using Mockito.

Requirements:
- Use @ExtendWith(MockitoExtension.class) — no @SpringBootTest
- Cover: happy path, edge cases, null/empty inputs, exception scenarios
- Use AssertJ assertions (assertThat)
- Mock all dependencies: {', '.join(dep_classes) if dep_classes else '[see classes below]'}
- Do not assume any Spring context or infrastructure is running

Here are the sanitized source files:

[paste the contents of each extracted file below this line]
"""


def react_prompt_template(deps: dict, mapping_dict: dict, test_framework: str = "jest") -> str:
    var_mappings = mapping_dict.get("variable", [])
    dep_modules = []
    for mid, i in deps.items():
        if i["type"] == "component":
            continue
        p = Path(mid)
        # index.ts barrels are better identified by their directory (types/index.ts → types)
        name = p.stem
        if name == "index" and p.parent.name:
            name = p.parent.name
        dep_modules.append(apply_variable_mappings(name, var_mappings))
    if test_framework == "vitest":
        fw_label, mock_fn = "Vitest", "vi.mock"
    else:
        fw_label, mock_fn = "Jest", "jest.mock"
    return f"""I have a React component/module I need unit tests for.

Please generate comprehensive unit tests using {fw_label} and React Testing Library.

Requirements:
- Use @testing-library/react and @testing-library/user-event
- Mock dependency modules with {mock_fn}: {', '.join(dep_modules) if dep_modules else '[see modules below]'}
- Cover: rendering, user interaction, edge cases, and async/API error paths
- No real network calls — mock api/service modules directly
- Avoid snapshot-only tests; assert on visible behavior

Here are the sanitized source files:

[paste the contents of each extracted file below this line]
"""


def angular_prompt_template(deps: dict, mapping_dict: dict,
                            test_framework: str = None) -> str:
    var_mappings = mapping_dict.get("variable", [])
    dependencies = []
    excluded_types = {"component", "template", "style"}
    for module_id, info in deps.items():
        if info["type"] in excluded_types:
            continue
        name = Path(module_id).name.split(".")[0]
        sanitized = apply_variable_mappings(name, var_mappings)
        if sanitized not in dependencies:
            dependencies.append(sanitized)
    return f"""I have an Angular component/service/module I need unit tests for.

Please generate comprehensive unit tests using Jasmine and Angular TestBed.

Requirements:
- Use TestBed for Angular components and dependency injection
- Cover: happy paths, edge cases, validation, loading/error states, and observable behavior
- Test component inputs, outputs, user interactions, and rendered template behavior where applicable
- Mock injected services and collaborators: {', '.join(dependencies) if dependencies else '[see files below]'}
- Use HttpTestingController when the code uses HttpClient; make no real network calls
- Keep tests isolated from unrelated application modules

Here are the sanitized source files, including referenced templates and styles:

[paste the contents of each extracted file below this line]
"""


# ─────────────────────────────────────────────
#  Language registry
# ─────────────────────────────────────────────

JAVA_TYPE_ORDER  = ["controller", "service", "model", "repository", "util", "enum"]
JAVA_TYPE_LABELS = {
    "controller":  "Controllers / entry points",
    "service":     "Services",
    "model":       "Domain models",
    "repository":  "Repository interfaces (interface only — no impl)",
    "util":        "Utilities / helpers",
    "enum":        "Enums / constants",
}

REACT_TYPE_ORDER  = ["component", "hook", "context", "api", "types", "util", "module"]
REACT_TYPE_LABELS = {
    "component": "Components",
    "hook":      "Hooks",
    "context":   "Contexts / providers",
    "api":       "API / service clients",
    "types":     "Types / interfaces",
    "util":      "Utilities / helpers",
    "module":    "Other modules",
}

ANGULAR_TYPE_ORDER = [
    "component", "template", "style", "service", "ngmodule", "routing",
    "directive", "pipe", "guard", "resolver", "interceptor", "state",
    "types", "util", "module",
]
ANGULAR_TYPE_LABELS = {
    "component":   "Components",
    "template":    "Component templates",
    "style":       "Component styles",
    "service":     "Services",
    "ngmodule":    "Angular modules",
    "routing":     "Routing",
    "directive":   "Directives",
    "pipe":        "Pipes",
    "guard":       "Route guards",
    "resolver":    "Route resolvers",
    "interceptor": "HTTP interceptors",
    "state":       "State management",
    "types":       "Types / models",
    "util":        "Utilities / helpers",
    "module":      "Other modules",
}


def _java_pkg_path_variants(frm: str, to: str) -> list:
    return [(frm.replace(".", os.sep), to.replace(".", os.sep))]


def _react_pkg_path_variants(frm: str, to: str) -> list:
    """Emit POSIX and Windows separator variants unconditionally, so a tree
    sanitized on one OS still renames correctly when reversed on the other
    (rather than depending on the host's os.sep)."""
    candidates = [
        (frm, to),
        (frm.replace("/", os.sep), to.replace("/", os.sep)),
        (frm.replace("/", "\\"), to.replace("/", "\\")),
    ]
    return list(dict.fromkeys(candidates))


def _angular_pkg_path_variants(frm: str, to: str) -> list:
    """Angular paths may be exchanged between Windows and POSIX machines."""
    candidates = [
        (frm, to),
        (frm.replace("/", os.sep), to.replace("/", os.sep)),
        (frm.replace("/", "\\"), to.replace("/", "\\")),
    ]
    return list(dict.fromkeys(candidates))


LANGUAGES = {
    "spring": {
        "key":               "spring",
        "label":             "Spring Boot (Java)",
        "extensions":        [".java"],
        "default_src":       "src/main/java",
        "src_markers":       ["src/main/java", "src\\main\\java"],
        "entry_hint":        ".java",
        "pkg_mapping_title": "Package / token mappings",
        "pkg_mapping_hint":  "e.g. com.classified → com.example",
        "doc_tag_label":     "Javadoc",
        "logger_label":      "logger statements (log.*)",
        "module_id":         java_module_id,
        "find_deps":         java_find_deps,
        "id_to_rel_path":    fqn_to_relative_path,
        "output_rel_path":   java_output_rel_path,
        "classify":          classify_java,
        "type_order":        JAVA_TYPE_ORDER,
        "type_labels":       JAVA_TYPE_LABELS,
        "strip_comments":    strip_comments_java,
        "strip_doc_tags":    strip_javadoc_tags,
        "mask_strings":      mask_strings_java,
        "strip_loggers":     strip_loggers_java,
        "pkg_path_variants": _java_pkg_path_variants,
        "prompt_template":   java_prompt_template,
    },
    "react": {
        "key":               "react",
        "label":             "React (JS/TS)",
        "extensions":        [".tsx", ".ts", ".jsx", ".js"],
        "default_src":       "src",
        "src_markers":       [f"{os.sep}src{os.sep}", "/src/", "\\src\\"],
        "entry_hint":        ".js/.jsx/.ts/.tsx",
        "pkg_mapping_title": "Path / module segment mappings",
        "pkg_mapping_hint":  "e.g. features/payroll → features/feature1",
        "doc_tag_label":     "JSDoc",
        "logger_label":      "console.* statements",
        "module_id":         react_module_id,
        "find_deps":         react_find_deps,
        "id_to_rel_path":    lambda mid: mid,
        "output_rel_path":   react_output_rel_path,
        "classify":          classify_react,
        "type_order":        REACT_TYPE_ORDER,
        "type_labels":       REACT_TYPE_LABELS,
        "strip_comments":    strip_comments_react,
        "strip_doc_tags":    strip_javadoc_tags,
        "mask_strings":      mask_strings_react,
        "strip_loggers":     strip_loggers_react,
        "pkg_path_variants": _react_pkg_path_variants,
        "prompt_template":   react_prompt_template,
    },
    "angular": {
        "key":               "angular",
        "label":             "Angular (TypeScript)",
        "extensions":        [".ts", ".html", ".css", ".scss", ".sass", ".less", ".styl"],
        "default_src":       "src",
        "src_markers":       [f"{os.sep}src{os.sep}", "/src/", "\\src\\"],
        "entry_hint":        ".component.ts/.service.ts/.module.ts/.ts",
        "pkg_mapping_title": "Path / module segment mappings",
        "pkg_mapping_hint":  "e.g. app/payroll → app/feature1",
        "doc_tag_label":     "JSDoc",
        "logger_label":      "console.* statements",
        "module_id":         angular_module_id,
        "find_deps":         angular_find_deps,
        "id_to_rel_path":    lambda mid: mid,
        "output_rel_path":   react_output_rel_path,
        "classify":          classify_angular,
        "type_order":        ANGULAR_TYPE_ORDER,
        "type_labels":       ANGULAR_TYPE_LABELS,
        "strip_comments":    strip_comments_react,
        "strip_doc_tags":    strip_javadoc_tags,
        "mask_strings":      mask_strings_angular_typescript,
        "strip_loggers":     strip_loggers_react,
        "pkg_path_variants": _angular_pkg_path_variants,
        "prompt_template":   angular_prompt_template,
        "sanitize_source":   sanitize_angular,
    },
}

DEFAULT_LANG = "spring"


def infer_language(entry: str) -> str:
    lower_name = Path(entry).name.lower()
    angular_suffixes = (
        ".component.ts", ".service.ts", ".module.ts", ".directive.ts",
        ".pipe.ts", ".guard.ts", ".resolver.ts", ".interceptor.ts",
        ".routes.ts",
    )
    if lower_name.endswith(angular_suffixes):
        return "angular"
    ext = Path(entry).suffix.lower()
    for key, lang_def in LANGUAGES.items():
        if ext in lang_def["extensions"]:
            return key
    return DEFAULT_LANG


# ─────────────────────────────────────────────
#  Output helpers
# ─────────────────────────────────────────────

def print_checklist(deps: dict, lang: dict):
    grouped = {}
    for module_id, info in deps.items():
        t = info["type"]
        grouped.setdefault(t, []).append((module_id, info["path"]))

    print()
    print(bold("═══ Extraction checklist ═══"))
    for t in lang["type_order"]:
        if t not in grouped:
            continue
        print(f"\n{cyan(lang['type_labels'].get(t, t))}")
        for module_id, path in grouped[t]:
            status = green("✓ found") if path else red("✗ not found")
            rel    = str(path) if path else lang["id_to_rel_path"](module_id)
            print(f"  [ ] {rel}  {dim(status)}")

    missing = [(mid, i) for mid, i in deps.items() if i["path"] is None]
    if missing:
        print()
        print(bold(red("═══ Requires manual decision ═══")))
        print(red(f"  {len(missing)} referenced file(s) will NOT be extracted:"))
        for module_id, _ in missing:
            print(red(f"    ✗ {module_id}"))
        print(yellow("  The sanitized output still references these names. For each one,"))
        print(yellow("  either locate it (adjust --src / add it manually) or confirm the"))
        print(yellow("  reference itself is safe to leave in the transferred files."))


def write_extracted(deps: dict, mapping_dict: dict, options: dict, out_dir: Path, lang: dict,
                    registry: StringMaskRegistry | None = None):
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for module_id, info in deps.items():
        if info["path"] is None:
            print(yellow(f"  [skip] {module_id} — source not found"))
            continue

        sanitized = sanitize(info["source"], mapping_dict, options, lang, registry,
                             source_path=info["path"])
        rel_path  = lang["output_rel_path"](module_id, mapping_dict)
        dest      = out_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(sanitized, encoding="utf-8")
        written += 1
        print(green(f"  ✓ {rel_path}"))

    print()
    print(bold(f"  {written} file(s) written to: {out_dir}"))


def write_claude_prompt(deps: dict, out_dir: Path, lang: dict, mapping_dict: dict, test_framework: str = None):
    prompt = lang["prompt_template"](deps, mapping_dict, test_framework)
    prompt_file = out_dir / "CLAUDE_PROMPT.txt"
    prompt_file.write_text(prompt, encoding="utf-8")
    print(green(f"  Prompt template → {prompt_file}"))


def write_reverse_instructions(out_dir: Path):
    """Emit instructions for reversing sanitization on generated test files."""
    instructions = f"""How to restore original names in generated test files
=====================================================

1. Save the AI-generated tests into a directory, e.g. ./generated-tests/
2. Preview what will change:

   python code_extractor.py reverse --mapping {out_dir / 'mapping.json'} --dir ./generated-tests --dry-run

3. Apply the reversal (a timestamped backup of the directory is created first):

   python code_extractor.py reverse --mapping {out_dir / 'mapping.json'} --dir ./generated-tests

   Options: --no-backup to skip the backup, --force to re-run on a directory
   that was already reversed with this mapping.

SECURITY: mapping.json maps sanitized names back to the sensitive originals.
Keep it inside the airgap — never share it alongside the sanitized files.
"""
    instructions_file = out_dir / "REVERSE_INSTRUCTIONS.txt"
    instructions_file.write_text(instructions, encoding="utf-8")
    print(green(f"  Reversal instructions → {instructions_file}"))


# ─────────────────────────────────────────────
#  Transfer verification & audit
# ─────────────────────────────────────────────
#
# The sanitizer is a denylist: it can only transform what the mapping names.
# Before output leaves the environment, two code-side gates apply:
#
#  1. verify_sanitized_output — the fixed-point invariant (re-applying the
#     mapping changes nothing) enforced at runtime, not just in the tests.
#     A violation aborts the extraction.
#  2. audit_output — a recall-oriented residual-content report over the
#     output (surviving strings/comments/HTML text, secret-shaped patterns,
#     classification markings, unmapped identifier vocabulary) that a human
#     must review and explicitly approve before transfer.

AUDIT_REPORT_TXT  = "TRANSFER_AUDIT.txt"
AUDIT_REPORT_JSON = "TRANSFER_AUDIT.json"
APPROVAL_MARKER   = ".transfer_approval.json"
# Artifacts that live in the output directory but must never be transferred
# (mapping.json is the decoder ring) or are audit metadata about the output
# rather than part of it. Excluded from scanning and from the audit hash.
AUDIT_EXCLUDED_FILES = {
    "mapping.json", "REVERSE_INSTRUCTIONS.txt",
    AUDIT_REPORT_TXT, AUDIT_REPORT_JSON, APPROVAL_MARKER, REVERSAL_MARKER,
}
DO_NOT_TRANSFER = ("mapping.json", "REVERSE_INSTRUCTIONS.txt")

AUDIT_CODE_EXTS       = {".java", ".ts", ".tsx", ".js", ".jsx"}
AUDIT_STYLESHEET_EXTS = ANGULAR_RESOURCE_EXTS - {".html"}

# Classification banner / portion markings — a hard stop, never approvable.
CLASSIFICATION_MARKING_RE = re.compile(
    r"\b(?:TOP\s+SECRET|SECRET|CONFIDENTIAL|UNCLASSIFIED)\s*//"  # SECRET//NOFORN
    r"|\bNOFORN\b"
    r"|\bFOUO\b"
    r"|\bREL\s+TO\s+[A-Z]{2,}"
    r"|\((?:U|C|S|TS)(?://[A-Z]{2,})+\)"                         # (S//NF)
)

# Shapes that are sensitive regardless of vocabulary. Recall-oriented: false
# positives cost a reviewer a glance; false negatives cross the boundary.
SECRET_PATTERNS = {
    "private-key":   re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "jwt":           re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+"),
    "ipv4":          re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}"
                                r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b"),
    "email":         re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
    "url":           re.compile(r"\bhttps?://[^\s\"'`<>)]+"),
    "unc-path":      re.compile(r"\\\\[\w.$-]+\\[\w.$-]+"),
    "guid":          re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                                r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
    "credential-assignment": re.compile(
        r"(?i)\b(?:password|passwd|pwd|api[_-]?key|secret|token)\s*[:=]\s*['\"][^'\"]{4,}"),
}

_ENTROPY_CANDIDATE_RE = re.compile(r"\b[A-Za-z0-9+/=_-]{24,}\b")
_ENTROPY_THRESHOLD = 4.0
_STR_PLACEHOLDER_RE = re.compile(r"STR_\d+")
_NUMERIC_CONSTANT_RE = re.compile(r"\b\d{4,}\b")
_IDENTIFIER_RE = re.compile(r"\b[A-Za-z_$][A-Za-z0-9_$]{2,}\b")
_HTML_TEXT_NODE_RE = re.compile(r">([^<]+)<")
_HTML_ATTR_VALUE_RE = re.compile(r"""[\w-]+\s*=\s*(?:"([^"]+)"|'([^']+)')""")
_CSS_URL_RE = re.compile(r"url\(\s*['\"]?([^'\")]+?)['\"]?\s*\)")

# Language keywords and pervasive framework/library names, excluded from the
# unmapped-identifier inventory to keep it reviewable. Anything domain-shaped
# should NOT be in this list — the inventory errs toward showing too much.
_AUDIT_COMMON_IDENTIFIERS = frozenset("""
abstract any arguments assert async await boolean break byte case catch char
class const constructor continue debugger declare default delete do double
else enum export extends false final finally float for from function get goto
if implements import in instanceof int interface is let long module namespace
native new null number object of package private protected public readonly
return set short static string super switch symbol synchronized this throw
throws transient true try type typeof undefined unknown var void volatile
while with yield
String Integer Long Double Float Boolean Character Byte Short Object List Map
Set ArrayList HashMap HashSet Optional Override Deprecated SuppressWarnings
Exception RuntimeException IllegalArgumentException Autowired Service
Repository Controller RestController Entity Table Column Id GeneratedValue
Test BeforeEach AfterEach Mock InjectMocks
console log info warn error debug trace length push pop map filter reduce
forEach indexOf includes slice splice join split trim replace concat then
catch finally resolve reject Promise Array JSON Math Date RegExp Error window
document require exports
React useState useEffect useContext useMemo useCallback useRef Fragment Props
Component Injectable Directive Pipe NgModule Input Output EventEmitter
OnInit OnDestroy OnChanges Observable Subject BehaviorSubject Subscription
HttpClient HttpHeaders HttpParams ActivatedRoute Router RouterModule
FormBuilder FormGroup FormControl Validators TestBed ComponentFixture
PipeTransform providedIn selector templateUrl styleUrls standalone imports
declarations providers bootstrap subscribe unsubscribe pipe ngOnInit
ngOnDestroy ngOnChanges
""".split())


def _shannon_entropy(token: str) -> float:
    from math import log2
    counts = {}
    for ch in token:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(token)
    return -sum((c / n) * log2(c / n) for c in counts.values())


def _audited_files(out_dir: Path) -> list:
    """(rel_posix, Path) for every file subject to verification/audit."""
    files = []
    for p in sorted(out_dir.rglob("*")):
        if p.is_file() and p.name not in AUDIT_EXCLUDED_FILES:
            files.append((p.relative_to(out_dir).as_posix(), p))
    return files


def verify_sanitized_output(out_dir: Path, mapping_dict: dict, lang: dict) -> list:
    """
    Runtime fixed-point gate: re-applying the mapping to any emitted file's
    content or path must change nothing. Returns a list of violation strings;
    non-empty means the output is NOT fully sanitized and must not leave.
    """
    violations = []
    for rel, p in _audited_files(out_dir):
        mapped = str(apply_path_mappings(rel, mapping_dict, lang))
        if mapped not in (rel, rel.replace("/", os.sep)):
            violations.append(f"path is not a fixed point of the mapping: {rel} → {mapped}")
        try:
            content = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            violations.append(f"non-UTF-8 file in output (cannot verify): {rel}")
            continue
        if apply_all_mappings(content, mapping_dict) != content:
            violations.append(f"content is not a fixed point of the mapping: {rel}")
    return violations


def _finding(category: str, severity: str, rel: str, line: int, detail: str) -> dict:
    return {"category": category, "severity": severity,
            "file": rel, "line": line, "detail": detail[:160]}


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _audit_scan_lines(rel: str, text: str) -> list:
    """Pattern scans that apply to every audited file, line by line."""
    findings = []
    for i, line in enumerate(text.splitlines(), 1):
        if CLASSIFICATION_MARKING_RE.search(line):
            findings.append(_finding("classification-marking", "block", rel, i,
                                     line.strip()))
        for name, pattern in SECRET_PATTERNS.items():
            for m in pattern.finditer(line):
                findings.append(_finding(f"secret-pattern:{name}", "review",
                                         rel, i, m.group(0)))
        for m in _ENTROPY_CANDIDATE_RE.finditer(line):
            token = m.group(0)
            if _shannon_entropy(token) > _ENTROPY_THRESHOLD:
                findings.append(_finding("high-entropy-token", "review",
                                         rel, i, token))
    return findings


def _audit_scan_code(rel: str, text: str) -> tuple:
    """Surviving strings/comments/numbers plus identifiers, via _scan_js."""
    findings = []
    identifiers = {}
    pos = 0
    for kind, segment in _scan_js(text):
        line = _line_of(text, pos)
        if kind in ("string", "template"):
            inner = segment[1:-1].strip()
            if inner and not _STR_PLACEHOLDER_RE.fullmatch(inner):
                findings.append(_finding("surviving-string", "info", rel, line, inner))
        elif kind in ("line_comment", "block_comment"):
            body = segment.strip("/* \t\n")
            if body:
                findings.append(_finding("surviving-comment", "info", rel, line, body))
        else:  # code
            for m in _NUMERIC_CONSTANT_RE.finditer(segment):
                findings.append(_finding("numeric-constant", "info", rel,
                                         _line_of(text, pos + m.start()), m.group(0)))
            for m in _IDENTIFIER_RE.finditer(segment):
                name = m.group(0)
                if (name not in _AUDIT_COMMON_IDENTIFIERS
                        and not _STR_PLACEHOLDER_RE.fullmatch(name)):
                    identifiers[name] = identifiers.get(name, 0) + 1
        pos += len(segment)
    return findings, identifiers


def _audit_scan_html(rel: str, text: str) -> list:
    findings, seen = [], set()
    for m in _HTML_TEXT_NODE_RE.finditer(text):
        value = m.group(1).strip()
        if value and value not in seen:
            seen.add(value)
            findings.append(_finding("html-text", "info", rel,
                                     _line_of(text, m.start()), value))
    for m in _HTML_ATTR_VALUE_RE.finditer(text):
        value = (m.group(1) or m.group(2) or "").strip()
        if value and any(c.isalpha() for c in value) and value not in seen:
            seen.add(value)
            findings.append(_finding("html-attribute", "info", rel,
                                     _line_of(text, m.start()), value))
    return findings


def _audit_scan_stylesheet(rel: str, text: str) -> list:
    findings = []
    for m in STYLESHEET_COMMENT_OR_STRING_RE.finditer(text):
        line = _line_of(text, m.start())
        if m.group(1) or m.group(2):
            inner = (m.group(1) or m.group(2))[1:-1].strip()
            if inner:
                findings.append(_finding("surviving-string", "info", rel, line, inner))
        else:
            body = (m.group(3) or m.group(4) or "").strip("/* \t\n")
            if body:
                findings.append(_finding("surviving-comment", "info", rel, line, body))
    for m in _CSS_URL_RE.finditer(text):
        findings.append(_finding("surviving-string", "info", rel,
                                 _line_of(text, m.start()), m.group(1)))
    return findings


def _sanitized_vocabulary(mapping_dict: dict) -> set:
    """Every identifier/path segment the mapping is allowed to have produced."""
    vocab = set()
    for m in mapping_dict.get("variable", []):
        if m.get("to"):
            vocab.update(generate_name_variations(m["to"]))
    for m in mapping_dict.get("package", []):
        if m.get("to"):
            vocab.update(seg for seg in re.split(r"[./\\]", m["to"]) if seg)
    return vocab


def audit_hash(out_dir: Path) -> str:
    """Content hash over the audited files — what an approval attests to."""
    digest = hashlib.sha256()
    for rel, p in _audited_files(out_dir):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(p.read_bytes()).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def audit_output(out_dir: Path, mapping_dict: dict, lang: dict) -> dict:
    """
    Residual-content audit over an extraction output directory. Blocking
    findings (classification markings, verification failures) make the
    output unapprovable; 'review' and 'info' findings are the material a
    human reviewer signs off on via `approve`.
    """
    findings = [_finding("verification", "block", "", 0, violation)
                for violation in verify_sanitized_output(out_dir, mapping_dict, lang)]
    identifiers = {}

    for rel, p in _audited_files(out_dir):
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # already reported as a blocking verification finding
        findings.extend(_audit_scan_lines(rel, text))
        suffix = p.suffix.lower()
        if suffix in AUDIT_CODE_EXTS:
            code_findings, file_identifiers = _audit_scan_code(rel, text)
            findings.extend(code_findings)
            for name, count in file_identifiers.items():
                identifiers[name] = identifiers.get(name, 0) + count
        elif suffix == ".html":
            findings.extend(_audit_scan_html(rel, text))
        elif suffix in AUDIT_STYLESHEET_EXTS:
            findings.extend(_audit_scan_stylesheet(rel, text))

    vocab = _sanitized_vocabulary(mapping_dict)
    unmapped = sorted(((n, c) for n, c in identifiers.items() if n not in vocab),
                      key=lambda item: (-item[1], item[0]))

    segments = set()
    for rel, _ in _audited_files(out_dir):
        for part in Path(rel).parts:
            segments.update(seg for seg in part.split(".")[:1] if seg)
    unmapped_segments = sorted(seg for seg in segments if seg not in vocab)

    severity_order = {"block": 0, "review": 1, "info": 2}
    findings.sort(key=lambda f: (severity_order[f["severity"]], f["file"], f["line"]))
    counts = {"block": 0, "review": 0, "info": 0}
    for f in findings:
        counts[f["severity"]] += 1

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "output_dir": str(out_dir),
        "audit_hash": audit_hash(out_dir),
        "counts": counts,
        "findings": findings,
        "unmapped_identifiers": unmapped,
        "unmapped_path_segments": unmapped_segments,
    }


def write_audit_report(audit: dict, out_dir: Path) -> Path:
    """Write TRANSFER_AUDIT.txt (for the reviewer) and .json (for tooling)."""
    lines = [
        "TRANSFER AUDIT — residual-content report",
        "=" * 55,
        f"Generated: {audit['generated_at']}",
        f"Audit hash: {audit['audit_hash']}",
        "",
        "This report lists everything the sanitizer did NOT transform in the",
        "output. A human must review it and approve the exact content hash",
        "above before the files leave this environment:",
        "",
        f"  python code_extractor.py approve --dir {out_dir} --hash {audit['audit_hash'][:12]}",
        "",
        f"DO NOT TRANSFER these files (they stay inside): {', '.join(DO_NOT_TRANSFER)}",
        "",
    ]
    labels = {"block": "BLOCKING — output cannot be approved while these exist",
              "review": "REVIEW — sensitive-shaped content, confirm each one",
              "info": "RESIDUAL CONTENT — untransformed text that will transfer"}
    for severity in ("block", "review", "info"):
        group = [f for f in audit["findings"] if f["severity"] == severity]
        lines.append(f"{labels[severity]}: {len(group)}")
        lines.append("-" * 55)
        for f in group:
            location = f"{f['file']}:{f['line']}" if f["file"] else "(output)"
            lines.append(f"  [{f['category']}] {location}: {f['detail']}")
        lines.append("")

    lines.append(f"Unmapped identifier vocabulary (top {min(len(audit['unmapped_identifiers']), 50)} "
                 f"of {len(audit['unmapped_identifiers'])} — every name below transfers as-is):")
    lines.append("-" * 55)
    for name, count in audit["unmapped_identifiers"][:50]:
        lines.append(f"  {name}  ×{count}")
    lines.append("")
    lines.append("Path segments not derived from the mapping:")
    lines.append("-" * 55)
    for seg in audit["unmapped_path_segments"]:
        lines.append(f"  {seg}")
    lines.append("")

    txt_path = out_dir / AUDIT_REPORT_TXT
    txt_path.write_text("\n".join(lines), encoding="utf-8")
    (out_dir / AUDIT_REPORT_JSON).write_text(
        json.dumps(audit, indent=2), encoding="utf-8")
    return txt_path


def load_approval(out_dir: Path) -> dict | None:
    marker = out_dir / APPROVAL_MARKER
    if not marker.exists():
        return None
    try:
        return json.loads(marker.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def approval_state(audit: dict, out_dir: Path) -> str:
    """One of 'blocked', 'approved', 'stale', 'unapproved'."""
    if audit["counts"]["block"]:
        return "blocked"
    approval = load_approval(out_dir)
    if approval is None:
        return "unapproved"
    return "approved" if approval.get("audit_hash") == audit["audit_hash"] else "stale"


def print_audit_summary(audit: dict, out_dir: Path, state: str | None = None):
    counts = audit["counts"]
    if state is None:
        state = approval_state(audit, out_dir)
    print()
    print(bold("═══ Transfer audit ═══"))
    print(f"  Blocking: {red(str(counts['block'])) if counts['block'] else green('0')}"
          f"   Review: {yellow(str(counts['review'])) if counts['review'] else green('0')}"
          f"   Residual: {counts['info']}"
          f"   Unmapped identifiers: {len(audit['unmapped_identifiers'])}")
    print(f"  Report: {out_dir / AUDIT_REPORT_TXT}")
    if state == "blocked":
        print(red(bold("  ✗ BLOCKING findings — this output cannot be approved for transfer.")))
        for f in audit["findings"]:
            if f["severity"] == "block":
                location = f"{f['file']}:{f['line']}" if f["file"] else ""
                print(red(f"    [{f['category']}] {location} {f['detail']}"))
    elif state == "approved":
        approval = load_approval(out_dir) or {}
        print(green(bold(f"  ✓ APPROVED for transfer at {approval.get('approved_at')} "
                         f"(hash {audit['audit_hash'][:12]})")))
    else:
        if state == "stale":
            print(yellow("  [!] Output changed since it was approved — re-review required."))
        print(red(bold("  ⚠ NOT APPROVED FOR TRANSFER — review the report, then run:")))
        print(bold(f"    python code_extractor.py approve --dir {out_dir} "
                   f"--hash {audit['audit_hash'][:12]}"))


_PROTECTION_LABELS = {
    "strip_comments": "comment stripping",
    "strip_javadoc":  "doc-tag stripping",
    "mask_strings":   "string masking",
    "strip_loggers":  "logger stripping",
}


def print_disabled_protections(options: dict):
    disabled = [label for key, label in _PROTECTION_LABELS.items()
                if not options.get(key)]
    if disabled:
        print()
        print(red(bold(f"  ⚠ PROTECTIONS DISABLED: {', '.join(disabled)}")))
        print(red("    Content those protections would remove will remain in the output"))
        print(red("    and will be visible in the transfer audit."))


def run_extraction(deps: dict, mapping_dict: dict, options: dict, out_dir: Path,
                   lang: dict, lang_key: str, test_framework: str = None,
                   dry_run: bool = False):
    """Extraction pipeline shared by the CLI and interactive frontends."""
    print_disabled_protections(options)

    if dry_run:
        print()
        print(bold("Dry run — planned output:"))
        for module_id, info in deps.items():
            if info["path"] is None:
                print(yellow(f"  Would skip:  {module_id} — source not found"))
                continue
            rel_path = lang["output_rel_path"](module_id, mapping_dict)
            _, count = apply_all_mappings_count(info["source"], mapping_dict)
            print(f"  Would write: {out_dir / rel_path}  ({count} substitution(s))")
        for artifact in ("mapping.json", "CLAUDE_PROMPT.txt", "REVERSE_INSTRUCTIONS.txt",
                         AUDIT_REPORT_TXT, AUDIT_REPORT_JSON):
            print(f"  Would write: {out_dir / artifact}")
        print(bold("  No files written (--dry-run)."))
        return

    print()
    print(bold("Writing sanitized files..."))
    registry = StringMaskRegistry(mapping_dict.get("strings"))
    write_extracted(deps, mapping_dict, options, out_dir, lang, registry)
    write_claude_prompt(deps, out_dir, lang, mapping_dict, test_framework)

    # Save mappings for reversal: 'language' lets `reverse` auto-detect the
    # project type; 'strings' is what makes --mask-strings reversible.
    mapping_dict["language"] = lang_key
    mapping_dict["strings"] = registry.to_dict()
    save_mappings(mapping_dict, out_dir / "mapping.json")
    write_reverse_instructions(out_dir)

    # Hard gate: the output must be a fixed point of the mapping. A violation
    # means something escaped the renamer — never hand that to a transfer.
    violations = verify_sanitized_output(out_dir, mapping_dict, lang)
    if violations:
        print()
        print(red(bold("✗ SANITIZATION VERIFICATION FAILED — output must not be transferred:")))
        for violation in violations:
            print(red(f"    {violation}"))
        sys.exit(2)
    print(green("  ✓ Verified: output is a fixed point of the mapping"))

    audit = audit_output(out_dir, mapping_dict, lang)
    write_audit_report(audit, out_dir)
    print_audit_summary(audit, out_dir)


# ─────────────────────────────────────────────
#  Interactive menu
# ─────────────────────────────────────────────

def prompt_language() -> str:
    keys = list(LANGUAGES.keys())
    print()
    print(bold("Project type"))
    for i, key in enumerate(keys, 1):
        print(f"  {i}. {LANGUAGES[key]['label']}")
    choice = input(f"  Choice [1-{len(keys)}]: ").strip()
    try:
        return keys[int(choice) - 1]
    except (ValueError, IndexError):
        return keys[0]


def prompt_mappings(lang: dict) -> list:
    mappings = []
    print()
    print(bold(lang["pkg_mapping_title"]))
    print(dim(f"  Enter sensitive→safe substitutions ({lang['pkg_mapping_hint']})"))
    print(dim("  Press Enter with an empty 'from' to finish.\n"))
    while True:
        frm = input("  Sensitive string (from): ").strip()
        if not frm:
            break
        to = input("  Safe replacement  (to) : ").strip()
        if to:
            mappings.append({"from": frm, "to": to})
            print(green(f"    Mapped: {frm} → {to}"))
    return mappings


def prompt_variable_mappings() -> list:
    mappings = []
    print()
    print(bold("Variable/Class name mappings"))
    print(dim("  Rename classes and variables with automatic handling of variations"))
    print(dim("  E.g., 'Ingredient' → 'Item' will handle: Ingredient, ingredient, ingredients,"))
    print(dim("        IngredientService, INGREDIENT_TYPE, ingredient_service, ingredient-row, etc."))
    print(dim("  Press Enter with an empty 'from' to finish.\n"))
    while True:
        frm = input("  Original name (from): ").strip()
        if not frm:
            break
        to = input("  New name      (to) : ").strip()
        if to:
            # Show user what variations will be created
            from_vars = generate_name_variations(frm)
            mappings.append({"from": frm, "to": to})
            print(green(f"    Mapped: {frm} → {to}"))
            print(dim(f"      From variations: {', '.join(from_vars[:5])}{'...' if len(from_vars) > 5 else ''}"))
    return mappings


def prompt_options(lang: dict) -> dict:
    print()
    print(bold("Sanitization options"))
    def ask(label, default=True):
        yn = "Y/n" if default else "y/N"
        ans = input(f"  {label} [{yn}]: ").strip().lower()
        return (ans != "n") if default else (ans == "y")

    return {
        "strip_comments": ask("Strip all comments"),
        "strip_javadoc":  ask(f"Remove @author / @since {lang['doc_tag_label']} tags"),
        "mask_strings":   ask("Mask string literals", default=True),
        "strip_loggers":  ask(f"Remove {lang['logger_label']}"),
    }


def prompt_test_framework(lang: dict) -> str:
    if lang["key"] != "react":
        return None
    print()
    print(bold("Test framework"))
    print("  1. Jest + React Testing Library")
    print("  2. Vitest + React Testing Library")
    choice = input("  Choice [1/2]: ").strip()
    return "vitest" if choice == "2" else "jest"


def interactive_trace():
    lang_key = prompt_language()
    lang = LANGUAGES[lang_key]

    print()
    entry_str = input(bold(f"  Path to entry file ({lang['entry_hint']}): ")).strip().strip('"')
    entry_path = Path(entry_str)
    if not entry_path.exists():
        print(red(f"  File not found: {entry_path}"))
        return

    if lang_key == "spring":
        scope = input(bold("  Base package (e.g. com.mycompany.project): ")).strip()
    else:
        scope = input(bold("  Path alias for the src root [@]: ")).strip() or "@"

    # Try to auto-detect src root
    src_guess = ""
    for marker in lang["src_markers"]:
        idx = str(entry_path).find(marker)
        if idx != -1:
            src_guess = str(entry_path)[: idx + len(marker)].rstrip("/\\")
            break

    src_str = input(bold(f"  Source root [{src_guess or lang['default_src']}]: ")).strip().strip('"')
    src_root = Path(src_str or src_guess or lang["default_src"])

    out_str = input(bold("  Output directory [./extracted]: ")).strip().strip('"')
    out_dir = Path(out_str or "./extracted")

    # Mapping file
    default_map = out_dir / "mapping.json"
    map_str = input(bold(f"  Mapping file [{default_map}] (leave blank to define now): ")).strip().strip('"')
    mapping_file = Path(map_str) if map_str else None

    if mapping_file and mapping_file.exists():
        mapping_dict = load_mappings(mapping_file)
        pkg_count = len(mapping_dict.get("package", []))
        var_count = len(mapping_dict.get("variable", []))
        print(green(f"  Loaded {pkg_count} package mapping(s) and {var_count} variable mapping(s) from {mapping_file}"))
    else:
        pkg_mappings = prompt_mappings(lang)
        var_mappings = prompt_variable_mappings()
        mapping_dict = {"package": pkg_mappings, "variable": var_mappings}

    for warning in validate_mappings(mapping_dict):
        print(yellow(f"  [!] {warning}"))

    options = prompt_options(lang)
    test_framework = prompt_test_framework(lang)

    print()
    print(bold("Tracing dependencies..."))
    deps = trace(entry_path, scope, src_root, lang)
    print(green(f"  Found {len(deps)} module(s)."))

    print_checklist(deps, lang)

    print()
    confirm = input(bold("  Write sanitized files to output directory? [Y/n]: ")).strip().lower()
    if confirm == "n":
        print(yellow("  Aborted — no files written."))
        return

    run_extraction(deps, mapping_dict, options, out_dir, lang, lang_key, test_framework)

    print()
    print(bold("═══ Done ═══"))
    print(f"  Extracted files : {out_dir}")
    print(f"  Mapping file    : {out_dir / 'mapping.json'}")
    print(f"  Claude prompt   : {out_dir / 'CLAUDE_PROMPT.txt'}")
    print()
    print(dim("  Next steps:"))
    print(dim("  1. Paste each extracted file + CLAUDE_PROMPT.txt into Claude"))
    print(dim("  2. Save the generated tests to ./generated-tests/"))
    print(dim("  3. Run:  python code_extractor.py reverse --mapping mapping.json --dir ./generated-tests"))


def interactive_reverse():
    print()
    map_str = input(bold("  Path to mapping.json: ")).strip().strip('"')
    mapping_file = Path(map_str)
    if not mapping_file.exists():
        print(red(f"  File not found: {mapping_file}"))
        return

    dir_str = input(bold("  Directory containing generated test files: ")).strip().strip('"')
    target_dir = Path(dir_str)
    if not target_dir.exists():
        print(red(f"  Directory not found: {target_dir}"))
        return

    mapping_dict = load_mappings(mapping_file)
    lang = LANGUAGES[get_language(mapping_dict)]
    pkg_count = len(mapping_dict.get("package", []))
    var_count = len(mapping_dict.get("variable", []))
    print(green(f"  Loaded {pkg_count} package mapping(s) and {var_count} variable mapping(s)"))
    print(green(f"  Language: {lang['label']}"))
    print()
    print(bold("Reversing mappings..."))
    apply_reversal(target_dir, mapping_dict, lang)


def interactive_menu():
    print()
    print(bold("╔═════════════════════════════════════════════════╗"))
    print(bold("║ Code Extractor & Sanitizer (Java/React/Angular) ║"))
    print(bold("╚═════════════════════════════════════════════════╝"))
    print()
    print("  1. Trace dependencies & extract sanitized files")
    print("  2. Reverse sanitization on generated test files")
    print("  3. Exit")
    print()
    choice = input("  Choice [1/2/3]: ").strip()
    if choice == "1":
        interactive_trace()
    elif choice == "2":
        interactive_reverse()
    else:
        print("Bye.")


# ─────────────────────────────────────────────
#  CLI entry point
# ─────────────────────────────────────────────

def parse_inline_mappings(pairs: list | None, flag: str) -> list:
    """Parse repeated FROM=TO CLI values into mapping dicts (split on the first '=')."""
    mappings = []
    for pair in pairs or []:
        frm, sep, to = pair.partition("=")
        if not sep or not frm or not to:
            print(red(f"Invalid {flag} value: '{pair}' — expected FROM=TO"))
            sys.exit(1)
        mappings.append({"from": frm, "to": to})
    return mappings


def cmd_trace(args):
    entry_path = Path(args.entry)
    if not entry_path.exists():
        print(red(f"Entry file not found: {entry_path}"))
        sys.exit(1)

    lang_key = args.lang or infer_language(args.entry)
    lang = LANGUAGES[lang_key]

    if lang_key == "spring" and not args.base:
        print(red("--base is required for Spring Boot (Java) projects"))
        sys.exit(1)
    scope = args.base if lang_key == "spring" else (args.alias or "@")

    src_root = Path(args.src) if args.src else Path(lang["default_src"])
    out_dir  = Path(args.out)

    mapping_dict = {"package": [], "variable": [], "strings": {}}
    if args.mapping:
        mapping_path = Path(args.mapping)
        if not mapping_path.exists():
            # Silently continuing would write UNSANITIZED output on a path typo.
            print(red(f"Mapping file not found: {mapping_path}"))
            sys.exit(1)
        mapping_dict = load_mappings(mapping_path)
        pkg_count = len(mapping_dict.get("package", []))
        var_count = len(mapping_dict.get("variable", []))
        print(green(f"Loaded {pkg_count} package mapping(s) and {var_count} variable mapping(s) from {args.mapping}"))

    mapping_dict["package"] += parse_inline_mappings(args.map_package, "--map-package")
    mapping_dict["variable"] += parse_inline_mappings(args.map_var, "--map-var")

    for warning in validate_mappings(mapping_dict):
        print(yellow(f"[!] {warning}"))

    if args.strip_javadoc or args.strip_loggers:
        print(yellow("[!] --strip-javadoc and --strip-loggers are deprecated no-ops: "
                     "doc tags and loggers are now stripped by default "
                     "(use --keep-doc-tags / --keep-loggers to keep them)."))
    if args.mask_strings:
        print(yellow("[!] --mask-strings is a deprecated no-op: string masking is now "
                     "ON by default (use --no-mask-strings to disable it)."))

    options = {
        "strip_comments": not args.keep_comments,
        "strip_javadoc":  not args.keep_doc_tags,
        "mask_strings":   not args.no_mask_strings,
        "strip_loggers":  not args.keep_loggers,
    }

    print(bold(f"Language: {lang['label']}"))
    print(bold(f"Tracing from: {entry_path}"))
    deps = trace(entry_path, scope, src_root, lang)
    print(green(f"Found {len(deps)} module(s)."))

    print_checklist(deps, lang)

    run_extraction(deps, mapping_dict, options, out_dir, lang, lang_key,
                   args.test_framework, dry_run=args.dry_run)


def cmd_reverse(args):
    mapping_file = Path(args.mapping)
    target_dir   = Path(args.dir)

    if not mapping_file.exists():
        print(red(f"Mapping file not found: {mapping_file}"))
        sys.exit(1)
    if not target_dir.exists():
        print(red(f"Target directory not found: {target_dir}"))
        sys.exit(1)

    mapping_dict = load_mappings(mapping_file)
    lang = LANGUAGES[get_language(mapping_dict, args.lang)]
    pkg_count = len(mapping_dict.get("package", []))
    var_count = len(mapping_dict.get("variable", []))
    str_count = len(mapping_dict.get("strings", {}))
    print(green(f"Loaded {pkg_count} package mapping(s), {var_count} variable mapping(s), "
                f"{str_count} masked string(s)"))
    print(green(f"Language: {lang['label']}"))
    result = apply_reversal(target_dir, mapping_dict, lang,
                            dry_run=args.dry_run, backup=not args.no_backup, force=args.force)
    if result["refused"]:
        sys.exit(1)


def _load_audit_context(args) -> tuple:
    """(out_dir, mapping_dict, lang) shared by the audit/approve commands."""
    out_dir = Path(args.dir)
    if not out_dir.is_dir():
        print(red(f"Output directory not found: {out_dir}"))
        sys.exit(1)
    mapping_path = Path(args.mapping) if args.mapping else out_dir / "mapping.json"
    if mapping_path.exists():
        mapping_dict = load_mappings(mapping_path)
    elif args.mapping:
        print(red(f"Mapping file not found: {mapping_path}"))
        sys.exit(1)
    else:
        print(yellow("[!] No mapping.json found — auditing without mapping context "
                     "(fixed-point verification and identifier cross-reference degraded)."))
        mapping_dict = {"package": [], "variable": [], "strings": {}}
    lang = LANGUAGES[get_language(mapping_dict, getattr(args, "lang", None))]
    return out_dir, mapping_dict, lang


def cmd_audit(args):
    """Exit 0 = approved & clean, 1 = awaiting approval, 2 = blocking findings."""
    out_dir, mapping_dict, lang = _load_audit_context(args)
    audit = audit_output(out_dir, mapping_dict, lang)
    report = write_audit_report(audit, out_dir)
    print(green(f"  Audit report → {report}"))
    state = approval_state(audit, out_dir)
    print_audit_summary(audit, out_dir, state)

    sys.exit({"approved": 0, "unapproved": 1, "stale": 1, "blocked": 2}[state])


def cmd_approve(args):
    out_dir, mapping_dict, lang = _load_audit_context(args)
    audit = audit_output(out_dir, mapping_dict, lang)

    if audit["counts"]["block"]:
        print_audit_summary(audit, out_dir)
        print(red(bold("Refusing to approve: blocking findings present.")))
        sys.exit(2)

    expected = args.hash.strip().lower()
    if len(expected) < 12 or not audit["audit_hash"].startswith(expected):
        print(red("Hash mismatch: the reviewed report does not describe the current output."))
        print(red(f"  current hash: {audit['audit_hash'][:12]}   given: {expected or '(none)'}"))
        print(red("  Re-run `audit`, review the fresh report, and approve its hash."))
        sys.exit(1)

    marker = {
        "approved_at": datetime.now().isoformat(timespec="seconds"),
        "audit_hash": audit["audit_hash"],
        "findings": audit["counts"],
    }
    (out_dir / APPROVAL_MARKER).write_text(json.dumps(marker, indent=2), encoding="utf-8")
    print(green(bold(f"✓ Approved for transfer (hash {audit['audit_hash'][:12]}).")))
    print(green(f"  Marker → {out_dir / APPROVAL_MARKER}"))
    print(yellow(f"  Remember: {', '.join(DO_NOT_TRANSFER)} stay inside the environment."))


def main():
    if sys.version_info < (3, 9):
        sys.exit("code_extractor.py requires Python 3.9+")
    if len(sys.argv) == 1:
        interactive_menu()
        return

    parser = argparse.ArgumentParser(
        description="Code extractor and sanitizer (Spring Boot Java / React / Angular)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive mode
  python code_extractor.py

  # Trace and extract (Spring Boot — inferred from the .java entry file)
  python code_extractor.py trace \\
    --entry src/main/java/com/myco/OrderService.java \\
    --base com.myco \\
    --src src/main/java \\
    --out ./extracted \\
    --mapping mapping.json

  # Trace and extract (React — inferred from the .tsx entry file)
  python code_extractor.py trace \\
    --entry src/components/OrderList.tsx \\
    --src src \\
    --out ./extracted \\
    --test-framework vitest

  # Trace and extract (Angular — inferred from conventional Angular suffixes)
  python code_extractor.py trace \\
    --entry src/app/orders/order-list.component.ts \\
    --src src \\
    --out ./extracted

  # Reverse sanitization on generated tests (language read from mapping.json)
  python code_extractor.py reverse \\
    --mapping ./extracted/mapping.json \\
    --dir ./generated-tests

  # Re-run the residual-content audit over extracted output
  python code_extractor.py audit --dir ./extracted

  # Approve the reviewed output for transfer (hash from TRANSFER_AUDIT.txt)
  python code_extractor.py approve --dir ./extracted --hash <audit-hash-prefix>
""")

    sub = parser.add_subparsers(dest="command")

    t = sub.add_parser("trace", help="Trace dependencies and extract sanitized files")
    t.add_argument("--entry",          required=True, help="Path to the entry source file")
    t.add_argument("--lang",           choices=list(LANGUAGES), default=None,
                   help="Project language (default: inferred from --entry filename)")
    t.add_argument("--base",           default=None, help="[spring] Base package to trace within (e.g. com.mycompany)")
    t.add_argument("--alias",          default="@",  help="[react/angular] Path alias that maps to the src root (default: @)")
    t.add_argument("--src",            default=None, help="Source root directory (default: src/main/java for spring, src for react/angular)")
    t.add_argument("--out",            default="./extracted",   help="Output directory for sanitized files")
    t.add_argument("--mapping",        default=None,            help="Path to mapping.json (optional)")
    t.add_argument("--test-framework", choices=["jest", "vitest"], default="jest",
                   help="[react] Test framework for the generated prompt (default: jest)")
    t.add_argument("--map-package",    action="append", metavar="FROM=TO",
                   help="Add a package/path mapping inline (repeatable)")
    t.add_argument("--map-var",        action="append", metavar="FROM=TO",
                   help="Add a variable/class mapping inline (repeatable)")
    t.add_argument("--dry-run",        action="store_true",
                   help="Show what would be written without writing anything")
    t.add_argument("--keep-comments",   action="store_true",    help="Do not strip comments")
    t.add_argument("--keep-doc-tags",   action="store_true",    help="Do not strip @author/@since doc tags")
    t.add_argument("--keep-loggers",    action="store_true",    help="Do not strip logger statements")
    t.add_argument("--no-mask-strings", action="store_true",
                   help="Do not mask string literals (masking is ON by default; "
                        "originals are recorded in mapping.json for reversal)")
    t.add_argument("--mask-strings",   action="store_true",     help=argparse.SUPPRESS)  # deprecated no-op (now the default)
    t.add_argument("--strip-javadoc",  action="store_true",     help=argparse.SUPPRESS)  # deprecated no-op (now the default)
    t.add_argument("--strip-loggers",  action="store_true",     help=argparse.SUPPRESS)  # deprecated no-op (now the default)

    r = sub.add_parser("reverse", help="Reverse sanitization mappings on generated test files")
    r.add_argument("--mapping", required=True, help="Path to mapping.json")
    r.add_argument("--dir",     required=True, help="Directory containing generated test files")
    r.add_argument("--lang",    choices=list(LANGUAGES), default=None,
                   help="Override language (default: read from mapping.json)")
    r.add_argument("--dry-run",   action="store_true", help="Preview changes without writing anything")
    r.add_argument("--no-backup", action="store_true", help="Skip the automatic backup of the target directory")
    r.add_argument("--force",     action="store_true", help="Re-run even if this directory was already reversed with this mapping")

    a = sub.add_parser("audit", help="Audit extracted output for residual sensitive content")
    a.add_argument("--dir",     required=True, help="Extraction output directory to audit")
    a.add_argument("--mapping", default=None,  help="Path to mapping.json (default: <dir>/mapping.json)")
    a.add_argument("--lang",    choices=list(LANGUAGES), default=None,
                   help="Override language (default: read from mapping.json)")

    p = sub.add_parser("approve", help="Approve audited output for transfer")
    p.add_argument("--dir",     required=True, help="Extraction output directory to approve")
    p.add_argument("--hash",    required=True,
                   help="Audit hash (≥12 chars) from the reviewed TRANSFER_AUDIT report")
    p.add_argument("--mapping", default=None,  help="Path to mapping.json (default: <dir>/mapping.json)")
    p.add_argument("--lang",    choices=list(LANGUAGES), default=None,
                   help="Override language (default: read from mapping.json)")

    args = parser.parse_args()
    if args.command == "trace":
        cmd_trace(args)
    elif args.command == "reverse":
        cmd_reverse(args)
    elif args.command == "audit":
        cmd_audit(args)
    elif args.command == "approve":
        cmd_approve(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
