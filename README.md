# change-amplification

An agent skill that finds design decisions leaked across modules, using your git history as evidence.

A decision has leaked when one small change keeps forcing edits in several places. This skill finds those decisions, confirms each one in the code, and proposes where to hide it.

## What you get

A read-only report. This finding comes from a real audit of [Homebrew](https://github.com/Homebrew/brew) at commit `bc63375ac3`:

> ### The set of `{{token}}` names allowed in install steps is declared in four places
>
> - Encoded at: `install_steps.rb:1453`, `rubocops/shared/install_steps_helper.rb:443`, `cask/artifact/install_steps.rb:62`, `cask_artifact.rb:51`
> - Found by: history (strongest pair: 24 shared commits)
> - Hide it in: `InstallSteps`, behind a `TEMPLATE_TOKENS` registry
> - Next change: add one token, 7 files today, 5 after
> - Fix cost: 3 files once

Each report also lists the candidates it rejected and why, so you can check its judgment. When a fix would not reduce the files a future change touches, it recommends leaving the code alone.

## Install

You need git and Python 3.9 or newer. There are no packages to install.

For Claude Code:

```bash
git clone https://github.com/keronakhla/change-amplification ~/.claude/skills/change-amplification
```

For Codex:

```bash
git clone https://github.com/keronakhla/change-amplification ~/.agents/skills/change-amplification
```

For other agents that support [Agent Skills](https://agentskills.io):

```bash
npx skills add keronakhla/change-amplification
```

## Use

In Claude Code, run this inside any git repository. In other agents, ask them to run the change-amplification skill.

```text
/change-amplification
```

- It audits the repository on your default branch.
- On a feature branch, it checks only what the branch touches, including uncommitted work, and reports whether the branch adds, removes, or misses a leak.
- To audit from a feature branch, run `/change-amplification audit the whole repository`.

## How it works

1. Mine: a small script finds files in different modules that keep changing together. It filters out expected pairs such as tests, lockfiles, migrations, and generated code, and collects the "keep in sync" comments maintainers leave behind.
2. Confirm: the agent reads the commits and code, names the shared decision, and searches the repository for every copy of it. Candidates without a real shared decision are rejected.
3. Judge the branch: on a feature branch, each leak gets a verdict of Misses, Adds, Removes, Pays, or Unrelated.
4. Hide: each leak gets an owner module, a simpler interface, and a before-and-after count of the files the next change would touch.

## When to use it

Use it for a design audit, before a refactor, or to learn where a codebase is hard to change. It does not replace code review.

## Good to know

- Cost: a run reads a lot of code. Test runs used roughly 55,000 to 350,000 tokens and took from under a minute to 15 minutes, depending on the repository and model.
- History: the skill relies on past commits, so it finds less in new repositories and shallow clones (copies missing older commits).
- Large repositories: the report says how many groups of related files it examined. Ask for a deeper audit to work through the rest.
- Privacy: the script runs locally and never writes to your repository. Your agent sends the code it reads to its model provider, as it does for any other task.
- Coverage: full runs were checked on Ruby, Swift, C++, TypeScript, and JavaScript codebases on macOS, and the script's results were identical on Python 3.9 and 3.15. Go, Java, Python, and Rust layouts have only been checked with fixtures or small samples, so reports from those stacks are especially welcome.

## Credits

Inspired by John Ousterhout's *A Philosophy of Software Design*. This project is independent and not endorsed by the author.

## License

[MIT](LICENSE)
