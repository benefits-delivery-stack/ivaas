---
status: "accepted"
deciders: CDS-IVAAS
---

# Use FakeCloud for local and CI S3 emulation

## Context and Problem Statement

The DPW VMI document-delivery workflow needs an S3 endpoint in local development and CI. Testing involves putting
an object into an inbox bucket, which the delivery worker copies server-side into a destination bucket.

Which tool provides local and CI AWS S3 emulation?

### Decision Drivers

* The DPW VMI [document delivery flow](../../income-verification-api/packages/adapters/dpw-vmi/docs/data-flow.md) relies
  on an AWS S3 integration.
* The Django app, the delivery worker and the webhook test utility share one external S3 endpoint, similar to real,
  deployed configuration.
* Code should use plain [boto3](https://aws.amazon.com/sdk-for-python/) with an endpoint override and credentials
  provided via environment variables, and path-style addressing when endpoint override is set.
* Assumed: Two buckets, with `PutObject` into the IVaaS inbox and a server-side `CopyObject` into the destination
  program inbox.
* Room to emulate other AWS services later from the same Docker Compose service.

## Decision Outcome

Use FakeCloud for local and CI S3 emulation. It supports the required S3 operations (with optional persistent storage
for local development), runs as a single Compose service, and offers broad AWS support for future AWS emulation needs.

### Consequences

* FakeCloud becomes the de facto tool for AWS emulation.
* Tests do not depend on persisted emulator state. FakeCloud runs ephemerally in CI, therefore tests need to recreate
  the required buckets and objects during setup.
* Application code needs only use [boto3](https://aws.amazon.com/sdk-for-python/) and an endpoint setting, so replacing
  the emulator in the future is mainly a configuration change.
* Similarly, the application code is otherwise unaware whether it is interacting with a real AWS
  service or FakeCloud.

## Options Considered

### [FakeCloud](https://fakecloud.dev/)

An AWS emulator available as a Docker image or a standalone binary, with
[Docker Compose installation](https://fakecloud.dev/docs/getting-started/) and an endpoint for its
implemented services.

* `+` easy setup with Docker Compose
* `+` broad AWS-service and cross-service coverage that IVaaS could reuse later from the
  same Compose service
* `+` its current [S3 service documentation](https://fakecloud.dev/docs/services/s3/) covers `PutObject` and `CopyObject`
  that the VMI adapter will use.
* `+` offers "persistent" storage mode for local development over multiple restarts
* `-` younger project than the other alternatives

### [Moto server](https://docs.getmoto.org/en/latest/docs/server_mode.html)

An HTTP service with an official Docker image and an endpoint override.

* `+` easy setup with Docker Compose
* `+` [broad AWS-service and cross-service coverage](https://docs.getmoto.org/en/latest/docs/services/index.html) that
  IVaaS could reuse later from the same Compose service
* `-` keeps state in memory only

### LocalStack

A broad AWS emulator.

* `+` covers many AWS services
* `--` the current container requires an account and auth token
