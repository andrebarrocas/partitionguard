"""AWS request/response contract tests using botocore Stubber, not live AWS tests."""

import pytest

from partitionguard.aws import AWSInventory

boto3 = pytest.importorskip("boto3")
Stubber = pytest.importorskip("botocore.stub").Stubber


@pytest.fixture
def aws():
    kwargs = {
        "region_name": "eu-west-1",
        "aws_access_key_id": "testing",
        "aws_secret_access_key": "testing",
    }
    return AWSInventory(boto3.client("s3", **kwargs), boto3.client("glue", **kwargs))


def test_inventory_paginated_bound_and_measured_bytes(aws):
    with Stubber(aws.s3) as stub:
        stub.add_response(
            "list_objects_v2",
            {
                "Contents": [{"Key": "data/day=1/a", "Size": 10}],
                "IsTruncated": True,
                "NextContinuationToken": "two",
            },
            {"Bucket": "bucket", "Prefix": "data/", "MaxKeys": 4},
        )
        stub.add_response(
            "list_objects_v2",
            {"Contents": [{"Key": "data/day=2/b", "Size": 20}], "IsTruncated": False},
            {"Bucket": "bucket", "Prefix": "data/", "MaxKeys": 3, "ContinuationToken": "two"},
        )
        result = aws.objects("bucket", "data/", max_objects=3)
        assert result["listed_bytes"] == 30 and result["object_count"] == 2
        assert not result["snapshot_consistent"] and not result["etag_is_content_checksum"]
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("prefix", ["", "/", "data"])
def test_root_or_ambiguous_prefix_refused_before_api(aws, prefix):
    with Stubber(aws.s3):
        with pytest.raises(ValueError):
            aws.objects("bucket", prefix)


def test_cap_exceeded_no_partial_success(aws):
    with Stubber(aws.s3) as stub:
        stub.add_response(
            "list_objects_v2", {"Contents": [{"Key": "data/a", "Size": 11}], "IsTruncated": False}
        )
        with pytest.raises(ValueError, match="cap"):
            aws.objects("bucket", "data/", max_bytes=10)


def table():
    return {
        "Name": "events",
        "TableType": "EXTERNAL_TABLE",
        "VersionId": "7",
        "Owner": "data",
        "StorageDescriptor": {
            "Columns": [{"Name": "id", "Type": "bigint"}],
            "Location": "s3://bucket/data/",
        },
        "PartitionKeys": [{"Name": "day", "Type": "date"}],
    }


def test_glue_normalization_remains_unverified(aws):
    with Stubber(aws.glue) as stub:
        stub.add_response("get_table", {"Table": table()}, {"DatabaseName": "db", "Name": "events"})
        c = aws.contract("db", "events")
        assert c.version == "7" and c.partition_by == ("day",) and c.unsupported


def test_inventory_must_match_catalog_location(aws):
    with Stubber(aws.glue) as glue, Stubber(aws.s3):
        glue.add_response("get_table", {"Table": table()})
        with pytest.raises(ValueError, match="within"):
            aws.inspect("db", "events", "different", "data/")


def test_nested_glue_type_rejected(aws):
    t = table()
    t["StorageDescriptor"]["Columns"][0]["Type"] = "struct<x:string>"
    with Stubber(aws.glue) as stub:
        stub.add_response("get_table", {"Table": t})
        with pytest.raises(ValueError, match="Unsupported"):
            aws.contract("db", "events")


@pytest.mark.parametrize(
    "limits",
    [
        {"max_objects": 1.5},
        {"max_objects": True},
        {"max_bytes": float("inf")},
        {"max_bytes": True},
        {"max_pages": 0},
        {"max_pages": 1001},
    ],
)
def test_caps_fail_before_any_api_request(aws, limits):
    with Stubber(aws.s3), Stubber(aws.glue):
        with pytest.raises(ValueError, match="caps"):
            aws.inspect("db", "events", "bucket", "data/", **limits)


def test_empty_truncated_pages_cannot_bypass_workload_cap(aws):
    with Stubber(aws.s3) as stub:
        for token in ("page2", "page3"):
            stub.add_response(
                "list_objects_v2",
                {"Contents": [], "IsTruncated": True, "NextContinuationToken": token},
            )
        with pytest.raises(ValueError, match="page cap"):
            aws.objects("bucket", "data/", max_pages=2)
        stub.assert_no_pending_responses()


def test_duplicate_objects_across_pages_are_not_double_counted(aws):
    with Stubber(aws.s3) as stub:
        stub.add_response(
            "list_objects_v2",
            {
                "Contents": [{"Key": "data/a", "Size": 1}],
                "IsTruncated": True,
                "NextContinuationToken": "next",
            },
        )
        stub.add_response(
            "list_objects_v2", {"Contents": [{"Key": "data/a", "Size": 1}], "IsTruncated": False}
        )
        with pytest.raises(ValueError, match="Inconsistent"):
            aws.objects("bucket", "data/")
