---
name: change-amplification
description: Audits a repository for design decisions leaked across modules, using git history to find code that keeps changing together. On a branch, checks whether it adds, removes, or misses that coupling. Inspired by A Philosophy of Software Design.
disable-model-invocation: true
license: MIT
compatibility: Requires git and Python 3.9 or newer.
---

# Change Amplification

Find **leaks**: one design decision encoded in several modules, so one change to it becomes edits in all of them. History nominates **candidates**, a repository-wide **sweep** finds the partners history never saw, and only the code confirms a leak.

This skill is read-only and ends at the report. The user chooses which fix to apply.

## 1. Mine

From the repository, run the script in the folder holding this `SKILL.md`:

```bash
python3 <skill-folder>/scripts/cochange.py
```

Add `--base <ref>` or `--head <ref>` when the user names them, and `--audit` when the user wants the whole repository audited from a branch. `--help` lists the thresholds.

`mode` says whether this run is an **audit** or a **branch check**, which includes uncommitted work. `commands` holds the exact commands for reading this run's code.

Read `warnings`, then compare `module_sizes` with the repository's folders. Lower `--module-files` below `module_file_limit` when a module lumps features together, and raise it when modules are only layout folders. Inside a flat folder each file counts as its own module. When `filtered_folders` names a folder of real source, rerun with `--keep <folder>`.

**Done when** `module_sizes` matches the repository's real structure, `filtered_folders` hides no real source, and every warning that applies to the assessed commit is carried into the report.

## 2. Confirm

Cover every cluster in the output. When the user asks for a deeper audit, repeat this step for each later page with `--page <n>`. A branch check lists only the clusters the branch touches.

Start with `sync_comments`. Each is a maintainer saying a decision lives in several places, so treat it as a candidate even when no cluster names it. In a branch check they come from changed files. When the branch changed what a comment guards and left a named partner alone, that is a likely Miss. A partner in another repository cannot be confirmed, so list it under Limits.

Then take each listed pair in the covered clusters, in output order. A cluster can hold several decisions, so group its pairs by decision as you go:

1. **Triage** the pair from the `subjects` of its `commits`. When they show unrelated work or a plain structural reason, classify it now and record it as triaged.
2. Otherwise read its commits with `commands.commit`, adding any older names from `former_paths`.
3. Read both files with `commands.read`.
4. Name the **decision** in one sentence: the fact that, if it changed, would force edits in both files.
5. **Sweep** for each value or identifier with `commands.sweep`. Every hit that encodes the decision, in any language, is a **partner**, inside the cluster or outside it.
6. Classify the decision:
   - **Leak**: you can point to `file:line` in each partner where the same decision is independently encoded. A middle layer that only forwards values it never uses counts.
   - **Resolved**: the coupling was real and an earlier commit already removed it.
   - **Expected**: the files change together for a sound structural reason. Common cases are a composition root constructing features, a registry or catalog listing its members, a caller adopting a deliberate interface change, a duplication the repository documents as deliberate, and build or vendored output the script failed to filter.
   - **Coincidence**: the shared commits changed these files for unrelated reasons.

A **hub** cluster has one file coupled to many, and is often a legitimate composition root with one real leak inside it.

**Done when** every listed sync comment and pair is classified or triaged, and every leak has been swept.

## 3. Judge the branch

Skip this step for an audit.

Run `commands.diff` and read any new files in `branch.untracked`. For each value, identifier, or rule the diff changes, sweep the head for its old form. Each surviving hit that encodes the same decision is a new leak, even when no cluster named it. `missing_partners` is only a hint: its entries can be wrong, and an empty list says nothing about Misses.

Give each confirmed leak and each entry in `new_module_crossings` one verdict, using the first that fits:

- **Misses**: the branch changed the decision in some partners while another still encodes the old one. A new member added to a shared list, schema, or type in only some partners counts. This is a likely bug.
- **Adds**: the branch encodes the decision in a module that did not encode it before, or a new crossing carries shared knowledge.
- **Removes**: fewer modules encode the decision after the branch than before it.
- **Pays**: the branch carried the change to every partner, so the same modules still encode the decision.
- **Unrelated**: the branch leaves the decision alone.

The overall verdict is **worse** with any Misses or Adds, **better** with Removes and neither of those, and otherwise **neutral**.

**Done when** every changed value has been swept and every confirmed leak and crossing has one verdict.

## 4. Hide

For each confirmed leak, design the fix:

- **Owner**: the one module that should know the decision.
- **Interface**: what the owner exposes so partners stop knowing it. It should be simpler than the decision it hides. Pull complexity into the owner rather than passing it to callers.
- **Next change**: a realistic future change to the decision, with the files it touches today and after the fix. Count every file that must be edited, including tests, previews, and assets. The last commit that changed the decision is the best baseline: `git show --stat <sha>`.
- **Fix cost**: the files the one-time migration touches.

When the next change touches the same number of files either way, recommend leaving the code as it is.

**Done when** every leak has a before and after file count, plus either an owner, interface sketch, and fix cost, or a recommendation to leave it.

## 5. Report

Lead with the headline. Order a branch check's leaks by Misses, Adds, Removes, Pays, Unrelated, and an audit's by the most files saved on the next change.

```markdown
**Repository audit: <examined_cluster_count> of <cluster_count> clusters examined.** <The main result, in one sentence.>
<Or, in a branch check:>
**Branch verdict: <worse, better, or neutral>.** <One sentence why.>

### <The decision, in one sentence>
- **Branch:** <verdict and what it did. Omit in an audit.>
- **Encoded at:** `file:line`, `file:line`
- **Found by:** any of history (strongest pair: <N> shared commits, <N> focused), sync comment, sweep
- **Hide it in:** <owner> behind <interface>. When the fix saves no files, write **Leave it:** <why> instead.
- **Next change:** <change>, <N> files today, <N> after
- **Fix cost:** <N> files once. Omit when leaving it.

### Rejected candidates
- <Cluster ids and pairs, grouped when they share a reason>: <Resolved, Expected, or Coincidence, and why>

### Limits
- <Warnings that affect confidence>
- <Pairs classified from commit subjects alone>
- <Branch check: `cluster_count` minus `covered_cluster_count` clusters left unexamined, and `--audit` for a full audit>
```

**Done when** every leak, rejected candidate, and limit appears in the report.
