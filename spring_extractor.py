#!/usr/bin/env python3
"""
Spring Boot Code Extractor & Sanitizer
---------------------------------------
Traces dependencies from an entry class, sanitizes sensitive package names,
and produces a reversal script to undo mappings on generated test files.

Usage:
  python spring_extractor.py trace   --entry path/to/MyService.java --base com.mycompany --src src/main/java --out ./extracted
  python spring_extractor.py reverse --mapping mapping.json --dir ./generated-tests
  python spring_extractor.py         (interactive menu)

Requirements: Python 3.7+, no third-party packages needed.
"""

import os
import re
import sys
import json
import argparse
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
#  Import / class parsing
# ─────────────────────────────────────────────

IMPORT_RE   = re.compile(r"^import\s+(?:static\s+)?([a-zA-Z0-9_.]+);", re.MULTILINE)
PACKAGE_RE  = re.compile(r"^package\s+([a-zA-Z0-9_.]+);", re.MULTILINE)
CLASS_RE    = re.compile(r"(?:public\s+)?(?:class|interface|enum|@interface)\s+(\w+)")


def parse_imports(source: str, base_package: str) -> list[str]:
    """Return fully-qualified class names imported from within base_package."""
    found = []
    for m in IMPORT_RE.finditer(source):
        fqn = m.group(1)
        if fqn.startswith(base_package):
            parts = fqn.split(".")
            class_name = parts[-1]
            if class_name != "*" and class_name[0].isupper():
                found.append(fqn)
    return found


def fqn_to_relative_path(fqn: str) -> str:
    return fqn.replace(".", os.sep) + ".java"


def classify(fqn: str) -> str:
    lower = fqn.lower()
    if any(k in lower for k in ("repository", "repo", "dao")):    return "repository"
    if any(k in lower for k in ("service",)):                      return "service"
    if any(k in lower for k in ("controller", "rest", "resource")): return "controller"
    if any(k in lower for k in ("enum",)):                         return "enum"
    if any(k in lower for k in ("util", "helper", "config", "configuration")): return "util"
    return "model"


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


def to_pascal_case(name: str) -> str:
    """Convert snake_case or other to PascalCase (ingredient → Ingredient, ingredient_service → IngredientService)"""
    # Handle snake_case
    if "_" in name:
        return "".join(w.capitalize() for w in name.split("_"))
    # Already PascalCase or needs first letter capitalized
    return name[0].upper() + name[1:] if name else name


def to_camel_case(name: str) -> str:
    """Convert to camelCase (Ingredient → ingredient, IngredientService → ingredientService)"""
    pascal = to_pascal_case(name)
    return pascal[0].lower() + pascal[1:] if pascal else pascal


def pluralize(word: str) -> str:
    """Simple English pluralization"""
    # Common irregular plurals
    irregulars = {"ingredient": "ingredients", "service": "services", "type": "types"}
    lower = word.lower()
    if lower in irregulars:
        # Preserve case pattern
        if word.isupper():
            return irregulars[lower].upper()
        elif word[0].isupper():
            return irregulars[lower].capitalize()
        else:
            return irregulars[lower]
    
    # Simple rules
    if lower.endswith(("s", "ss", "x", "z", "ch", "sh")):
        return word + "es"
    elif lower.endswith("y") and len(word) > 1 and word[-2].lower() not in "aeiou":
        return word[:-1] + "ies"
    else:
        return word + "s"


def singularize(word: str) -> str:
    """Simple English singularization"""
    # Common irregulars
    irregulars = {"ingredients": "ingredient", "services": "service", "types": "type"}
    lower = word.lower()
    if lower in irregulars:
        # Preserve case pattern
        if word.isupper():
            return irregulars[lower].upper()
        elif word[0].isupper():
            return irregulars[lower].capitalize()
        else:
            return irregulars[lower]
    
    # Simple rules
    if lower.endswith("ies") and len(word) > 3 and word[-4].lower() not in "aeiou":
        return word[:-3] + "y"
    elif lower.endswith("es") and len(word) > 2 and word[-3:-2].lower() in "sxz" or word[-3:].lower() in ("ch", "sh"):
        return word[:-2]
    elif lower.endswith("s") and len(word) > 1 and not lower.endswith("ss"):
        return word[:-1]
    else:
        return word


