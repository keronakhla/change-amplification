#!/usr/bin/env python3
"""Mine git history for cross-module change amplification and print JSON evidence.

History is read only up to the merge-base, so a branch never counts as evidence
for itself. The script finds candidates. Reading the code confirms them.
"""
import argparse
import collections
import functools
import itertools
import json
import math
import os
import re
import subprocess
import sys

READ_ONLY_ENV = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
MAX_MODULE_FILES = 100
MAX_BRANCH_FILES = 40
MAX_FILTERED_NAMES = 12
PAGE_CLUSTERS = 12
PAGE_SYNC_COMMENTS = 20

LOCKFILES = {
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock",
    "bun.lockb", "deno.lock", "cargo.lock", "gemfile.lock", "poetry.lock", "uv.lock", "pdm.lock",
    "pipfile.lock", "composer.lock", "go.sum", "go.work.sum", "podfile.lock", "package.resolved",
    "cartfile.resolved", "flake.lock", "mix.lock", "pubspec.lock", "packages.lock.json",
    "gradle.lockfile", ".terraform.lock.hcl",
}
# A tracked build/, out/, or target/ is usually real source (LLVM's lib/Target), so those stay in.
GENERATED_DIRS = {
    "dist", "coverage", "vendor", "third_party", "third-party", "node_modules", "bower_components",
    ".next", ".nuxt", ".svelte-kit", ".turbo", ".wrangler", ".gradle", ".dart_tool", ".venv", "venv",
    "__generated__", "generated", "deriveddata", "__pycache__",
}
LINGUIST_MARK = re.compile(r"(.*): linguist-(?:generated|vendored): (?:set|true)$")
GENERATED_HEADER = re.compile(
    r"(?-i:@generated)\b|\bthis file (is|was|has been) (\w+[- ])?(auto)?generated\b|"
    r"\b(auto-?generated|automatically generated) (file|code)\b|"
    r"\b(auto)?generated\b.*\bdo not edit\b|\bdo not edit\b.*\b(auto)?generated\b",
    re.I,
)
# Strings are matched too, so a generator's output template is not read as its own header.
COMMENT_OR_STRING = re.compile(
    r"(?P<string>`.*?(?:`|\Z)|\"\"\".*?(?:\"\"\"|\Z)|'''.*?(?:'''|\Z)|\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*')|"
    r"/\*.*?(?:\*/|\Z)|<!--.*?(?:-->|\Z)|//[^\n]*|^[ \t]*(?:#|--|;|%)[^\n]*",
    re.S | re.M,
)
GENERATED_HEADER_BYTES = 2048
GENERATED_NAME = re.compile(
    r"(\.min\.(js|css)|\.map|\.snap|\.gen\.[^.]+|\.generated\.[^.]+|_generated\.[^.]+|_gen\.go|"
    r"\.pb(\.[^.]+)+|_pb2(_grpc)?\.pyi?|_pb(\.d)?\.[jt]s|\.g\.(dart|cs)|\.freezed\.dart|\.designer\.cs|"
    r"^zz_generated\..+|^project\.pbxproj|^worker-configuration\.d\.ts|^database\.types\.ts)$",
    re.I,
)
TEST_DIRS = {
    "test", "tests", "__tests__", "spec", "specs", "e2e", "fixtures", "__fixtures__", "__mocks__",
    "__snapshots__", "mocks", "testdata", "testutil", "testutils", "testing", "benchmarks",
    "benches", "cypress", "playwright", "playground", "playgrounds", "uitests", "androidtest",
}
TEST_DIR_NAME = re.compile(r"([-_.](tests?|specs?)|[a-z]Tests?)$")
TEST_NAME = re.compile(
    r"([.-](test|spec)\.[^.]+|\.(stories|story|cy)\.[^.]+|_(test|spec)\.[^.]+|^test_.+\.py|^conftest\.py|"
    r"(Test|Tests)\.(swift|java|kt|cs|m|mm|php|scala|groovy))$"
)
MIGRATION_DIRS = {"migrations", "alembic"}
DB_MIGRATION = re.compile(r"(^|/)(db|database)/(migrate|migration)/")
DB_SCHEMA = re.compile(r"(^|/)db/(schema\.rb|structure\.sql)$")
LOCALE_DIRS = {"locales", "translations"}
# These folders also hold locale-handling code, so only catalogs like en.ts or messages.json count.
LOCALE_CODE_DIRS = {"locale", "i18n", "l10n"}
LOCALE_CATALOG = re.compile(r"^[a-z]{2}([-_][A-Za-z0-9]{2,8})*\.|\.(json5?|ya?ml|xml|properties|csv)$")
LOCALE_DIR_NAME = re.compile(r"(\.lproj|^values-[a-z]{2}(-.+)?)$")
LOCALE_NAME = re.compile(r"\.(xcstrings|strings|stringsdict|arb|po|pot|mo|xliff|xlf|resx|ftl)$", re.I)
DOC_DIRS = {"docs", "doc", "documentation", "man", "manpages", "examples", "example", "samples"}
SOURCE_ROOTS = {"src", "source", "sources", "lib", "app", "pkg", "internal", "cmd"}
# Inside a source root these names are usually features: a marketplace's vendor/, a docs/ route.
DOMAIN_DIRS = DOC_DIRS | {"vendor", "dist", "coverage", "spec", "specs", "playground", "playgrounds"}
DOC_NAME = re.compile(
    r"(\.(md|mdx|rst|txt|adoc)|\.[1-9]|^(readme|license|licence|notice|copying|changelog|authors|"
    r"contributors|contributing))$",
    re.I,
)
ASSET_NAME = re.compile(
    r"\.(png|jpe?g|gif|webp|avif|bmp|tiff?|heic|ico|icns|svg|psd|sketch|fig|pdf|woff2?|ttf|otf|eot|"
    r"mp[34]|wav|m4a|aac|flac|ogg|mov|avi|mkv|webm|lottie|riv|glb|gltf|wasm|zip|gz|tgz|bz2|xz|7z|rar|"
    r"tar|jar|war|dmg|exe|dll|so|dylib|a|o|class|pyc|bin|dat|db|sqlite3?|crt|pem|cer|der)$",
    re.I,
)
CONFIG_DIRS = {
    ".github", ".gitlab", ".circleci", ".buildkite", ".claude", ".cursor", ".vscode", ".idea",
    ".husky", ".devcontainer",
}
CONFIG_NAME = re.compile(
    r"(.+\.(toml|ya?ml|ini|cfg|properties|xcconfig|entitlements|plist|xcscheme|xcworkspacedata|xcprivacy|"
    r"csproj|fsproj|vbproj|sln|props|targets|gemspec|bzl|cmake|mk|modulemap|pc)|"
    r".+\.config\.[^.]+|^\..+|^release-please.*|^package\.json|^package\.swift|^composer\.json|^(ts|js)config.*\.json|"
    r"^(nx|turbo|lerna|angular|vercel|components|biome|deno)\.jsonc?|^dockerfile.*|.+\.dockerfile|"
    r"^docker-compose.*|^compose\.ya?ml|^(gnu)?makefile.*|^justfile|^procfile|^rakefile|^gemfile|^podfile|"
    r"^cartfile|^brewfile|^codeowners|^go\.(mod|work)|^requirements.*\.txt|^setup\.py|^pom\.xml|"
    r"^(build|settings)\.gradle(\.kts)?|^cmakelists\.txt|^build(\.bazel)?|^workspace(\.bazel)?|"
    r"^module\.bazel|^mix\.exs|^rust-toolchain(\.toml)?|^wrangler\..+)$",
    re.I,
)
HEADER_OR_STYLE = {".h", ".hh", ".hpp", ".hxx", ".inl", ".pyi", ".rbi", ".css", ".scss", ".sass", ".less"}
MECHANICAL_SUBJECT = re.compile(
    r"^\s*((chore|build|ci|fix)\(deps[^)]*\)|style(\(|:)|bump\b|format\b|prettier\b|lint\b|revert\b|"
    r"apply (prettier|formatting)|run (prettier|formatter)|reformat\b|update dependenc|"
    r"chore(\([^)]*\))?: (release|bump)\b|release(:|\s+(v?\d|candidate))|v?\d+\.\d+\.\d+)",
    re.I,
)
SYNC_COMMENT = (
    r"(keep|must|should)[^.]{0,40}in[- ]sync|"
    r"(make sure|be sure|remember|(don'?t|do not) forget)( to)? also (add|update|change|edit|modify|bump|adjust)"
)


