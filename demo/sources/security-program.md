# Meridian Robotics — Information Security Program

## Overview

The security program uses layered controls: identity, network segmentation, encryption, and
monitoring. Security controls are reviewed annually by an independent assessor, and the findings
are reported to the audit committee.

## Incident Response

SRE-X9 directs responders to isolate the regional queue as the first containment step for a
suspected data-path compromise. Critical incidents escalate after fifteen minutes without an
acknowledged responder. Incident INC-4402 affected the billing queue and was resolved by
applying SRE-X9 and reprocessing the held messages.

## Backups and Recovery

Backups are tested quarterly by restoring a sample workload into an isolated environment and
verifying data integrity against checksums.

## Vendor Management

Vendors are reviewed annually for security posture and contractual compliance. Contract Cedar is
governed by Retention-9, which sets the minimum data-retention terms every vendor agreement must
carry.

## Programs and Ownership

Operations owns Project Atlas, the warehouse automation rollout. The Platform Team manages
Product Beacon, the internal telemetry service. Finance owns Project Delta, the billing
modernization effort. Regulation SEC-8 governs Product Ember because it processes financial
disclosures. Security Engineering owns the gateway service that enforces authentication for every
external request.

## Billing Platform Migration

The migration deadline is September 30. The migration has three stages: dual-write, backfill
verification, and cutover. Project Delta tracks each stage against the deadline.