def generate_name_variations(base_name: str) -> list[str]:
    """
    Generate common variations of a class/variable name.
    E.g., 'Ingredient' → ['Ingredient', 'ingredient', 'ingredients', 'INGREDIENT', 'INGREDIENTS']
    """
    variations = set()
    
    # PascalCase forms
    pascal = to_pascal_case(base_name)
    variations.add(pascal)
    variations.add(pluralize(pascal))
    variations.add(singularize(pascal))
    
    # camelCase forms
    camel = to_camel_case(base_name)
    if camel and camel != pascal:
        variations.add(camel)
        variations.add(pluralize(camel))
        variations.add(singularize(camel))
    
    # UPPER_SNAKE_CASE forms
    upper_snake = to_upper_snake_case(base_name)
    variations.add(upper_snake)
    variations.add(pluralize(upper_snake))
    variations.add(singularize(upper_snake))
    
    # snake_case forms
    snake = to_snake_case(base_name)
    if snake != base_name and snake != camel.lower():
        variations.add(snake)
        variations.add(pluralize(snake))
        variations.add(singularize(snake))
    
    # Remove empty strings and the base name itself if it's just a case variant
    variations.discard("")
    
    return sorted(list(variations))


def apply_variable_mappings(source: str, var_mappings: list[dict]) -> str:
    """
    Apply variable/class name mappings with word boundary awareness.
    Handles both standalone names and compound names (e.g., IngredientService, INGREDIENT_TYPE).
    Preserves plural/singular forms during mapping.
    """
    for mapping in var_mappings:
        from_name = mapping.get("from", "")
        to_name = mapping.get("to", "")
        if not from_name or not to_name:
            continue
        
        # Generate all variations of the 'from' name
        from_variations = generate_name_variations(from_name)
        
        # Process each variation
        for from_var in from_variations:
            # Determine the correct form for the 'to' name based on the 'from' form
            # Check if the from_var is plural
            singular_from = singularize(from_var)
            is_plural = (singular_from != from_var)
            
            if from_var.isupper():
                # UPPER_SNAKE_CASE or CONSTANT_CASE
                to_var = to_upper_snake_case(to_name)
                if is_plural:
                    to_var = pluralize(to_var)
            elif "_" in from_var and not from_var[0].isupper():
                # snake_case
                to_var = to_snake_case(to_name)
                if is_plural:
                    to_var = pluralize(to_var)
            elif from_var[0].isupper():
                # PascalCase
                to_var = to_pascal_case(to_name)
                if is_plural:
                    to_var = pluralize(to_var)
            else:
                # camelCase
                to_var = to_camel_case(to_name)
                if is_plural:
                    to_var = pluralize(to_var)
            
            # For UPPER_SNAKE_CASE variations, also match them as prefix in compound names
            # e.g., INGREDIENT in INGREDIENT_TYPE
            if from_var.isupper() and "_" not in from_var:
                # Pattern: INGREDIENT followed by underscore and more letters
                pattern = re.escape(from_var) + r"(?=_[A-Z])"
                source = re.sub(pattern, to_var, source)
            
            # For standalone replacements: use word boundaries
            # Don't include underscore in the negative lookahead for consistency with compound names
            pattern = r"(?<![a-zA-Z0-9_])" + re.escape(from_var) + r"(?![a-zA-Z0-9])"
            source = re.sub(pattern, to_var, source)
            
            # For compound names in PascalCase (e.g., IngredientService → ItemService)
            if not "_" in from_var and from_var[0].isupper() and len(from_var) > 1:
                # Match patterns where the word is part of a larger identifier
                pattern_compound = re.escape(from_var) + r"(?=[A-Z])"  # Before uppercase: IngredientService → ItemService
                source = re.sub(pattern_compound, to_var, source)
                pattern_compound = r"(?<=[a-z])" + re.escape(from_var)  # After lowercase: myIngredient → myItem
                source = re.sub(pattern_compound, to_var, source)

            # For lower camelCase prefixes (e.g., ingredientId → itemId)
            if from_var and from_var[0].islower() and len(from_var) > 1:
                pattern_compound = re.escape(from_var) + r"(?=[A-Z])"
                source = re.sub(pattern_compound, to_var, source)
    
    return source


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


