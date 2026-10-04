"""Read-only AWS planning inputs. No AWS mutation or migration execution claims."""

from __future__ import annotations

from datetime import datetime, timezone

from .models import Column, Contract, digest, identifier


class AWSInventory:
    API_VERSION = 1

    def __init__(self, s3, glue):
        self.s3, self.glue = s3, glue

    @classmethod
    def connect(cls, region=None):
        import boto3
        from botocore.config import Config

        config = Config(
            connect_timeout=5, read_timeout=20, retries={"max_attempts": 2, "mode": "standard"}
        )
        return cls(
            boto3.client("s3", region_name=region, config=config),
            boto3.client("glue", region_name=region, config=config),
        )

    @staticmethod
    def _validate_bounds(bucket, prefix, max_objects, max_bytes, max_pages):
        if (
            not isinstance(bucket, str)
            or not bucket
            or not isinstance(prefix, str)
            or not prefix
            or prefix == "/"
            or not prefix.endswith("/")
        ):
            raise ValueError(
                "Use a nonempty directory prefix ending in /; root bucket scans are refused"
            )
        if any(type(value) is not int for value in (max_objects, max_bytes, max_pages)) or not (
            1 <= max_objects <= 100000 and max_bytes >= 1 and 1 <= max_pages <= 1000
        ):
            raise ValueError(
                "Positive integer object/byte/page caps required; at most 100000 objects and 1000 pages"
            )

    def objects(
        self, bucket: str, prefix: str, max_objects=1000, max_bytes=10_000_000_000, max_pages=100
    ):
        self._validate_bounds(bucket, prefix, max_objects, max_bytes, max_pages)
        objects, token, seen, object_keys, size, pages = [], None, set(), set(), 0, 0
        while True:
            if pages >= max_pages:
                raise ValueError("Inventory exceeds approved page cap; narrow the prefix")
            kwargs = {
                "Bucket": bucket,
                "Prefix": prefix,
                "MaxKeys": min(1000, max_objects - len(objects) + 1),
            }
            if token:
                kwargs["ContinuationToken"] = token
            result = self.s3.list_objects_v2(**kwargs)
            pages += 1
            for item in result.get("Contents", []):
                if not item["Key"].startswith(prefix) or item["Key"] in object_keys:
                    raise ValueError("Inconsistent paginated inventory; refuse partial estimate")
                object_keys.add(item["Key"])
                size += item["Size"]
                objects.append(
                    {"key": item["Key"], "bytes": item["Size"], "etag": item.get("ETag")}
                )
                if len(objects) > max_objects or size > max_bytes:
                    raise ValueError(
                        "Inventory exceeds approved object or byte cap; narrow the prefix"
                    )
            if not result.get("IsTruncated"):
                break
            token = result.get("NextContinuationToken")
            if not token or token in seen:
                raise ValueError("Invalid or repeated pagination token")
            seen.add(token)
        objects.sort(key=lambda x: x["key"])
        return {
            "bucket": bucket,
            "prefix": prefix,
            "object_count": len(objects),
            "listed_bytes": size,
            "objects": objects,
            "inventory_sha256": digest(objects),
            "pages_read": pages,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "snapshot_consistent": False,
            "etag_is_content_checksum": False,
        }

    def contract(self, database: str, table: str, catalog_id=None):
        identifier(database)
        identifier(table)
        kwargs = {"DatabaseName": database, "Name": table}
        if catalog_id:
            kwargs["CatalogId"] = catalog_id
        info = self.glue.get_table(**kwargs)["Table"]
        if info.get("TableType", "EXTERNAL_TABLE") != "EXTERNAL_TABLE":
            raise ValueError(
                "Only external Glue tables supported; views and governed tables need dedicated adapters"
            )
        sd = info.get("StorageDescriptor") or {}
        partitions = info.get("PartitionKeys", [])
        fields = sd.get("Columns", []) + partitions
        normalized = Contract(
            product=f"{database}.{table}",
            table=f"{database}.{table}",
            version=str(info.get("VersionId", "unversioned")),
            owner=info.get("Owner") or "unspecified",
            columns=tuple(Column(name=f["Name"], data_type=f["Type"]) for f in fields),
            partition_by=tuple(f["Name"] for f in partitions),
            metadata={
                "source": "aws-glue",
                "catalog_id": catalog_id or info.get("CatalogId"),
                "location": sd.get("Location"),
                "table_version": info.get("VersionId"),
            },
            unsupported=(
                "Catalog schema is descriptive; data quality, nullability, keys and live data require operator review",
            ),
        )
        return normalized

    def inspect(
        self,
        database,
        table,
        bucket,
        prefix,
        max_objects=1000,
        max_bytes=10_000_000_000,
        catalog_id=None,
        max_pages=100,
    ):
        self._validate_bounds(bucket, prefix, max_objects, max_bytes, max_pages)
        contract = self.contract(database, table, catalog_id)
        location = contract.metadata.get("location")
        requested = f"s3://{bucket}/{prefix}"
        if (
            not isinstance(location, str)
            or not location.startswith("s3://")
            or not requested.startswith(location.rstrip("/") + "/")
        ):
            raise ValueError("Requested inventory prefix must be within the Glue table S3 location")
        return {
            "contract": contract.model_dump(mode="json"),
            "inventory": self.objects(bucket, prefix, max_objects, max_bytes, max_pages),
            "execution_supported": False,
        }
