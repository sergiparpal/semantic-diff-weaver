# Semantic diff review

Use this skill whenever the user asks what a commit, a branch, or a pull request changes in
behavior, how risky a change is, what to test before merging it, which existing tests to run, or
to scaffold those tests. Two read-only tools from the semantic-diff-weaver plugin do the work:
`analyze_semantic_diff` reads two committed Git revisions and returns evidence-backed, risk-ranked
test obligations, and `generate_test_skeletons` scaffolds failing tests for the obligations the
user approves. Neither imports, executes, builds, or modifies the repository.

**Do not run the code under review to find out what it does.** Executing a revision's code, for
instance with `exec` on a file read through `git show`, runs whatever the change contains. With a
change you did not write that is unsafe, and the analysis answers the same question from the
committed source alone. Use the terminal only for read-only Git commands such as `git log`,
`git status`, `git merge-base`, and `git symbolic-ref`.

1. **Pick the refs.** `head_ref` defaults to `HEAD`. The base is what the user named, or for "the
   last commit" its parent, `HEAD~1`. For a branch it is the branch the work will merge into:
   `git symbolic-ref --short refs/remotes/origin/HEAD` names the remote's default branch when one
   is recorded, and `main` or `master` stands in when none is. A local branch can lag behind its
   remote copy, so when both exist use the one that contains the other
   (`git merge-base --is-ancestor main origin/main` succeeds when `origin/main` does), and ask the
   user if they have diverged. Then pass `git merge-base <base> <head>` as `base_ref`: the tool
   compares the two trees as they stand, so commits the base gained after the work branched off
   would read as reverted by it. Say which base you used. Only committed changes are analyzed: if
   `git status` shows uncommitted edits, say that they are not included.
2. **Call `analyze_semantic_diff`** with `repo_path` set to the repository's absolute path,
   `base_ref`, `head_ref`, and `output_format: "markdown"`, which returns the brief alone. On a
   large change, narrow it with `include`, such as `["src/**"]`, and never with `exclude`, which
   also hides the tests the brief would name.
3. **On an error**, read its `error` code and `remediation`. `path_outside_repository` means the
   repository lies outside the roots the operator authorized: tell the user to add it to
   `SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS` rather than retrying with another path.
4. **Report** the verdict and overall risk the brief opens with, then the obligations highest risk
   first, each with its ID, the behavior that changed, the file and symbol, and its evidence IDs.
   Present candidate tests as unverified: the tool matches them statically and never runs them.
   When the user asks which tests to run, give the brief's "Tests to run".
5. **Scaffold only what the user approves.** Call `generate_test_skeletons` with the IDs the user
   named, the same `repo_path`, `base_ref`, `head_ref`, and `include`, and the framework the
   project's tests use, `pytest` or `unittest`. It analyzes again without the model, which can
   number obligations differently from a brief the model took part in: check that each skeleton's
   title is the obligation the user approved, and say so when one is not. Show the patch and where
   each test goes. The tool never applies it; apply it with `git apply` only if the user asks.

Interpreting the analysis is your part: ground every claim in the evidence the tools returned,
not in guesses about the code.
