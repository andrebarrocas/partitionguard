# Name review — 2026-10-04

The requested working name **DataGuard** is widely used. The GitHub repository
search API returned 564 name matches, including unrelated products, and an
existing [DataGuard validation project](https://github.com/eu-parc/dataguard)
publishes as [peh-dataguard](https://pypi.org/project/peh-dataguard/).

We selected **PartitionGuard** as the local project and distribution name.
Direct public API observations on the review date:

| Query | Result |
| --- | --- |
| `https://pypi.org/pypi/dataguard/json` | HTTP 404 |
| `https://pypi.org/pypi/partitionguard/json` | HTTP 404 |
| GitHub repository search `partitionguard in:name` | `total_count: 0` |

These are availability observations, not a reservation, legal clearance, or a
claim that publishing is authorized. Package and repository creation are future
release actions. The executable and Python import name are `partitionguard`.
The package is installed locally with `pip install .`; `pip install partitionguard`
is an eventual public-release goal, not a currently verified installation path.