def run_git(repo, *args, check=True):
    result = subprocess.run(
        ["git", "-C", repo, "-c", "core.quotePath=false", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        env=READ_ONLY_ENV,
    )
    if check and result.returncode != 0:
        sys.exit("git {} failed: {}".format(args[0], result.stderr.strip()))
    return result.stdout if result.returncode == 0 else ""


def meaningful_dirs(folders):
    """Lowercase folder names, dropping domain words that sit inside a source root."""
    kept, in_source = set(), False
    for folder in map(str.lower, folders):
        if not (in_source and folder in DOMAIN_DIRS):
            kept.add(folder)
        in_source = in_source or folder in SOURCE_ROOTS
    return kept


def classify(path):
    """Return why a file's co-changes are expected, or None for design-bearing code."""
    parts = path.split("/")
    name, dirs = parts[-1], meaningful_dirs(parts[:-1])
    lowered = name.lower()
    if lowered in LOCKFILES:
        return "lockfile"
    if dirs & GENERATED_DIRS or "Pods" in parts[:-1] or GENERATED_NAME.search(name):
        return "generated"
    if dirs & TEST_DIRS or TEST_NAME.search(name) or any(TEST_DIR_NAME.search(p) for p in parts[:-1]):
        return "test"
    if dirs & MIGRATION_DIRS or DB_SCHEMA.search(path) or DB_MIGRATION.search(path):
        return "migration"
    if (
        dirs & LOCALE_DIRS
        or (dirs & LOCALE_CODE_DIRS and LOCALE_CATALOG.search(name))
        or any(LOCALE_DIR_NAME.search(p) for p in dirs)
        or LOCALE_NAME.search(name)
    ):
        return "locale"
    if ASSET_NAME.search(name) or any(p.endswith(".xcassets") for p in dirs):
        return "asset"
    if dirs & CONFIG_DIRS or CONFIG_NAME.search(name):
        return "config"
    if DOC_NAME.search(name) or dirs & DOC_DIRS:
        return "docs"
    return None


def find_split_folders(code_files, limit):
    """Return folders too large to be one module, so layouts need no per-language rules."""
    sizes = collections.Counter()
    for path in code_files:
        parts = path.split("/")[:-1]
        for depth in range(1, len(parts) + 1):
            sizes["/".join(parts[:depth])] += 1
    return {""} | {folder for folder, size in sizes.items() if size >= limit}


def find_flat_folders(code_files):
    """Return folders with no structure to lean on, where each file has to count as a module."""
    direct = collections.Counter(path.rsplit("/", 1)[0] if "/" in path else "(root)" for path in code_files)
    limit = max(6, min(MAX_MODULE_FILES, len(code_files) / 2))
    return {folder for folder, size in direct.items() if size >= limit}


def folder_of(path, split_folders):
    """Name the first folder on a file's path small enough to stay whole."""
    current = ""
    for segment in path.split("/")[:-1]:
        if current not in split_folders:
            break
        current = current + "/" + segment if current else segment
    return current or "(root)"


def module_of(path, split_folders, flat_folders):
    """Name a file's module: its folder, or the file itself inside a flat folder."""
    folder = folder_of(path, split_folders)
    return path if folder in flat_folders else folder


def is_declaration(name):
    lowered = name.lower()
    return lowered.endswith(".d.ts") or "." + lowered.rsplit(".", 1)[-1] in HEADER_OR_STYLE


def is_companion(a, b):
    """Same-named header/style and implementation files are one unit split by language."""
    name_a, name_b = a.rsplit("/", 1)[-1], b.rsplit("/", 1)[-1]
    return name_a.split(".")[0] == name_b.split(".")[0] and is_declaration(name_a) != is_declaration(name_b)


def resolve_base(repo, head, requested):
    """Return (base ref, merge base), or (None, head) to audit a repository with no default branch."""
    if requested:
        candidates = [requested]
    else:
        origin_head = run_git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False).strip()
        candidates = [origin_head, "origin/main", "origin/master", "main", "master"]
    for candidate in filter(None, candidates):
        if run_git(repo, "rev-parse", "--verify", "--quiet", candidate + "^{commit}", check=False).strip():
            merge_base = run_git(repo, "merge-base", head, candidate, check=False).strip()
            if merge_base:
                return candidate, merge_base
    if requested:
        sys.exit("No merge base between --head and --base {}.".format(requested))
    return None, head