# ─────────────────────────────────────────────
#  Recursive dependency tracer
# ─────────────────────────────────────────────

def trace(entry_path: Path, base_package: str, src_root: Path) -> dict:
    """
    BFS from entry_path, following imports within base_package.
    Returns {fqn: {"path": Path|None, "type": str, "source": str}} 
    """
    visited = {}
    queue   = [entry_path]

    while queue:
        current = queue.pop(0)
        try:
            source = current.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            print(red(f"  [!] Cannot read {current}: {e}"))
            continue

        pkg_m   = PACKAGE_RE.search(source)
        cls_m   = CLASS_RE.search(source)
        pkg     = pkg_m.group(1) if pkg_m else ""
        cls     = cls_m.group(1) if cls_m else current.stem
        fqn     = f"{pkg}.{cls}" if pkg else cls

        if fqn in visited:
            continue

        visited[fqn] = {
            "path":   current,
            "type":   classify(fqn),
            "source": source,
        }

        for dep_fqn in parse_imports(source, base_package):
            if dep_fqn not in visited:
                dep_path = find_source_file(dep_fqn, src_root)
                if dep_path:
                    queue.append(dep_path)
                else:
                    visited[dep_fqn] = {
                        "path":   None,
                        "type":   classify(dep_fqn),
                        "source": "",
                    }

    return visited


# ─────────────────────────────────────────────
#  Sanitization
# ─────────────────────────────────────────────

def load_mappings(mapping_file: Path) -> dict:
    """Load mappings from file. Returns {'package': [...], 'variable': [...]}"""
    with open(mapping_file, encoding="utf-8") as f:
        data = json.load(f)
    # Support both old format (list) and new format (dict with 'package' and 'variable')
    if isinstance(data, list):
        return {"package": data, "variable": []}
    return data


def save_mappings(mappings: dict, mapping_file: Path):
    """Save mappings to file with structure: {'package': [...], 'variable': [...]}"""
    with open(mapping_file, "w", encoding="utf-8") as f:
        json.dump(mappings, f, indent=2)
    print(green(f"  Mappings saved → {mapping_file}"))


def apply_mappings(source: str, mappings: list[dict]) -> str:
    """Legacy: apply package mappings only (for backward compatibility)"""
    for m in mappings:
        frm, to = m.get("from", ""), m.get("to", "")
        if frm and to:
            source = source.replace(frm, to)
    return source


def apply_all_mappings(source: str, mapping_dict: dict) -> str:
    """Apply both package and variable mappings to source code"""
    # Apply package mappings first
    pkg_mappings = mapping_dict.get("package", [])
    source = apply_mappings(source, pkg_mappings)
    
    # Then apply variable/class mappings
    var_mappings = mapping_dict.get("variable", [])
    source = apply_variable_mappings(source, var_mappings)
    
    return source


def apply_path_mappings(path_value: Path | str, mapping_dict: dict) -> Path:
    """Apply package and variable mappings to a path string or Path."""
    path_text = str(path_value)

    for mapping in mapping_dict.get("package", []):
        frm = mapping.get("from", "")
        to = mapping.get("to", "")
        if frm and to:
            path_text = path_text.replace(frm.replace(".", os.sep), to.replace(".", os.sep))

    path_text = apply_variable_mappings(path_text, mapping_dict.get("variable", []))
    return Path(path_text)


