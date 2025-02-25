# Limits

This section describes current limitations of the Amazon S3 Find and Forget
solution. We are actively working on adding additional features and supporting
more data formats. For feature requests, please open an issue on our [Issue
Tracker].

## Supported Data Formats

The following data formats are supported:

#### Apache Parquet

|                                       |                                                                                                                                                                              |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Compression on Read                   | Snappy, Brotli, Gzip, uncompressed                                                                                                                                           |
| Compression on Write                  | Snappy                                                                                                                                                                       |
| Supported Types for Column Identifier | bigint, char, double, float, int, smallint, string, tinyint, varchar. Nested types (types whose parent is a struct, map, array) are only supported for **struct** type (\*). |
| Notes                                 | (\*) When using a type nested in a struct as column identifier with Apache Parquet files, use the Athena's version 2 engine. For more information, see [Managing Workgroups] |

#### JSON

|                                       |                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Compression on Read                   | Gzip, uncompressed (\*\*)                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Compression on Write                  | Gzip, uncompressed (\*\*)                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Supported Types for Column Identifier | number, string. Nested types (types whose parent is a object, array) are only supported for **object** type.                                                                                                                                                                                                                                                                                                                                            |
| Notes                                 | (\*\*) The compression type is determined from the file extension. If no file extension is present the solution treats the data as uncompressed. If the data is compressed make sure the file name includes the compression extension, such as `gz`.<br><br>When using OpenX JSON SerDe, `ignore.malformed.json` cannot be `TRUE`, `dots.in.keys` cannot be `TRUE`, and column mappings are not supported. For more information, see [OpenX JSON SerDe] |

## Supported Query Providers

The following data catalog provider and query executor combinations are
supported:

| Catalog Provider | Query Executor |
| ---------------- | -------------- |
| AWS Glue         | Amazon Athena  |

## Concurrency Limits

| Catalog Provider        | Query Executor            |
| ----------------------- | ------------------------- |
| Max Concurrent Jobs     | 1                         |
| Max Athena Concurrency  | See account service quota |
| Max Fargate Concurrency | See account service quota |

## Other Limitations

- Only buckets with versioning set to **Enabled** are supported
- Decompressed individual object size must be less than the Fargate task memory
  limit (`DeletionTaskMemory`) specified when launching the stack
- S3 Objects using the `GLACIER` or `DEEP_ARCHIVE` storage classes are not
  supported and will be ignored
- The bucket targeted by a data mapper must be in the same region as the Amazon
  S3 Find and Forget deployment
- Client-side encrypted S3 Objects are supported only when a symmetric customer
  master key (CMK) is stored in AWS Key Management Service (AWS KMS) and
  encrypted using one of the [AWS supported SDKs].
- If the bucket targeted by a data mapper belongs to an account other than the
  account that the Amazon S3 Find and Forget Solution is deployed in, only
  SSE-KMS with a customer master key (CMK) may be used for encryption
- To avoid race conditions when objects are processed by the solution,
  manipulating existing data lake objects must not occur while a Job is running.
  The solution will attempt to verify object integrity between read and write
  operations and attempt to rollback any changes if an inconsistency is
  detected. If the rollback fails, you will need to manually reconcile the
  object versions to avoid data inconsistency or loss
- We recommend that you avoid running a Deletion Job in parallel to a workload
  that reads from the data lake unless it has been designed to handle temporary
  inconsistencies between objects
- Buckets with MFA Delete enabled are not supported

## Service Quotas

If you wish to increase the number of concurrent queries that can be by Athena
and therefore speed up the Find phase, you will need to request a Service Quota
increase for Athena. For more, information consult the [Athena Service Quotas]
page. Similarly, to increase the number of concurrent Fargate tasks and
therefore speed up the Forget phase, consult the [Fargate Service Quotas] page.
When configuring the solution, you should not set an `AthenaConcurrencyLimit` or
`DeletionTasksMaxNumber` greater than the respective Service Quote for your
account.

Amazon S3 Find and Forget is also bound by any other service quotas which apply
to the underlying AWS services that it leverages. For more information, consult
the AWS docs for [Service Quotas] and the relevant Service Quota page for the
service in question:

- [SQS Service Quotas]
- [Step Functions Service Quotas]
- [DynamoDB Service Quotas]

[aws supported sdks]:
  https://docs.aws.amazon.com/AmazonS3/latest/userguide/UsingClientSideEncryption.html
[issue tracker]: https://github.com/awslabs/amazon-s3-find-and-forget/issues
[service quotas]:
  https://docs.aws.amazon.com/general/latest/gr/aws_service_limits.html
[service quotas]:
  https://docs.aws.amazon.com/general/latest/gr/aws_service_limits.html
[athena service quotas]:
  https://docs.aws.amazon.com/athena/latest/ug/service-limits.html
[fargate service quotas]:
  https://docs.aws.amazon.com/AmazonECS/latest/developerguide/service-quotas.html
[step functions service quotas]:
  https://docs.aws.amazon.com/step-functions/latest/dg/limits.html
[sqs service quotas]:
  https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-quotas.html
[dynamodb service quotas]:
  https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Limits.html
[deletion job]: ARCHITECTURE.md#deletion-jobs
[deletion queue]: ARCHITECTURE.md#deletion-queue
[managing workgroups]:
  https://docs.aws.amazon.com/athena/latest/ug/workgroups-create-update-delete.html
[openx json serde]:
  https://docs.aws.amazon.com/athena/latest/ug/json-serde.html#openx-json-serde