def resolve_scope(repo, head, args):
    """Decide what this run assesses, and return (is audit, base ref, merge base)."""
    if args.audit:
        return True, None, head
    base_ref, merge_base = resolve_base(repo, head, args.base)
    if base_ref is None:
        return True, None, head
    if args.base:
        return False, base_ref, merge_base
    branch = run_git(repo, "symbolic-ref", "--short", "--quiet", "HEAD", check=False).strip()
    on_default = args.head == "HEAD" and branch == base_ref.split("/", 1)[-1]
    detached_on_base = not branch and merge_base == head
    return (True, base_ref, head) if on_default or detached_on_base else (False, base_ref, merge_base)


def read_history(repo, base, max_commits):
    """Return commits as (sha, subject, current paths) newest first, plus each old name's current name."""
    output = run_git(
        repo, "log", base, "--no-merges", "-M", "--name-status",
        "--max-count={}".format(max_commits), "--format=%x01%h%x00%s",
    )
    renamed_to, commits, current = {}, [], None
    for line in output.splitlines():
        if line.startswith("\x01"):
            sha, _, subject = line[1:].partition("\x00")
            current = (sha, subject, set())
            commits.append(current)
        elif line and current:
            fields = line.split("\t")
            if fields[0][0] in "RC" and len(fields) == 3:
                target = renamed_to.get(fields[2], fields[2])
                if fields[0][0] == "R":
                    renamed_to[fields[1]] = target
                current[2].add(target)
            elif len(fields) == 2:
                current[2].add(renamed_to.get(fields[1], fields[1]))
    return commits, renamed_to