def rename_java_file_stem(file_path: Path, var_mappings: list[dict]) -> Path:
    """Return the same path with its .java stem renamed via variable mappings."""
    if not var_mappings:
        return file_path

    new_stem = apply_variable_mappings(file_path.stem, var_mappings)
    if new_stem == file_path.stem:
        return file_path
    return file_path.with_name(new_stem + file_path.suffix)


def rename_java_path(file_path: Path, mapping_dict: dict) -> Path:
    """Rename a .java file path, including parent folders, via path mappings."""
    renamed_path = apply_path_mappings(file_path, mapping_dict)
    if renamed_path.name == file_path.name and renamed_path.parent == file_path.parent:
        return file_path
    return renamed_path


def strip_comments(source: str) -> str:
    # Block comments (including Javadoc)
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    # Line comments
    source = re.sub(r"//[^\n]*", "", source)
    # Collapse excessive blank lines
    source = re.sub(r"\n{3,}", "\n\n", source)
    return source


def strip_javadoc_tags(source: str) -> str:
    return re.sub(r"@(author|since|version|see)\b[^\n]*", "", source, flags=re.IGNORECASE)


def mask_strings(source: str) -> str:
    counter = [0]
    def replacer(m):
        idx = counter[0]
        counter[0] += 1
        return f'"STR_{idx}"'
    return re.sub(r'"([^"\\]|\\.)*"', replacer, source)


def strip_loggers(source: str) -> str:
    return re.sub(r"[ \t]*log\.(debug|info|warn|error|trace)\([^;]+\);\n?", "\n", source)


def sanitize(source: str, mapping_dict: dict, options: dict) -> str:
    source = apply_all_mappings(source, mapping_dict)
    if options.get("strip_comments"):   source = strip_comments(source)
    if options.get("strip_javadoc"):    source = strip_javadoc_tags(source)
    if options.get("mask_strings"):     source = mask_strings(source)
    if options.get("strip_loggers"):    source = strip_loggers(source)
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


def apply_reversal(target_dir: Path, mapping_dict: dict):
    """Apply reversed mappings to all .java files in target directory."""
    reversed_maps = reverse_mappings(mapping_dict)
    java_files = sorted(target_dir.rglob("*.java"), key=lambda path: len(path.parts), reverse=True)
    if not java_files:
        print(yellow("  No .java files found in target directory."))
        return

    renamed = 0
    for jf in java_files:
        renamed_path = rename_java_path(jf, reversed_maps)
        if renamed_path != jf:
            if renamed_path.exists():
                print(yellow(f"  [skip] file rename collision: {jf.name} -> {renamed_path.name}"))
                continue
            renamed_path.parent.mkdir(parents=True, exist_ok=True)
            jf.rename(renamed_path)
            renamed += 1

    changed = 0
    for jf in sorted(target_dir.rglob("*.java"), key=lambda path: len(path.parts), reverse=True):
        original = jf.read_text(encoding="utf-8", errors="replace")
        result   = apply_all_mappings(original, reversed_maps)
        if result != original:
            jf.write_text(result, encoding="utf-8")
            changed += 1
    print(green(f"  Reversal complete — {renamed} file(s) renamed, {changed}/{len(java_files)} files updated."))


# ─────────────────────────────────────────────
#  Output helpers
# ─────────────────────────────────────────────

TYPE_ORDER  = ["controller", "service", "model", "repository", "util", "enum"]
TYPE_LABELS = {
    "controller":  "Controllers / entry points",
    "service":     "Services",
    "model":       "Domain models",
    "repository":  "Repository interfaces (interface only — no impl)",
    "util":        "Utilities / helpers",
    "enum":        "Enums / constants",
}


