# V2 Cutover Runbook

Last updated: 2026-05-20

## Preconditions

1. Branch includes all V2 MVP slices.
2. Focused V2 test suites pass.
3. Operator account can access plugin workflow pages.

## Validation Steps

1. Seed architecture only
   `python manage.py mpf_seed_v2 --architecture-only`

2. Stamp mini fabric through workflow
   `Plugins -> Multiplanar Fabrics -> Stamp Template -> Execute`

3. Confirm stamp result summary
   Verify stamp run link, failure count, and managed object counts are shown.

4. Validate path resolution
   Open `Path Query`, select fabric, resolve source lane to destination lane.

5. Confirm source anchoring
   Open stamped `FabricNode` and `Endpoint` details and confirm `Source` fields.

6. Confirm cable independence
   Verify no NetBox cable rows were introduced for modeled-fabric semantics.

## Rollback

1. Disable plugin in NetBox plugin configuration and restart NetBox.
2. Restore previous plugin commit/tag.
3. Re-enable and restart.

No V1-to-V2 data migration is required for rollback because MVP does not overwrite V1 data model artifacts.

## Post-Cutover Monitoring

1. Review new `StampRun` entries for failures.
2. Spot-check path queries across stamped fabrics.
3. Track operator-reported UX gaps for post-MVP backlog.