def declares_generated(opening):
    """Return whether a comment, rather than a string a generator writes out, marks the file generated."""
    return any(
        not span.group("string") and GENERATED_HEADER.search(span.group())
        for span in COMMENT_OR_STRING.finditer(opening)
    )


def read_generated_headers(repo, paths, revision):
    """Return the files whose opening lines declare them generated."""
    request = "".join("{}:{}\n".format(revision, path) for path in paths)
    output = subprocess.run(
        ["git", "-C", repo, "cat-file", "--batch"],
        input=request.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=READ_ONLY_ENV,
    ).stdout
    generated, offset = set(), 0
    for path in paths:
        end = output.index(b"\n", offset)
        fields = output[offset:end].split()
        offset = end + 1
        if fields[-1] != b"missing":
            size = int(fields[-1])
            opening = output[offset:offset + min(size, GENERATED_HEADER_BYTES)].decode("utf-8", "replace")
            if declares_generated(opening):
                generated.add(path)
            offset += size + 1
    return generated


def read_sync_comments(repo, revision, kind_of, only):
    """Find comments where maintainers say a decision lives in several places, one per file first.

    With no revision, search the working tree, including new files.
    """
    options, target = ([], [revision]) if revision else (["--untracked"], [])
    prefix = len(revision) + 1 if revision else 0
    found, per_file = [], collections.Counter()
    output = run_git(repo, "grep", "-nIiE", *options, "-e", SYNC_COMMENT, *target, check=False)
    for line in output.splitlines():
        fields = line[prefix:].split(":", 2)
        if len(fields) != 3 or kind_of(fields[0]):
            continue
        if only is None or fields[0] in only:
            per_file[fields[0]] += 1
            comment = {"at": "{}:{}".format(fields[0], fields[1]), "text": fields[2].strip()[:110]}
            found.append((per_file[fields[0]], comment))
    return [comment for _, comment in sorted(found, key=lambda item: item[0])]


def read_generated_attributes(repo, paths, revision):
    """Read attributes at the assessed commit, or the working tree on older git."""
    if not paths:
        return set()
    for source in (["--source", revision], []):
        result = subprocess.run(
            ["git", "-C", repo, "check-attr", *source, "--stdin", "linguist-generated", "linguist-vendored"],
            input="\n".join(paths) + "\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            encoding="utf-8",
            errors="replace",
            env=READ_ONLY_ENV,
        )
        if result.returncode == 0:
            break
    return {match.group(1) for match in map(LINGUIST_MARK.match, result.stdout.splitlines()) if match}


def read_branch(repo, merge_base, head, include_uncommitted):
    """Return the changed, deleted, added, and untracked paths this branch check covers."""
    changed, deleted, added, untracked = set(), set(), set(), set()
    target = [merge_base] if include_uncommitted else [merge_base, head]
    output = run_git(repo, "diff", "--name-status", "-M", *target)
    for line in output.splitlines():
        fields = line.split("\t")
        status = fields[0][0]
        if status == "R" and len(fields) == 3:
            changed.add(fields[1])
            added.add(fields[2])
        elif status == "D":
            deleted.add(fields[1])
        elif status == "A":
            added.add(fields[1])
        elif len(fields) >= 2:
            changed.add(fields[-1])
    if include_uncommitted:
        untracked.update(run_git(repo, "ls-files", "--others", "--exclude-standard").splitlines())
        added |= untracked
    return changed, deleted, added, untracked


