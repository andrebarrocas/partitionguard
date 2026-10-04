# AWS planning inputs

The optional `aws` extra provides **read-only, experimental** S3 inventory and
Glue schema inspection. It cannot execute a migration, run Athena queries, modify
Glue, operate Redshift, or launch EMR. Delta is the first tested ecosystem
execution adapter; this AWS subset supplies planning context only.

```sh
python -m pip install '.[aws]'
partitionguard aws-inspect logistics shipment_events \
  --bucket example-data-bucket --prefix shipment_events/event_date=2026-01-01/ \
  --max-objects 1000 --max-bytes 1000000000 --max-pages 100 --region eu-west-1
```

Use your ordinary AWS credential provider chain. No credentials are saved in the
plan output. This command makes billable AWS API calls when run with live access.
Required read permissions are scoped `glue:GetTable` and `s3:ListBucket` for the
chosen table/bucket/prefix. It does not download object contents.

The Glue table location must contain the requested prefix. Root bucket inventory
is refused; an explicit directory prefix, finite object limit and finite byte
budget are required. Pagination also has a finite page cap (default 100, maximum 1,000), including empty pages. Exceeding a cap fails instead of returning a seemingly
complete partial inventory. Unsupported Glue physical types fail normalization.
The normalized catalog contract is marked unverified: catalog metadata does not
prove keys, nullability, physical rows, or quality.

Listed bytes are the sum of returned object sizes, not an estimate of migrated
row bytes. A paginated listing is not an atomic snapshot. ETags are recorded as
object metadata and **are not treated as content checksums**. Listings can change
while pages are read; use these results for review, never as execution approval.

Tests use `botocore.stub.Stubber` to verify actual request shapes, pagination,
limits, schema normalization and errors. No live AWS account was used to validate
this release. A live integration gate and storage-specific consistency protocol
are required before adding AWS execution.

Primary API references: [S3 list_objects_v2](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/list_objects_v2.html),
[Glue get_table](https://docs.aws.amazon.com/boto3/latest/reference/services/glue/client/get_table.html).