def print_checklist(deps: dict):
    grouped = {}
    for fqn, info in deps.items():
        t = info["type"]
        grouped.setdefault(t, []).append((fqn, info["path"]))

    print()
    print(bold("═══ Extraction checklist ═══"))
    for t in TYPE_ORDER:
        if t not in grouped:
            continue
        print(f"\n{cyan(TYPE_LABELS.get(t, t))}")
        for fqn, path in grouped[t]:
            status = green("✓ found") if path else red("✗ not found")
            rel    = str(path) if path else fqn_to_relative_path(fqn)
            print(f"  [ ] {rel}  {dim(status)}")

    missing = [(fqn, i) for fqn, i in deps.items() if i["path"] is None]
    if missing:
        print()
        print(yellow(f"  {len(missing)} file(s) could not be located automatically."))
        print(yellow("  Locate them manually or adjust --src."))


def write_extracted(deps: dict, mapping_dict: dict, options: dict, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    pkg_mappings = mapping_dict.get("package", [])
    var_mappings = mapping_dict.get("variable", [])
    for fqn, info in deps.items():
        if info["path"] is None:
            print(yellow(f"  [skip] {fqn} — source not found"))
            continue

        sanitized = sanitize(info["source"], mapping_dict, options)

        # Reconstruct path using sanitized package name
        sanitized_fqn = fqn
        for m in pkg_mappings:
            if m.get("from") and m.get("to"):
                sanitized_fqn = sanitized_fqn.replace(m["from"], m["to"])

        pkg_name, sep, class_name = sanitized_fqn.rpartition(".")
        if sep:
            class_name = apply_variable_mappings(class_name, var_mappings)
            sanitized_fqn = f"{pkg_name}.{class_name}"
        else:
            sanitized_fqn = apply_variable_mappings(sanitized_fqn, var_mappings)

        rel_path = apply_path_mappings(Path(fqn_to_relative_path(sanitized_fqn)), mapping_dict)
        dest     = out_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(sanitized, encoding="utf-8")
        written += 1
        print(green(f"  ✓ {rel_path}"))

    print()
    print(bold(f"  {written} file(s) written to: {out_dir}"))


def write_claude_prompt(deps: dict, out_dir: Path):
    dep_classes = [fqn.split(".")[-1] for fqn, i in deps.items() if i["type"] != "controller"]
    prompt = f"""I have a Spring Boot service I need unit tests for.

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
    prompt_file = out_dir / "CLAUDE_PROMPT.txt"
    prompt_file.write_text(prompt, encoding="utf-8")
    print(green(f"  Prompt template → {prompt_file}"))


def write_reversal_script(mapping_dict: dict, out_dir: Path):
    """Write a platform-aware reversal script for both package and variable mappings."""
    reversed_maps = reverse_mappings(mapping_dict)
    pkg_reversed = reversed_maps.get("package", [])
    var_reversed = reversed_maps.get("variable", [])

    # Bash version
    bash_lines = [
        "#!/usr/bin/env bash",
        "# Reverse sanitization mappings on generated test files",
        "",
        "rename_files() {",
        "  while IFS= read -r -d '' file; do",
        "    new_path=\"$file\"",
    ]

    for m in pkg_reversed:
        frm = m["from"].replace(".", "/")
        to = m["to"].replace(".", "/")
        bash_lines.append(f'    new_path=$(printf %s "$new_path" | sed "s|{frm}|{to}|g")')

    if var_reversed:
        for m in var_reversed:
            bash_lines.append(f'    new_path=$(printf %s "$new_path" | sed "s|{m["from"]}|{m["to"]}|g")')

    bash_lines.extend([
        "    if [[ \"$new_path\" != \"$file\" ]]; then",
        "      mkdir -p \"$(dirname \"$new_path\")\"",
        "      mv \"$file\" \"$new_path\"",
        "    fi",
        "  done < <(find . -name \"*.java\" -print0)",
        "}",
        "",
        "rename_files",
        "",
        'FILES=$(find . -name "*.java")',
        "",
    ])
    
    if pkg_reversed:
        bash_lines.append("# Package mappings")
        for m in pkg_reversed:
            frm = m["from"].replace(".", r"\.").replace("/", r"\/")
            to  = m["to"].replace(".", r"\.").replace("/", r"\/")
            bash_lines.append(f'# {m["from"]} → {m["to"]}')
            bash_lines.append(f"sed -i 's/{frm}/{to}/g' $FILES")
        bash_lines.append("")
    
    if var_reversed:
        bash_lines.append("# Variable/class name mappings")
        for m in var_reversed:
            frm = m["from"].replace(".", r"\.").replace("/", r"\/")
            to  = m["to"].replace(".", r"\.").replace("/", r"\/")
            bash_lines.append(f'# {m["from"]} → {m["to"]}')
            bash_lines.append(f"sed -i 's/\\b{frm}\\b/{to}/g' $FILES")
        bash_lines.append("")

    bash_file = out_dir / "reverse_sanitize.sh"
    bash_file.write_text("\n".join(bash_lines), encoding="utf-8")
    bash_file.chmod(0o755)

    # PowerShell version
    ps_lines = ["# Reverse sanitization mappings on generated test files", '$files = Get-ChildItem -Recurse -Filter "*.java"', "foreach ($f in $files) {", "    $newPath = $f.FullName"]

    for m in pkg_reversed:
        ps_lines.append(f'    $newPath = $newPath -replace [regex]::Escape("{m["from"].replace(".", os.sep)}"), "{m["to"].replace(".", os.sep)}"')

    if var_reversed:
        for m in var_reversed:
            ps_lines.append(f'    $newPath = $newPath -replace [regex]::Escape("{m["from"]}"), "{m["to"]}"')
    ps_lines.extend([
        '    if ($newPath -ne $f.FullName) {',
        '        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $newPath) | Out-Null',
        '        Move-Item -LiteralPath $f.FullName -Destination $newPath',
        '    }',
        '}',
        '$files = Get-ChildItem -Recurse -Filter "*.java"',
        'foreach ($f in $files) {',
        '    $content = Get-Content $f.FullName -Raw'
    ])
    
    if pkg_reversed:
        ps_lines.append("    # Package mappings")
        for m in pkg_reversed:
            ps_lines.append(f'    # {m["from"]} → {m["to"]}')
            ps_lines.append(f'    $content = $content -replace [regex]::Escape("{m["from"]}"), "{m["to"]}"')
    
    if var_reversed:
        ps_lines.append("    # Variable/class name mappings")
        for m in var_reversed:
            from_pat = m["from"]
            to_rep = m["to"]
            ps_lines.append(f'    # {from_pat} → {to_rep}')
            # Use a variable to hold the escaped pattern for word boundary matching
            ps_lines.append(f'    $pattern = "\\b" + [regex]::Escape("{from_pat}") + "\\b"')
            ps_lines.append(f'    $content = $content -replace $pattern, "{to_rep}"')
    
    ps_lines += ["    Set-Content $f.FullName $content", "}"]

    ps_file = out_dir / "reverse_sanitize.ps1"
    ps_file.write_text("\n".join(ps_lines), encoding="utf-8")

    print(green(f"  Bash reversal script  → {bash_file}"))
    print(green(f"  PowerShell reversal   → {ps_file}"))


# ─────────────────────────────────────────────
#  Interactive menu
# ─────────────────────────────────────────────

def prompt_mappings() -> list[dict]:
    mappings = []
    print()
    print(bold("Package / token mappings"))
    print(dim("  Enter sensitive→safe substitutions (e.g. com.classified → com.example)"))
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


def prompt_variable_mappings() -> list[dict]:
    mappings = []
    print()
    print(bold("Variable/Class name mappings"))
    print(dim("  Rename classes and variables with automatic handling of variations"))
    print(dim("  E.g., 'Ingredient' → 'Item' will handle: Ingredient, ingredient, ingredients,"))
    print(dim("        IngredientService, INGREDIENT_TYPE, ingredient_service, etc."))
    print(dim("  Press Enter with an empty 'from' to finish.\n"))
    while True:
        frm = input("  Original name (from): ").strip()
        if not frm:
            break
        to = input("  New name      (to) : ").strip()
        if to:
            # Show user what variations will be created
            from_vars = generate_name_variations(frm)
            to_vars = generate_name_variations(to)
            mappings.append({"from": frm, "to": to})
            print(green(f"    Mapped: {frm} → {to}"))
            print(dim(f"      From variations: {', '.join(from_vars[:5])}{'...' if len(from_vars) > 5 else ''}"))
    return mappings


def prompt_options() -> dict:
    print()
    print(bold("Sanitization options"))
    def ask(label, default=True):
        yn = "Y/n" if default else "y/N"
        ans = input(f"  {label} [{yn}]: ").strip().lower()
        return (ans != "n") if default else (ans == "y")

    return {
        "strip_comments": ask("Strip all comments"),
        "strip_javadoc":  ask("Remove @author / @since Javadoc tags"),
        "mask_strings":   ask("Mask string literals", default=False),
        "strip_loggers":  ask("Remove logger statements"),
    }


def interactive_trace():
    print()
    entry_str = input(bold("  Path to entry class (.java): ")).strip().strip('"')
    entry_path = Path(entry_str)
    if not entry_path.exists():
        print(red(f"  File not found: {entry_path}"))
        return

    base = input(bold("  Base package (e.g. com.mycompany.project): ")).strip()

    # Try to auto-detect src root
    src_guess = ""
    for part in ["src/main/java", "src\\main\\java"]:
        idx = str(entry_path).find(part)
        if idx != -1:
            src_guess = str(entry_path)[: idx + len(part)]
            break

    src_str = input(bold(f"  Source root [{src_guess or 'src/main/java'}]: ")).strip().strip('"')
    src_root = Path(src_str or src_guess or "src/main/java")

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
        pkg_mappings = prompt_mappings()
        var_mappings = prompt_variable_mappings()
        mapping_dict = {"package": pkg_mappings, "variable": var_mappings}

    options = prompt_options()

    print()
    print(bold("Tracing dependencies..."))
    deps = trace(entry_path, base, src_root)
    print(green(f"  Found {len(deps)} class(es)."))

    print_checklist(deps)

    print()
    confirm = input(bold("  Write sanitized files to output directory? [Y/n]: ")).strip().lower()
    if confirm == "n":
        print(yellow("  Aborted — no files written."))
        return

    print()
    print(bold("Writing sanitized files..."))
    write_extracted(deps, mapping_dict, options, out_dir)
    write_claude_prompt(deps, out_dir)

    # Save mappings for reversal
    final_map = out_dir / "mapping.json"
    save_mappings(mapping_dict, final_map)
    write_reversal_script(mapping_dict, out_dir)

    print()
    print(bold("═══ Done ═══"))
    print(f"  Extracted files : {out_dir}")
    print(f"  Mapping file    : {final_map}")
    print(f"  Claude prompt   : {out_dir / 'CLAUDE_PROMPT.txt'}")
    print()
    print(dim("  Next steps:"))
    print(dim("  1. Paste each extracted file + CLAUDE_PROMPT.txt into Claude"))
    print(dim("  2. Save the generated tests to ./generated-tests/"))
    print(dim("  3. Run:  python spring_extractor.py reverse --mapping mapping.json --dir ./generated-tests"))


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
    pkg_count = len(mapping_dict.get("package", []))
    var_count = len(mapping_dict.get("variable", []))
    print(green(f"  Loaded {pkg_count} package mapping(s) and {var_count} variable mapping(s)"))
    print()
    print(bold("Reversing mappings..."))
    apply_reversal(target_dir, mapping_dict)


def interactive_menu():
    print()
    print(bold("╔══════════════════════════════════════════╗"))
    print(bold("║  Spring Boot Extractor & Sanitizer       ║"))
    print(bold("╚══════════════════════════════════════════╝"))
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

def cmd_trace(args):
    entry_path = Path(args.entry)
    if not entry_path.exists():
        print(red(f"Entry file not found: {entry_path}"))
        sys.exit(1)

    src_root = Path(args.src)
    out_dir  = Path(args.out)

    mapping_dict = {"package": [], "variable": []}
    if args.mapping and Path(args.mapping).exists():
        mapping_dict = load_mappings(Path(args.mapping))
        pkg_count = len(mapping_dict.get("package", []))
        var_count = len(mapping_dict.get("variable", []))
        print(green(f"Loaded {pkg_count} package mapping(s) and {var_count} variable mapping(s) from {args.mapping}"))

    options = {
        "strip_comments": not args.keep_comments,
        "strip_javadoc":  args.strip_javadoc,
        "mask_strings":   args.mask_strings,
        "strip_loggers":  args.strip_loggers,
    }

    print(bold(f"Tracing from: {entry_path}"))
    deps = trace(entry_path, args.base, src_root)
    print(green(f"Found {len(deps)} class(es)."))

    print_checklist(deps)

    print()
    print(bold("Writing sanitized files..."))
    write_extracted(deps, mapping_dict, options, out_dir)
    write_claude_prompt(deps, out_dir)

    final_map = out_dir / "mapping.json"
    save_mappings(mapping_dict, final_map)
    write_reversal_script(mapping_dict, out_dir)


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
    pkg_count = len(mapping_dict.get("package", []))
    var_count = len(mapping_dict.get("variable", []))
    print(green(f"Loaded {pkg_count} package mapping(s) and {var_count} variable mapping(s)"))
    apply_reversal(target_dir, mapping_dict)


def main():
    if len(sys.argv) == 1:
        interactive_menu()
        return

    parser = argparse.ArgumentParser(
        description="Spring Boot code extractor and sanitizer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive mode
  python spring_extractor.py

  # Trace and extract
  python spring_extractor.py trace \\
    --entry src/main/java/com/myco/OrderService.java \\
    --base com.myco \\
    --src src/main/java \\
    --out ./extracted \\
    --mapping mapping.json

  # Reverse sanitization on generated tests
  python spring_extractor.py reverse \\
    --mapping ./extracted/mapping.json \\
    --dir ./generated-tests
""")

    sub = parser.add_subparsers(dest="command")

    t = sub.add_parser("trace", help="Trace dependencies and extract sanitized files")
    t.add_argument("--entry",          required=True, help="Path to the entry .java file")
    t.add_argument("--base",           required=True, help="Base package to trace within (e.g. com.mycompany)")
    t.add_argument("--src",            default="src/main/java", help="Source root directory")
    t.add_argument("--out",            default="./extracted",   help="Output directory for sanitized files")
    t.add_argument("--mapping",        default=None,            help="Path to mapping.json (optional)")
    t.add_argument("--keep-comments",  action="store_true",     help="Do not strip comments")
    t.add_argument("--strip-javadoc",  action="store_true",     help="Remove @author/@since tags")
    t.add_argument("--mask-strings",   action="store_true",     help="Mask string literals")
    t.add_argument("--strip-loggers",  action="store_true",     help="Remove logger statements")

    r = sub.add_parser("reverse", help="Reverse sanitization mappings on generated test files")
    r.add_argument("--mapping", required=True, help="Path to mapping.json")
    r.add_argument("--dir",     required=True, help="Directory containing generated test .java files")

    args = parser.parse_args()
    if args.command == "trace":
        cmd_trace(args)
    elif args.command == "reverse":
        cmd_reverse(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