def group_pairs(pairs, hub_degree):
    """Split pairs into hub stars and connected groups, so one hub cannot chain everything."""
    degree = collections.Counter()
    for pair in pairs:
        degree.update((pair["a"], pair["b"]))
    hubs = {path for path, partners in degree.items() if partners >= hub_degree}
    stars, remaining = collections.defaultdict(list), []
    for pair in pairs:
        ends = [end for end in (pair["a"], pair["b"]) if end in hubs]
        if ends:
            stars[max(ends, key=lambda end: (degree[end], end))].append(pair)
        else:
            remaining.append(pair)

    parent = {}

    def find(item):
        parent.setdefault(item, item)
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    for pair in remaining:
        parent[find(pair["a"])] = find(pair["b"])
    groups = collections.defaultdict(list)
    for pair in remaining:
        groups[find(pair["a"])].append(pair)
    return [(hub, group) for hub, group in stars.items()] + [(None, group) for group in groups.values()]


def branch_state(files, pairs, counts, changed, deleted, min_confidence):
    touched = (files & changed) | (files & deleted)
    missing = {}
    for pair in pairs:
        for source, partner in ((pair["a"], pair["b"]), (pair["b"], pair["a"])):
            if source in touched and partner not in touched and pair["together"] / counts[source] >= min_confidence:
                missing.setdefault(partner, set()).add(source)
    if not touched:
        state = "untouched"
    elif missing:
        state = "partial"
    else:
        state = "complete"
    return {
        "state": state,
        "changed": sorted(files & changed),
        "deleted": sorted(files & deleted),
        "missing_partners": [
            {"path": path, "usually_changes_with": sorted(sources)} for path, sources in sorted(missing.items())
        ],
    }


def is_kept(path, keep):
    return any(path == item or path.startswith(item + "/") for item in keep)


@functools.lru_cache(maxsize=None)
def filtering_folder(folder):
    """Return the outermost part of a folder whose name alone filters its files, or None."""
    parts = folder.split("/")
    for depth in range(1, len(parts) + 1):
        prefix = "/".join(parts[:depth])
        if classify(prefix + "/x"):
            return prefix
    return None


def summarize_filtered(kinds):
    """Name the folders that hide source files, so a real module cannot vanish unnoticed."""
    by_name = collections.defaultdict(collections.Counter)
    for path, kind in kinds.items():
        folder, _, name = path.rpartition("/")
        outer = filtering_folder(folder) if kind and folder and not classify(name) else None
        if outer:
            by_name[outer.rsplit("/", 1)[-1]][outer] += 1
    ranked = sorted(by_name.items(), key=lambda item: (-sum(item[1].values()), item[0]))
    summary = {
        name: "{} files, largest {}".format(sum(folders.values()), folders.most_common(1)[0][0])
        for name, folders in ranked[:MAX_FILTERED_NAMES]
    }
    if len(ranked) > MAX_FILTERED_NAMES:
        summary["(other names)"] = str(len(ranked) - MAX_FILTERED_NAMES)
    return summary


def evidence_commands(merge_base, head, include_uncommitted):
    """Give exact read commands, so later steps need not care whether the work is committed."""
    commit = "git show <sha> -- :/<a> :/<b>"
    if include_uncommitted:
        return {
            "commit": commit,
            "read": 'cat "$(git rev-parse --show-toplevel)/<path>"',
            "sweep": "git grep -nF --untracked -e <value> -- :/ | LC_ALL=C cut -c1-240",
            "diff": "git diff {}".format(merge_base[:10]),
        }
    return {
        "commit": commit,
        "read": "git show {}:<path>".format(head[:10]),
        "sweep": "git grep -nF -e <value> {} -- :/ | LC_ALL=C cut -c1-240".format(head[:10]),
        "diff": "git diff {} {}".format(merge_base[:10], head[:10]),
    }


