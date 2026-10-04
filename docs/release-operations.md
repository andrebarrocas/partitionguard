# Release and documentation operations

The repository includes workflows, not a published service. A repository owner must supply the GitHub repository, protected environments, PyPI ownership, and documentation hosting settings. No package or site is published by running local tests.

## Continuous integration

`ci.yml` runs on pull requests and pushes. It installs all adapter/test extras, uses a disposable PostgreSQL 16 service, and runs the full suite on Python 3.11, 3.12, and 3.13. A separate check runs lint, package build, wheel metadata validation, and a strict documentation build. An isolated wheel installation with no optional SDK checks both passing and blocked `plan --ci` behavior. Pull-request jobs have read-only repository permissions and no publishing credentials.

GitHub Actions are pinned by full commit SHA. Dependabot proposes updates for Actions and Python dependencies. Review behavior changes and integration results before merging dependency updates.

## Preparing a release

1. Recheck project/package naming and establish ownership of the selected PyPI project. The working name is not a trademark clearance.
2. Complete local and CI verification against real PostgreSQL and Delta tables. Regenerate tutorial transcripts. Review all stated adapter and security limitations.
3. Update the package version, CHANGELOG, and matching `docs/releases/VERSION.md`. Commit the reviewed state and create the matching `vVERSION` tag.
4. Configure the GitHub `release` and `pypi` environments with appropriate reviewer restrictions. Restrict deployment branches/tags to reviewed releases.
5. Configure PyPI trusted publishing for this repository, `release.yml`, and environment `pypi`. Enable private vulnerability reporting and publish maintainer/community contacts.
6. Dispatch the Release workflow on the tagged commit with the matching numeric version. Leave `publish_pypi` false to build and create a draft GitHub release only.

The workflow checks that the package version matches the input and that the existing tag identifies the checked-out commit. It runs tests against PostgreSQL, builds the wheel and source distribution, validates package metadata, and uploads the resulting artifacts. The GitHub release job creates a **draft** from the checked-in notes; it does not silently publish a public release.

Selecting `publish_pypi=true` additionally enables a separate job with `id-token: write`, behind the `pypi` environment. It uploads the already built artifacts through PyPI trusted publishing. Configure required reviewers before using this option; naming an environment in YAML does not itself create a review policy. PyPI uploads are not a dry run and cannot be overwritten for the same version.

The owner publishes the reviewed GitHub draft separately. Once published, update release status documentation so it accurately records the publication rather than leaving an unreleased claim. The initial 0.1.0 files intentionally remain a candidate until that happens.

## Documentation

`mkdocs build --strict` builds the site locally. `docs.yml` runs only by explicit manual dispatch and deploys the checked-out documentation through GitHub Pages artifacts after the `github-pages` environment gate. Configure Pages to use GitHub Actions first. Review the built site before triggering deployment.

Official references: [GitHub environment protection](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments), [PyPI trusted publishing](https://docs.pypi.org/trusted-publishers/using-a-publisher/), [MkDocs deployment](https://www.mkdocs.org/user-guide/deploying-your-docs/), and [GitHub Pages custom workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).
