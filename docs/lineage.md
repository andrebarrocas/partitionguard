# Importing observed lineage

`partitionguard lineage-import events.json --mapping names.json --output lineage.json`
imports completed OpenLineage RunEvent observations offline. Inputs and outputs
connect through an observed transformation node; cycles are safe to traverse.
Provenance records the producer, event timestamp, run identity and event digest.
Only the information present in the file is used. It is not automatic lineage
discovery, and it cannot establish that all consumers have been found.

The optional mapping JSON maps qualified source identities to the product names
used in your contracts. Inspect the imported nodes before adding their
`dependencies` list to the namespaced `partitionguard` ODCS custom property.
The importer never edits a contract or authorizes a migration. Duplicate
observations are deduplicated into edges while provenance is retained.

Source mapping keys use `namespace/name`, for example
`{"warehouse/shipment_events": "shipment_events"}`. Every observation must supply
nonempty namespace/name pairs, a run ID, a producer, and an ISO event timestamp
with a timezone. These are import preconditions, not full OpenLineage schema
validation. The provider accepts at most 10,000 events and 100,000 total dataset
observations/nodes; the CLI also caps the event file size.

If distinct namespace/name pairs produce the same joined identity, the import
fails instead of merging them. Dataset aliases also cannot collide with the
`job:` transformation identity. Resolve identity mapping explicitly before
combining such sources; provenance does not authorize guessing their meaning.

The built-in importer and all planning commands are read-only. DataHub and
OpenMetadata are future providers through the same explicitly selected plugin
boundary, not bundled catalog clients. See the
[OpenLineage object model](https://openlineage.io/docs/spec/object-model/).