def analyze(args):
    repo = run_git(args.repo, "rev-parse", "--show-toplevel").strip()
    head = run_git(repo, "rev-parse", "--verify", args.head + "^{commit}").strip()
    is_audit, base_ref, merge_base = resolve_scope(repo, head, args)
    on_checkout = head == run_git(repo, "rev-parse", "HEAD").strip()
    dirty = run_git(repo, "status", "--porcelain").splitlines() if on_checkout else []
    include_uncommitted = bool(dirty) and not is_audit
    tracked = set(run_git(repo, "ls-tree", "-r", "--name-only", merge_base).splitlines())
    history, renamed_to = read_history(repo, merge_base, args.max_commits)
    linguist = read_generated_attributes(repo, sorted(tracked), merge_base)
    keep = [os.path.normpath(item).replace(os.sep, "/") for item in args.keep]

    def kind_of(path):
        if is_kept(path, keep):
            return None
        return "generated" if path in linguist else classify(path)

    kinds = {path: kind_of(path) for path in sorted(tracked)}
    code_files = [path for path, kind in kinds.items() if not kind]
    module_limit = args.module_files or max(6, min(MAX_MODULE_FILES, math.ceil(0.15 * len(code_files))))
    split_folders = find_split_folders(code_files, module_limit)
    flat_folders = find_flat_folders(code_files)

    filtered, kept = collections.Counter(), []
    bulk = mechanical = 0
    for sha, subject, paths in history:
        if MECHANICAL_SUBJECT.search(subject):
            mechanical += 1
            continue
        code = set()
        for path in paths & tracked:
            kind = kinds[path]
            if kind:
                filtered[kind] += 1
            else:
                code.add(path)
        if len(code) > args.max_files:
            bulk += 1
        elif code:
            kept.append((sha, subject, code))

    counts, together, focused, folder_pairs = collections.Counter(), collections.Counter(), collections.Counter(), set()
    modules = {}
    for _, _, code in kept:
        counts.update(code)
        for path in code:
            modules.setdefault(path, module_of(path, split_folders, flat_folders))
        folder_pairs.update(
            tuple(sorted(p)) for p in itertools.combinations({folder_of(f, split_folders) for f in code}, 2)
        )
        together.update(itertools.combinations(sorted(code), 2))
        if len(code) <= args.focused_files:
            focused.update(itertools.combinations(sorted(code), 2))

    total, pairs, same_module, companions = len(kept), [], 0, 0
    for (a, b), support in together.items():
        if support < args.min_together or focused[(a, b)] < args.min_focused:
            continue
        if modules[a] == modules[b]:
            same_module += 1
            continue
        if is_companion(a, b):
            companions += 1
            continue
        confidence = support / min(counts[a], counts[b])
        lift = support * total / (counts[a] * counts[b])
        if confidence >= args.min_confidence and lift >= args.min_lift:
            pairs.append({
                "a": a, "b": b, "together": support, "focused": focused[(a, b)],
                "confidence": round(confidence, 2), "lift": round(lift, 1),
            })

    paired_files = sorted({p[end] for p in pairs for end in ("a", "b")})
    declared = {path for path in read_generated_headers(repo, paired_files, merge_base) if not is_kept(path, keep)}
    pairs = [pair for pair in pairs if pair["a"] not in declared and pair["b"] not in declared]

    smallest_first = sorted(kept, key=lambda commit: len(commit[2]))
    for pair in pairs:
        pair["commits"] = [
            sha for sha, _, code in smallest_first
            if len(code) <= args.focused_files and pair["a"] in code and pair["b"] in code
        ][:args.max_pair_commits]

    former_names = collections.defaultdict(set)
    for old, new in renamed_to.items():
        former_names[new].add(old)
    subject_of = {sha: subject for sha, subject, _ in kept}

    changed, deleted, added, untracked = read_branch(repo, merge_base, head, include_uncommitted)
    clusters = []
    for hub, group in group_pairs(pairs, args.hub_degree):
        files = {p["a"] for p in group} | {p["b"] for p in group}
        ranked_files = sorted(files, key=lambda f: (f != hub, -counts[f], f))
        listed = sorted(group, key=lambda p: (-p["together"], -p["confidence"]))[:args.max_pairs]
        listed_files = {p[end] for p in listed for end in ("a", "b")}
        clusters.append({
            "hub": hub,
            "file_count": len(files),
            "files": {f: counts[f] for f in ranked_files[:args.max_cluster_files]},
            "pair_count": len(group),
            "pairs": listed,
            "subjects": {sha: subject_of[sha][:90] for p in listed for sha in p["commits"]},
            "former_paths": {f: sorted(former_names[f]) for f in sorted(listed_files) if f in former_names},
            "branch": branch_state(files, group, counts, changed, deleted, args.min_confidence),
        })
    order = {"partial": 0, "complete": 1, "untouched": 2}
    clusters.sort(key=lambda c: (order[c["branch"]["state"]], -sum(p["together"] for p in c["pairs"])))
    clusters = [dict(id="L{}".format(number), **cluster) for number, cluster in enumerate(clusters, 1)]
    covered = clusters if is_audit else [c for c in clusters if c["branch"]["state"] != "untouched"]
    sync_comments = read_sync_comments(
        repo, None if include_uncommitted else head, kind_of, None if is_audit else changed | added
    )

    branch_code = {path for path in (changed | added) if not kind_of(path)}
    branch_folders = collections.defaultdict(list)
    for path in branch_code:
        branch_folders[folder_of(path, split_folders)].append(path)
    new_pairs = [pair for pair in itertools.combinations(sorted(branch_folders), 2) if pair not in folder_pairs]
    new_pairs.sort(key=lambda pair: -(len(branch_folders[pair[0]]) + len(branch_folders[pair[1]])))
    crossings = [
        {"modules": [m1, m2], "files": sorted(branch_folders[m1])[:3] + sorted(branch_folders[m2])[:3]}
        for m1, m2 in new_pairs
    ]

    warnings = []
    if base_ref is None and not args.audit:
        warnings.append("no default branch found, so this audits the head; pass --base to compare a branch")
    if run_git(repo, "rev-parse", "--is-shallow-repository", check=False).strip() == "true":
        warnings.append("shallow clone: history is cut off, so evidence is incomplete; the user can run git fetch --unshallow")
    if not clusters:
        warnings.append("history found no candidates (usable commits: {}), but the code may still have leaks".format(total))
    if history and bulk / len(history) > 0.25:
        warnings.append("{} of {} commits exceeded --max-files, likely squash merges or bulk edits".format(bulk, len(history)))
    if dirty and is_audit:
        warnings.append("{} uncommitted paths are not part of this audit".format(len(dirty)))
    if include_uncommitted:
        warnings.append("{} uncommitted paths are included in this branch check".format(len(dirty)))
    if not is_audit and not (changed or deleted or added):
        warnings.append("this branch changes nothing against {}; pass --audit to audit the repository".format(base_ref))
    if len(crossings) > args.max_crossings:
        warnings.append("{} of {} new module crossings are listed, widest first; raise --max-crossings for more".format(
            args.max_crossings, len(crossings)))
    if len(branch_code) > MAX_BRANCH_FILES:
        warnings.append("{} of {} changed code files are listed".format(MAX_BRANCH_FILES, len(branch_code)))
    module_sizes = collections.Counter(
        folder_of(path, split_folders) for path, kind in kinds.items() if not kind
    )

    return {
        "repo": repo.rsplit("/", 1)[-1],
        "mode": "audit" if is_audit else "branch_check",
        "head": head[:10],
        "base": {"ref": base_ref, "merge_base": merge_base[:10]},
        "commands": evidence_commands(merge_base, head, include_uncommitted),
        "history": {
            "commits_read": len(history),
            "commits_used": total,
            "skipped_bulk": bulk,
            "skipped_mechanical": mechanical,
            "code_files": len(counts),
            "module_file_limit": module_limit,
            "module_sizes": dict(module_sizes.most_common(args.max_modules)),
            "module_count": len(module_sizes),
            "filtered_folders": summarize_filtered(kinds),
            "expected_changes_filtered": dict(filtered.most_common()),
            "generated_by_header": len(declared),
            "pairs_within_one_module": same_module,
            "companion_pairs": companions,
        },
        "branch": {
            "changed": len(changed),
            "added": len(added),
            "deleted": len(deleted),
            "code_files": sorted(branch_code)[:MAX_BRANCH_FILES],
            "untracked": sorted(branch_code & untracked)[:MAX_BRANCH_FILES],
            "new_module_crossings": crossings[:args.max_crossings],
            "new_module_crossing_count": len(crossings),
        },
        "warnings": warnings,
        "sync_comment_count": len(sync_comments),
        "sync_comments": sync_comments,
        "cluster_count": len(clusters),
        "covered_cluster_count": len(covered),
        "clusters": covered,
    }


def paginate(comments, cluster_lines, room):
    """Split evidence into pages that fit the budget, giving each page at least one cluster."""
    pages, comment_at, line_at = [], 0, 0
    while not pages or comment_at < len(comments) or line_at < len(cluster_lines):
        page_comments = comments[comment_at:comment_at + PAGE_SYNC_COMMENTS]
        comment_at += len(page_comments)
        used = len(json.dumps(page_comments, separators=(",", ":")))
        first = line_at
        while line_at < len(cluster_lines) and line_at - first < PAGE_CLUSTERS:
            size = len(cluster_lines[line_at]) + 2
            if line_at > first and used + size > room:
                break
            used += size
            line_at += 1
        pages.append((page_comments, first, line_at))
    return pages


def render(result, page, max_chars):
    """Print one page of evidence, strongest first, as whole JSON that agent tools can read."""
    compact = {"separators": (",", ":")}
    comments, clusters = result.pop("sync_comments"), result.pop("clusters")
    lines = [json.dumps(cluster, **compact) for cluster in clusters]
    pages = paginate(comments, lines, max_chars - len(json.dumps(result, **compact)) - 500)
    if not 1 <= page <= len(pages):
        sys.exit("--page {} is out of range: there are {} pages.".format(page, len(pages)))
    page_comments, first, last = pages[page - 1]
    if len(pages) > 1:
        comments_so_far = sum(len(shown) for shown, _, _ in pages[:page])
        next_step = "add --page {} for more".format(page + 1) if page < len(pages) else "this is the last page"
        result["warnings"].append("page {} of {}: {} of {} covered clusters and {} of {} sync comments so far; {}".format(
            page, len(pages), last, len(lines), comments_so_far, len(comments), next_step))
    unlisted = {
        c["id"]: c["pair_count"] - len(c["pairs"]) for c in clusters[first:last] if c["pair_count"] > len(c["pairs"])
    }
    if unlisted:
        result["warnings"].append("{} pairs in {} are not listed; raise --max-pairs to see them".format(
            sum(unlisted.values()), ", ".join(unlisted)))
    result.update(page=page, page_count=len(pages), examined_cluster_count=last, sync_comments=page_comments)
    return '{},"clusters":[\n{}\n]}}'.format(json.dumps(result, **compact)[:-1], ",\n".join(lines[first:last]))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=".", help="repository path")
    parser.add_argument("--base", help="base ref (default: origin/HEAD, then main, then master, else audit the head)")
    parser.add_argument("--head", default="HEAD", help="branch to assess")
    parser.add_argument("--audit", action="store_true", help="audit the whole repository, even from a branch")
    parser.add_argument("--page", type=int, default=1, help="page of evidence to show, strongest first")
    parser.add_argument("--keep", action="append", default=[], metavar="PATH",
                        help="folder or file to count as code despite its name; repeatable")
    parser.add_argument("--max-commits", type=int, default=3000)
    parser.add_argument("--max-files", type=int, default=40, help="skip commits touching more code files")
    parser.add_argument("--module-files", type=int,
                        help="split folders with at least this many code files (default: 15%% of code files, at most 100)")
    parser.add_argument("--hub-degree", type=int, default=5, help="partners that make a file a hub")
    parser.add_argument("--max-cluster-files", type=int, default=15, help="files shown per cluster")
    parser.add_argument("--min-together", type=int, default=3, help="minimum shared commits")
    parser.add_argument("--min-confidence", type=float, default=0.5, help="shared commits / rarer file's commits")
    parser.add_argument("--min-lift", type=float, default=2.0, help="how far above chance the pair co-changes")
    parser.add_argument("--focused-files", type=int, default=10, help="largest commit that counts as focused")
    parser.add_argument("--min-focused", type=int, default=2, help="minimum shared focused commits")
    parser.add_argument("--max-pairs", type=int, default=8, help="pairs shown per cluster")
    parser.add_argument("--max-crossings", type=int, default=8, help="new module crossings shown")
    parser.add_argument("--max-pair-commits", type=int, default=3, help="focused commits shown per pair, smallest first")
    parser.add_argument("--max-modules", type=int, default=40, help="modules listed in the output")
    parser.add_argument("--max-chars", type=int, default=24000, help="page size that agent tools can read whole")
    args = parser.parse_args()
    print(render(analyze(args), args.page, args.max_chars))


if __name__ == "__main__":
    main()
